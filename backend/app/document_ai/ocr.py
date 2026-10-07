"""OCR abstraction. Engines are pluggable; with OCR_ENGINE=none, scanned pages are
explicitly flagged (SCANNED_PAGE_NO_OCR) rather than silently yielding empty text."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

from app.config import get_settings
from app.document_ai.image_quality import preprocess_for_ocr


class OCRUnavailable(RuntimeError):
    pass


@dataclass
class OCRWord:
    text: str
    bbox: tuple[float, float, float, float]  # pixel coordinates of the input image
    conf: float  # 0-100


@dataclass
class OCRResult:
    text: str
    words: list[OCRWord] = field(default_factory=list)
    mean_confidence: float = 0.0
    engine: str = ""
    lines: list[dict] = field(default_factory=list)  # [{"text", "bbox", "conf"}] in input-image pixels


def words_from_tesseract_data(data: dict, transform=None) -> tuple[list[OCRWord], list[dict]]:
    """Convert pytesseract.image_to_data(DICT) output into words + lines in ORIGINAL image coords.

    `transform` is the 2x3 matrix original->processed returned by preprocess_for_ocr; boxes are
    mapped back through its inverse so provenance points at the original page.
    """
    from app.document_ai.image_quality import map_box_to_original

    words: list[OCRWord] = []
    grouped: dict[tuple[int, int, int], list[OCRWord]] = {}
    for i, txt in enumerate(data.get("text", [])):
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0 or not str(txt).strip():
            continue
        x, y, w, h = (float(data[k][i]) for k in ("left", "top", "width", "height"))
        box = (x, y, x + w, y + h)
        if transform is not None:
            box = map_box_to_original(box, transform)
        word = OCRWord(str(txt).strip(), box, conf)
        words.append(word)
        key = (int(data.get("block_num", [0] * (i + 1))[i]), int(data.get("par_num", [0] * (i + 1))[i]),
               int(data.get("line_num", [0] * (i + 1))[i]))
        grouped.setdefault(key, []).append(word)
    lines = []
    for ws in grouped.values():
        ws.sort(key=lambda w: w.bbox[0])
        lines.append({
            "text": " ".join(w.text for w in ws),
            "bbox": [min(w.bbox[0] for w in ws), min(w.bbox[1] for w in ws),
                     max(w.bbox[2] for w in ws), max(w.bbox[3] for w in ws)],
            "conf": round(sum(w.conf for w in ws) / len(ws), 1),
        })
    lines.sort(key=lambda ln: (ln["bbox"][1], ln["bbox"][0]))
    return words, lines


class OCREngine(ABC):
    name = "abstract"

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def recognize(self, image: np.ndarray) -> OCRResult: ...


class NullOCREngine(OCREngine):
    name = "none"

    def available(self) -> bool:
        return False

    def recognize(self, image: np.ndarray) -> OCRResult:
        raise OCRUnavailable("No OCR engine configured (OCR_ENGINE=none)")


class TesseractOCREngine(OCREngine):
    name = "tesseract"

    def __init__(self, languages: str = "eng", cmd: str = ""):
        self.languages = languages
        self._ok: bool | None = None
        try:
            import pytesseract

            if cmd:
                pytesseract.pytesseract.tesseract_cmd = cmd
            self._pt = pytesseract
        except ImportError:
            self._pt = None

    def available(self) -> bool:
        if self._ok is None:
            if self._pt is None:
                self._ok = False
            else:
                try:
                    self._pt.get_tesseract_version()
                    self._ok = True
                except Exception:
                    self._ok = False
        return self._ok

    def recognize(self, image: np.ndarray) -> OCRResult:
        if not self.available():
            raise OCRUnavailable("Tesseract binary not found")
        prepared, transform = preprocess_for_ocr(image)
        # psm 3 = automatic page segmentation (forms, statements, multi-column pages)
        data = self._pt.image_to_data(
            prepared,
            lang=self.languages,
            config="--psm 3 -c preserve_interword_spaces=1",
            output_type=self._pt.Output.DICT,
        )
        words, lines = words_from_tesseract_data(data, transform)
        mean = float(np.mean([w.conf for w in words])) if words else 0.0
        text = "\n".join(ln["text"] for ln in lines)
        return OCRResult(text=text, words=words, mean_confidence=mean, engine=self.name, lines=lines)


class PaddleOCREngine(OCREngine):
    """Placeholder adapter: wire up paddleocr here when it is adopted."""

    name = "paddleocr"

    def available(self) -> bool:
        try:
            import paddleocr  # noqa: F401
        except ImportError:
            return False
        return False  # not implemented yet

    def recognize(self, image: np.ndarray) -> OCRResult:
        raise OCRUnavailable("PaddleOCR adapter not implemented in this prototype")


_engine: OCREngine | None = None


def get_ocr_engine() -> OCREngine:
    global _engine
    if _engine is None:
        s = get_settings()
        name = s.ocr_engine.lower().strip()
        if name == "tesseract":
            _engine = TesseractOCREngine(s.ocr_languages, s.tesseract_cmd)
        elif name == "paddleocr":
            _engine = PaddleOCREngine()
        else:
            _engine = NullOCREngine()
    return _engine


def set_ocr_engine(engine: OCREngine | None) -> None:
    """Override the OCR engine (tests / alternative engines)."""
    global _engine
    _engine = engine
