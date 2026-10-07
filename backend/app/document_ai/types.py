"""In-memory representations produced by parsing (raw layer) before persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import TextSource

BBox = tuple[float, float, float, float]


def union_bbox(boxes: list[BBox | None]) -> BBox | None:
    bs = [b for b in boxes if b is not None]
    if not bs:
        return None
    return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float | None = None  # OCR confidence 0-100; None for native text

    @property
    def bbox(self) -> BBox:
        return (self.x0, self.y0, self.x1, self.y1)

    @property
    def height(self) -> float:
        return max(self.y1 - self.y0, 1.0)

    @property
    def char_width(self) -> float:
        return max((self.x1 - self.x0) / max(len(self.text), 1), 1.0)

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"text": self.text, "bbox": [round(v, 2) for v in self.bbox]}
        if self.conf is not None:
            d["conf"] = round(self.conf, 1)
        return d


@dataclass
class Segment:
    """A run of words on one line separated from its neighbours by a wide gap (a 'cell')."""

    text: str
    start: int  # char offsets within Line.text
    end: int
    words: list[Word]
    bbox: BBox | None

    @property
    def x0(self) -> float:
        return self.bbox[0] if self.bbox else 0.0

    @property
    def x1(self) -> float:
        return self.bbox[2] if self.bbox else 0.0


@dataclass
class Line:
    """A visual line: words clustered by baseline, ordered left->right.

    `text` joins words with one space, or two spaces across a wide horizontal gap
    (column boundary) so label/value and amount columns can be split into segments.
    """

    page_number: int
    words: list[Word]
    text: str = ""
    spans: list[tuple[int, int]] = field(default_factory=list)
    ref: dict[str, Any] | None = None  # spreadsheet sheet/row reference (lines without geometry)

    def __post_init__(self) -> None:
        if not self.text:
            self._build()

    def _build(self) -> None:
        parts: list[str] = []
        spans: list[tuple[int, int]] = []
        pos = 0
        prev: Word | None = None
        for w in self.words:
            if prev is not None:
                cw = min(prev.char_width, w.char_width)
                sep = "  " if (w.x0 - prev.x1) > 2.5 * cw else " "
                parts.append(sep)
                pos += len(sep)
            spans.append((pos, pos + len(w.text)))
            parts.append(w.text)
            pos += len(w.text)
            prev = w
        self.text = "".join(parts)
        self.spans = spans

    @property
    def bbox(self) -> BBox | None:
        return union_bbox([w.bbox for w in self.words])

    @property
    def height(self) -> float:
        return max((w.height for w in self.words), default=10.0)

    def words_in_span(self, start: int, end: int) -> list[Word]:
        return [w for w, (s, e) in zip(self.words, self.spans) if s < end and e > start]

    def bbox_for_span(self, start: int, end: int) -> BBox | None:
        return union_bbox([w.bbox for w in self.words_in_span(start, end)]) or self.bbox

    def ocr_conf_for_span(self, start: int, end: int) -> float | None:
        confs = [w.conf for w in self.words_in_span(start, end) if w.conf is not None]
        return sum(confs) / len(confs) if confs else None

    def segments(self) -> list[Segment]:
        out: list[Segment] = []
        for m in re.finditer(r"\S+(?: \S+)*", self.text):
            ws = self.words_in_span(m.start(), m.end())
            out.append(Segment(m.group(0), m.start(), m.end(), ws, union_bbox([w.bbox for w in ws])))
        return out


@dataclass
class ParsedPage:
    page_number: int
    width: float | None
    height: float | None
    text_source: TextSource
    raw_text: str
    words: list[Word] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    is_scanned: bool = False
    ocr_confidence: float | None = None
    image_quality: dict[str, Any] | None = None
    blocks: list[dict[str, Any]] = field(default_factory=list)
    text_quality: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    ocr_output: dict[str, Any] | None = None  # raw OCR artefact (engine, words, mean confidence)

    def warn(self, code: str, message: str) -> None:
        self.warnings.append({"code": code, "message": message})

    def error(self, code: str, message: str) -> None:
        self.errors.append({"code": code, "message": message})

    @property
    def has_text(self) -> bool:
        return self.text_source != TextSource.NONE and bool(self.lines)


@dataclass
class ParsedDocument:
    source_kind: str  # pdf | image | spreadsheet
    pages: list[ParsedPage]
    warnings: list[dict[str, str]] = field(default_factory=list)
    # Spreadsheet sources provide tables directly (sheet -> rows)
    sheet_tables: list[RawTable] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n".join(p.raw_text for p in self.pages)

    def lines_text(self) -> str:
        return "\n".join(line.text for p in self.pages for line in p.lines)

    def page(self, number: int) -> ParsedPage | None:
        for p in self.pages:
            if p.page_number == number:
                return p
        return None

    @property
    def pages_without_text(self) -> list[int]:
        return [p.page_number for p in self.pages if p.text_source == TextSource.NONE]


@dataclass
class RawTable:
    """A table exactly as found. Cells are strings (or None); nothing is parsed yet.

    row_hints marks rows the table extractor already knows are repeated headers
    ("HEADER"), so parsers never treat them as data.
    """

    page_number: int
    index: int
    rows: list[list[str | None]]
    method: str
    row_pages: list[int] = field(default_factory=list)
    row_bboxes: list[BBox | None] = field(default_factory=list)
    row_hints: list[str | None] = field(default_factory=list)
    bbox: BBox | None = None
    title: str | None = None
    page_end: int | None = None
    header_rows: list[list[str | None]] = field(default_factory=list)
    column_bboxes: list[BBox | None] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        n = len(self.rows)
        if not self.row_pages:
            self.row_pages = [self.page_number] * n
        if not self.row_bboxes:
            self.row_bboxes = [None] * n
        if not self.row_hints:
            self.row_hints = [None] * n

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)
