"""Raw text extraction (the raw layer).

Per PDF page we *inspect* the native text layer before deciding how to read it:

  native chars < MIN_TEXT_CHARS                  -> OCR     (NO_TEXT_LAYER: scanned page)
  garbage ratio high / few alphanumerics         -> OCR     (UNREADABLE_TEXT_LAYER: broken font encoding)
  page mostly covered by images + thin text      -> HYBRID  (native words kept, image content OCR'd,
                                                             e.g. scanned page with a stamp/watermark)
  otherwise                                      -> TEXT_LAYER

The decision, its inputs, layout blocks, OCR output and any error are kept per page. OCR is only
run when needed; when it is needed but unavailable or fails, the page is flagged explicitly
(SCANNED_PAGE_NO_OCR / OCR_FAILED) - extraction never silently returns an empty page.
Images are OCR'd; spreadsheets are read cell by cell.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from app.config import get_settings
from app.document_ai.image_quality import assess_image_quality, decode_image_file
from app.document_ai.ocr import OCRUnavailable, get_ocr_engine
from app.document_ai.types import Line, ParsedDocument, ParsedPage, RawTable, Word
from app.ingestion.file_validator import CSV, IMAGE_MIMES, PDF, TIFF, XLSX
from app.models.enums import TextSource

MIN_TEXT_CHARS = 20  # fewer native chars => scanned page
GARBAGE_RATIO_LIMIT = 0.2  # share of replacement/(cid:x)/control chars
MIN_ALNUM_RATIO = 0.45  # share of letters/digits among non-space chars
IMAGE_COVERAGE_HYBRID = 0.6  # page area covered by images
THIN_TEXT_WORDS = 40  # "thin" native layer on an image-covered page


def build_lines(words: list[Word], page_number: int) -> list[Line]:
    """Cluster words into visual lines by vertical centre, then sort left->right."""
    if not words:
        return []
    ws = sorted(words, key=lambda w: ((w.y0 + w.y1) / 2, w.x0))
    lines: list[list[Word]] = []
    centres: list[float] = []
    heights: list[float] = []
    for w in ws:
        yc = (w.y0 + w.y1) / 2
        h = w.height
        if lines and abs(yc - centres[-1]) <= 0.5 * max(h, heights[-1]):
            lines[-1].append(w)
            n = len(lines[-1])
            centres[-1] = centres[-1] + (yc - centres[-1]) / n
            heights[-1] = max(heights[-1], h)
        else:
            lines.append([w])
            centres.append(yc)
            heights.append(h)
    return [Line(page_number, sorted(lw, key=lambda w: w.x0)) for lw in lines]


# --------------------------------------------------------------------------- inspection
_CID = re.compile(r"\(cid:\d+\)")


def text_quality(text: str) -> dict[str, Any]:
    stripped = text.strip()
    nonspace = [c for c in stripped if not c.isspace()]
    bad = stripped.count("�") + 5 * len(_CID.findall(stripped)) + sum(
        1 for c in nonspace if ord(c) < 32 or 0xE000 <= ord(c) <= 0xF8FF  # control / private-use glyphs
    )
    alnum = sum(1 for c in nonspace if c.isalnum())
    return {
        "native_chars": len(stripped),
        "garbage_ratio": round(bad / max(len(nonspace), 1), 3),
        "alnum_ratio": round(alnum / max(len(nonspace), 1), 3),
    }


def _image_coverage(pg) -> float:
    area = max(pg.rect.width * pg.rect.height, 1.0)
    covered = 0.0
    try:
        infos = pg.get_image_info()
    except Exception:
        infos = []
    for info in infos:
        x0, y0, x1, y1 = info.get("bbox", (0, 0, 0, 0))
        x0, y0 = max(x0, pg.rect.x0), max(y0, pg.rect.y0)
        x1, y1 = min(x1, pg.rect.x1), min(y1, pg.rect.y1)
        if x1 > x0 and y1 > y0:
            covered += (x1 - x0) * (y1 - y0)
    return round(min(1.0, covered / area), 3)


def decide_page_method(q: dict[str, Any], n_words: int, image_coverage: float) -> tuple[str, str]:
    if q["native_chars"] < MIN_TEXT_CHARS:
        return "OCR", "NO_TEXT_LAYER"
    if q["garbage_ratio"] > GARBAGE_RATIO_LIMIT or q["alnum_ratio"] < MIN_ALNUM_RATIO:
        return "OCR", "UNREADABLE_TEXT_LAYER"
    if image_coverage >= IMAGE_COVERAGE_HYBRID and n_words < THIN_TEXT_WORDS:
        return "HYBRID", "IMAGE_PAGE_WITH_THIN_TEXT_LAYER"
    return "TEXT_LAYER", "NATIVE_TEXT_OK"


# --------------------------------------------------------------------------- OCR
def _overlaps(a: Word, b: Word) -> bool:
    ix = min(a.x1, b.x1) - max(a.x0, b.x0)
    iy = min(a.y1, b.y1) - max(a.y0, b.y0)
    if ix <= 0 or iy <= 0:
        return False
    inter = ix * iy
    return inter / max(min((a.x1 - a.x0) * (a.y1 - a.y0), (b.x1 - b.x0) * (b.y1 - b.y0)), 1e-6) > 0.3


def _run_ocr(img: np.ndarray, page: ParsedPage, scale: float) -> list[Word] | None:
    """OCR a page image. Returns words in page coordinates, or None when OCR could not run
    (the reason is recorded on the page)."""
    engine = get_ocr_engine()
    page.image_quality = assess_image_quality(img)
    if page.image_quality["issues"]:
        page.warn("LOW_IMAGE_QUALITY", f"Page image quality issues: {', '.join(page.image_quality['issues'])}")
    if not engine.available():
        page.warn("SCANNED_PAGE_NO_OCR",
                  "Page content is an image and no OCR engine is available; content not extracted (OCR required)")
        page.text_quality["ocr_status"] = "UNAVAILABLE"
        return None
    try:
        result = engine.recognize(img)
    except OCRUnavailable as exc:
        page.warn("SCANNED_PAGE_NO_OCR", str(exc))
        page.text_quality["ocr_status"] = "UNAVAILABLE"
        return None
    except Exception as exc:  # engine crash: recorded, page flagged - never silent
        page.error("OCR_FAILED", f"{engine.name}: {type(exc).__name__}: {exc}")
        page.warn("OCR_FAILED", "OCR engine failed on this page; content not extracted")
        page.text_quality["ocr_status"] = "FAILED"
        return None
    words = [
        Word(w.text, w.bbox[0] * scale, w.bbox[1] * scale, w.bbox[2] * scale, w.bbox[3] * scale, w.conf)
        for w in result.words
    ]
    page.ocr_confidence = round(result.mean_confidence, 1)
    page.ocr_output = {
        "engine": result.engine or engine.name,
        "mean_confidence": round(result.mean_confidence, 1),
        "scale_to_page": scale,
        "words": [w.as_dict() for w in words],
        "lines": [{**ln, "bbox": [round(v * scale, 2) for v in ln["bbox"]]} for ln in result.lines],
    }
    page.text_quality["ocr_status"] = "OK" if words else "NO_TEXT"
    if not words:
        page.warn("OCR_NO_TEXT", "OCR ran but recognised no text on this page")
    elif result.mean_confidence < 70:
        page.warn("LOW_OCR_CONFIDENCE", f"Mean OCR confidence {result.mean_confidence:.0f}%")
    return words


def _render(pg, dpi: int) -> tuple[np.ndarray, float]:
    import pymupdf as fitz

    zoom = dpi / 72.0
    pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 3:
        img = img[:, :, ::-1].copy()  # RGB -> BGR for OpenCV
    elif pix.n == 1:
        img = img.reshape(pix.height, pix.width).copy()
    return img, 1.0 / zoom


# --------------------------------------------------------------------------- PDF
def _parse_pdf(path: Path) -> ParsedDocument:
    import pymupdf as fitz

    settings = get_settings()
    pages: list[ParsedPage] = []
    with fitz.open(path) as doc:
        if doc.needs_pass:
            doc.authenticate("")
        for i in range(doc.page_count):
            pg = doc.load_page(i)
            raw = pg.get_text("text")
            native_words = [Word(t[4], t[0], t[1], t[2], t[3]) for t in pg.get_text("words") if t[4].strip()]
            q = text_quality(raw)
            coverage = _image_coverage(pg)
            method, reason = decide_page_method(q, len(native_words), coverage)
            page = ParsedPage(
                page_number=i + 1, width=pg.rect.width, height=pg.rect.height,
                text_source=TextSource.TEXT_LAYER, raw_text=raw,
            )
            page.text_quality = {**q, "native_words": len(native_words), "image_coverage": coverage,
                                 "rotation": pg.rotation, "decision": method, "reason": reason}
            if pg.rotation:
                page.warn("ROTATED_PAGE", f"Page rotated {pg.rotation} degrees; verify highlighted positions")
            page.blocks = [
                {"text": b[4].strip(), "bbox": [round(v, 2) for v in b[:4]], "type": "image" if b[6] == 1 else "text"}
                for b in pg.get_text("blocks")
            ]

            if method == "TEXT_LAYER":
                page.words = native_words
            else:
                page.is_scanned = True
                img, scale = _render(pg, settings.ocr_dpi)
                ocr_words = _run_ocr(img, page, scale)
                if method == "OCR":
                    if ocr_words is None:
                        page.text_source = TextSource.NONE
                        page.words = []
                        page.raw_text = ""
                        if reason == "UNREADABLE_TEXT_LAYER":
                            page.warn("UNREADABLE_TEXT_LAYER",
                                      "Native text layer is garbled (font encoding); OCR required")
                    else:
                        page.text_source = TextSource.OCR
                        page.words = ocr_words
                else:  # HYBRID: keep native words, add OCR words not already covered by them
                    if ocr_words is None:
                        page.text_source = TextSource.TEXT_LAYER
                        page.words = native_words
                        page.warn("PARTIAL_TEXT_OCR_REQUIRED",
                                  "Only a thin native text layer was read; image content needs OCR")
                    else:
                        extra = [w for w in ocr_words if not any(_overlaps(w, n) for n in native_words)]
                        page.text_source = TextSource.HYBRID
                        page.words = native_words + extra
                        page.text_quality["ocr_words_added"] = len(extra)
                if page.ocr_output:
                    page.blocks += [{"text": ln["text"], "bbox": ln["bbox"], "type": "ocr_line",
                                     "conf": ln.get("conf")} for ln in page.ocr_output.get("lines", [])]
            page.lines = build_lines(page.words, page.page_number)
            if page.text_source in (TextSource.OCR, TextSource.HYBRID):
                page.raw_text = "\n".join(ln.text for ln in page.lines)
            pages.append(page)
    return ParsedDocument(source_kind="pdf", pages=pages)


# --------------------------------------------------------------------------- images
def _parse_image(path: Path, mime: str) -> ParsedDocument:
    import cv2

    frames: list[np.ndarray] = []
    if mime == TIFF:
        ok, mats = cv2.imreadmulti(str(path))
        if ok:
            frames = list(mats)
    if not frames:
        img = decode_image_file(str(path))
        if img is not None:
            frames = [img]
    pages: list[ParsedPage] = []
    for i, img in enumerate(frames):
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        h, w = img.shape[:2]
        page = ParsedPage(page_number=i + 1, width=float(w), height=float(h), text_source=TextSource.OCR,
                          raw_text="", is_scanned=True)
        page.text_quality = {"decision": "OCR", "reason": "IMAGE_FILE"}
        words = _run_ocr(img, page, scale=1.0)
        if words is None:
            page.text_source = TextSource.NONE
        else:
            page.words = words
        page.lines = build_lines(page.words, page.page_number)
        page.raw_text = "\n".join(ln.text for ln in page.lines)
        if page.ocr_output:
            page.blocks = [{"text": ln["text"], "bbox": ln["bbox"], "type": "ocr_line", "conf": ln.get("conf")}
                           for ln in page.ocr_output.get("lines", [])]
        pages.append(page)
    return ParsedDocument(source_kind="image", pages=pages)


# --------------------------------------------------------------------------- spreadsheets
def _cell_str(v: object) -> str | None:
    if v is None:
        return None
    try:
        import pandas as pd

        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "strftime"):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    return s or None


def _parse_spreadsheet(path: Path, mime: str) -> ParsedDocument:
    import pandas as pd

    if mime == XLSX:
        sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    else:
        sheets = {"Sheet1": pd.read_csv(path, header=None, dtype=str, encoding_errors="replace")}
    pages: list[ParsedPage] = []
    tables: list[RawTable] = []
    for idx, (name, df) in enumerate(sheets.items()):
        all_rows = [[_cell_str(v) for v in row] for row in df.itertuples(index=False, name=None)]
        kept = [(r_i, r) for r_i, r in enumerate(all_rows) if any(c is not None for c in r)]
        rows = [r for _, r in kept]
        text_lines = ["  ".join(c for c in r if c) for r in rows]
        page = ParsedPage(page_number=idx + 1, width=None, height=None, text_source=TextSource.SPREADSHEET,
                          raw_text="\n".join(text_lines))
        page.text_quality = {"decision": "SPREADSHEET", "reason": "CELL_VALUES", "sheet": str(name)}
        # Synthetic lines (no geometry); provenance uses the sheet/row reference instead of a bbox.
        page.lines = []
        for (r_i, _), t in zip(kept, text_lines):
            page.lines.append(Line(idx + 1, [], text=t, spans=[], ref={"sheet": str(name), "row": r_i + 1}))
        pages.append(page)
        if rows:
            tables.append(RawTable(page_number=idx + 1, index=len(tables), rows=rows, method="spreadsheet",
                                   title=str(name)))
    return ParsedDocument(source_kind="spreadsheet", pages=pages, sheet_tables=tables)


def parse_document(path: Path, mime: str) -> ParsedDocument:
    if mime == PDF:
        return _parse_pdf(path)
    if mime in IMAGE_MIMES:
        return _parse_image(path, mime)
    if mime in (XLSX, CSV):
        return _parse_spreadsheet(path, mime)
    raise ValueError(f"UNSUPPORTED: content type '{mime}' cannot be parsed")
