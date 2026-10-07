"""Layout-aware label -> value location.

A label is found at the start of any *segment* of a line (a run of words separated from its
neighbours by a wide gap), optionally preceded by an item number ("4.", "(a)") or a bilingual
prefix ("नाम / Name"). The value is then looked for, in order:

  INLINE  rest of the label's own segment          "GSTIN: 27AAACR5055K1Z5"
  RIGHT   next segment(s) on the same line          "Legal Name        ABC TRADERS"
  BELOW   segment under the label (column-aware)    "Name" / "ABC TRADERS"
  ABOVE   segment over the label (caption forms)    "ABC TRADERS" / "Name of Proprietor"

Later positions are only tried when earlier ones are empty or do not hold a value of the
expected type. Text that itself looks like a label is never taken as a value. Tables are
searched separately by extractor.find_in_tables (label cell -> neighbour / cell below).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from statistics import median

from app.document_ai.types import BBox, Line, ParsedPage, Segment, union_bbox

ENUM_PREFIX = r"(?:\(?[0-9ivxIVX]{1,4}[.)]\s*|\(?[a-zA-Z][.)]\s+)?"
BILINGUAL_PREFIX = r"(?:[^\x00-\x7F][^/:\n]{0,30}/\s*)?"  # e.g. "नाम / ", "जन्म की तारीख / "
SEP = re.compile(r"^\s*(?:[:\-–=|]\s*)+|^\s+")


@dataclass
class LabelHit:
    page: ParsedPage
    line_index: int
    line: Line
    start: int  # label span in line.text (after enum/bilingual prefixes)
    end: int
    label_text: str
    priority: int
    cell_start: int | None = None  # start of the label cell incl. item number / bilingual prefix

    @property
    def bbox(self) -> BBox | None:
        return self.line.bbox_for_span(self.start, self.end) if self.line.words else None

    @property
    def cell_bbox(self) -> BBox | None:
        """Box of the whole label cell - values below/above align with this, not only the English part."""
        if not self.line.words:
            return None
        return self.line.bbox_for_span(self.cell_start if self.cell_start is not None else self.start, self.end)


@dataclass
class ValueCandidate:
    text: str
    position: str
    page: ParsedPage
    line: Line
    start: int
    end: int
    hit: LabelHit | None = None
    extra_lines: list[tuple[Line, int, int]] | None = None  # multi-line continuation pieces

    @property
    def bbox(self) -> BBox | None:
        boxes = [self.line.bbox_for_span(self.start, self.end)] if self.line.words else []
        for ln, s, e in self.extra_lines or []:
            if ln.words:
                boxes.append(ln.bbox_for_span(s, e))
        return union_bbox(boxes)

    @property
    def word_conf(self) -> float | None:
        return self.line.ocr_conf_for_span(self.start, self.end) if self.line.words else None

    @property
    def snippet(self) -> str:
        parts = []
        if self.hit is not None and self.hit.line is not self.line:
            parts.append(self.hit.line.text.strip())
        parts.append(self.line.text.strip())
        parts += [ln.text.strip() for ln, _, _ in self.extra_lines or []]
        return " / ".join(parts)


def _char_width(line: Line) -> float:
    ws = [w.char_width for w in line.words]
    return median(ws) if ws else 5.0


def _segment_starts(line: Line) -> list[int]:
    starts = {s.start for s in line.segments()}
    # labels following an inline separator: "Name: ABC  PAN: X" with narrow gaps
    starts |= {m.end() for m in re.finditer(r"[:|;]\s+", line.text)}
    return sorted(starts)


def find_label_hits(page: ParsedPage, label_regex: str, priority: int) -> list[LabelHit]:
    rx = re.compile(rf"\s*{ENUM_PREFIX}{BILINGUAL_PREFIX}(?P<label>{label_regex})(?![A-Za-z0-9])", re.IGNORECASE)
    # anywhere in the line, but only when explicitly introduced as a label ("... IFSC: KCBL0000212")
    rx_colon = re.compile(rf"(?<![A-Za-z0-9])(?P<label>{label_regex})\s*:", re.IGNORECASE)
    hits = []
    for li, line in enumerate(page.lines):
        found = None
        for st in _segment_starts(line):
            m = rx.match(line.text, st)
            if m:
                found = m
                break
        if found is None:
            found = rx_colon.search(line.text)
        if found is not None:  # one hit per line per label
            cell_start = len(line.text) - len(line.text[found.start():].lstrip())
            hits.append(LabelHit(page, li, line, found.start("label"), found.end("label"), found.group("label"),
                                 priority, cell_start=cell_start))
    return hits


class LabelVocabulary:
    """Recognises text that is itself a label (so it is never taken as a value)."""

    def __init__(self, label_regexes: list[str]):
        joined = "|".join(f"(?:{r})" for r in label_regexes) if label_regexes else r"(?!x)x"
        self._rx = re.compile(rf"^\s*{ENUM_PREFIX}{BILINGUAL_PREFIX}(?:{joined})(?![A-Za-z0-9])", re.IGNORECASE)
        self._inline_rx = re.compile(rf"\s(?={ENUM_PREFIX}(?:{joined})\s*:)", re.IGNORECASE)

    def is_label(self, text: str) -> bool:
        t = text.strip()
        return bool(t) and (t.endswith(":") or bool(self._rx.match(t)))

    def cut_at_inline_label(self, text: str) -> str:
        """'ABC TRADERS PAN: X' -> 'ABC TRADERS' (another known label introduced by ':')."""
        m = self._inline_rx.search(text)
        return text[: m.start()] if m else text


def _right_values(hit: LabelHit, vocab: LabelVocabulary) -> list[ValueCandidate]:
    """INLINE remainder of the label's segment and/or the RIGHT segment(s) after it."""
    line = hit.line
    rest = line.text[hit.end:]
    lead = SEP.match(rest)
    vstart = hit.end + (lead.end() if lead else 0)
    if vstart >= len(line.text):
        return []
    segs = [s for s in line.segments() if s.end > vstart]
    out = []
    if segs and segs[0].start < vstart:  # label segment continues: "GSTIN: 27AB..." / "NIC 5 DIGIT CODE"
        vc = _collect_right(hit, vocab, segs[:1], vstart, "INLINE")
        if vc:
            out.append(vc)
        segs = segs[1:]
    # each following segment is a RIGHT option ("Gross Total Income | 1 | 905000": the row number
    # is not acceptable as an amount, the next cell is) - stop at the next label
    for k in range(min(len(segs), 4)):
        if vocab.is_label(segs[k].text):
            break
        vc = _collect_right(hit, vocab, segs[k:], segs[k].start, "RIGHT")
        if vc:
            out.append(vc)
    return out


def _collect_right(hit: LabelHit, vocab: LabelVocabulary, segs: list[Segment], vstart: int,
                   position: str) -> ValueCandidate | None:
    line = hit.line
    first = segs[0]
    inline = position == "INLINE"
    pieces = [(max(first.start, vstart), first.end)]
    if not inline and vocab.is_label(first.text):
        return None
    cw = _char_width(line)
    prev = first
    for seg in segs[1:]:
        gap = (seg.x0 - prev.x1) if (seg.bbox and prev.bbox) else 1e9
        if gap > 8 * cw or vocab.is_label(seg.text):
            break
        pieces.append((seg.start, seg.end))
        prev = seg
    start, end = pieces[0][0], pieces[-1][1]
    text = vocab.cut_at_inline_label(line.text[start:end]).strip(" :-–|")
    if not text:
        return None
    off = line.text.find(text, start)
    start = off if off >= 0 else start
    return ValueCandidate(text, position, hit.page, line, start, start + len(text), hit)


def _aligned_segment(line: Line, x0: float, x1: float, cw: float, slack_right: float | None = None) -> Segment | None:
    """Segment of `line` in the column [x0, x1]: overlapping it, left-aligned with it, or (when
    slack_right is given) starting just right of the label - an indented value block."""
    best, best_d = None, 1e9
    for seg in line.segments():
        if not seg.bbox:
            continue
        overlap = min(seg.x1, x1) - max(seg.x0, x0)
        d = abs(seg.x0 - x0)
        indented = slack_right is not None and x0 <= seg.x0 <= slack_right
        if overlap > 0 or d <= 3 * cw or indented:
            if d < best_d:
                best, best_d = seg, d
    return best


def _vertical_value(hit: LabelHit, vocab: LabelVocabulary, direction: int, max_lines: int = 2) -> ValueCandidate | None:
    if not hit.line.words:
        # spreadsheet / geometry-less: take the next line as a whole
        idx = hit.line_index + direction
        if 0 <= idx < len(hit.page.lines) and direction > 0:
            ln = hit.page.lines[idx]
            if not vocab.is_label(ln.text):
                return ValueCandidate(ln.text.strip(), "BELOW", hit.page, ln, 0, len(ln.text), hit)
        return None
    lb = hit.cell_bbox
    if lb is None:
        return None
    cw = _char_width(hit.line)
    height = hit.line.height
    col_x0, col_x1 = lb[0], max(lb[2], lb[0] + 25 * cw)
    lines = hit.page.lines
    idx = hit.line_index
    for _ in range(max_lines):
        idx += direction
        if not 0 <= idx < len(lines):
            return None
        ln = lines[idx]
        if not ln.words:
            continue
        lbbox = ln.bbox
        gap = (lbbox[1] - lb[3]) if direction > 0 else (lb[1] - lbbox[3])
        if gap > 2.2 * height:
            return None
        seg = _aligned_segment(ln, col_x0, col_x1, cw, slack_right=lb[2] + 6 * cw)
        if seg is None:
            continue
        if vocab.is_label(seg.text):
            return None  # the next thing in this column is another label: no value here
        text = vocab.cut_at_inline_label(seg.text).strip(" :-–|")
        if text:
            return ValueCandidate(text, "BELOW" if direction > 0 else "ABOVE", hit.page, ln, seg.start,
                                  seg.start + len(text), hit)
    return None


def extend_multiline(vc: ValueCandidate, vocab: LabelVocabulary, max_lines: int = 3) -> ValueCandidate:
    """Append following lines whose text starts in the same column as the value (wrapped addresses)."""
    if not vc.line.words:
        return vc
    vb = vc.line.bbox_for_span(vc.start, vc.end)
    if vb is None:
        return vc
    cw = _char_width(vc.line)
    lines = vc.page.lines
    idx = lines.index(vc.line)
    extra: list[tuple[Line, int, int]] = []
    last_bottom = vb[3]
    for ln in lines[idx + 1: idx + 1 + max_lines]:
        if not ln.words or ln.bbox[1] - last_bottom > 1.6 * vc.line.height:
            break
        seg = _aligned_segment(ln, vb[0], vb[2], cw)
        if seg is None or abs(seg.x0 - vb[0]) > 3 * cw or vocab.is_label(seg.text):
            break
        if re.match(r"^\s*\(?\d{1,2}[.)]\s", seg.text):
            break
        # another label-value pair on that line (label column to the left) ends the block
        left = [s for s in ln.segments() if s.bbox and s.x1 < seg.x0 - cw]
        if any(vocab.is_label(s.text) for s in left):
            break
        extra.append((ln, seg.start, seg.end))
        last_bottom = ln.bbox[3]
    if extra:
        vc.extra_lines = extra
        vc.text = ", ".join([vc.text] + [ln.text[s:e].strip() for ln, s, e in extra])
    return vc


def locate_values(
    hit: LabelHit,
    vocab: LabelVocabulary,
    accept: Callable[[str], bool],
    multiline: bool = False,
) -> list[ValueCandidate]:
    """Cascade INLINE/RIGHT -> BELOW -> ABOVE; stop at the first position holding an acceptable value.

    Returns all positions tried that held *some* text, the accepted one first; when nothing is
    acceptable the RIGHT/INLINE text (if any) is returned so a malformed value is reported, not lost.
    """
    tried: list[ValueCandidate] = []
    for finder in (lambda: _right_values(hit, vocab),
                   lambda: [v for v in [_vertical_value(hit, vocab, +1)] if v],
                   lambda: [v for v in [_vertical_value(hit, vocab, -1)] if v]):
        for vc in finder():
            if multiline:
                vc = extend_multiline(vc, vocab)
            if accept(vc.text):
                return [vc]
            tried.append(vc)
    return tried[:1]
