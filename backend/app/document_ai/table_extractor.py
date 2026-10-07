"""Table extraction, kept separate from field extraction.

Strategy per page:
  1. Ruled tables via pdfplumber (text-layer PDFs). Cell text is kept verbatim (wrapped cells keep
     their line breaks in the raw rows; parsers join them).
  2. Layout transaction tables: find a header *band* (one or two lines, e.g. "Withdrawal" over
     "Amt (Dr)") whose words form known column phrases; column boundaries are the midpoints
     between header columns; amounts are placed by their right edge, text by its left edge.
     Pages without a header reuse the previous page's columns.
  3. Layout financial statements: rows of "label  amount  amount"; a text cell after an amount
     starts a new row, so T-format (Dr | Cr side by side) statements split into two rows.
  Spreadsheets provide their sheets directly.

Then page segments of the same transaction table are merged into one logical table spanning
pages (repeated header rows are kept, flagged HEADER). Raw cells are never parsed or dropped
here; typing/parsing happens in the extractors.

Header vocabulary is token based (any phrase whose tokens all belong to a column's vocabulary
and that contains one of its key tokens), so "Txn Date", "Transaction Date", "Tran. Dt" all map
to `date` without one regex per bank.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from app.document_ai.normalizers import AMOUNT_IN_TEXT, DATE_IN_TEXT, find_amounts, find_plain_numbers
from app.document_ai.types import BBox, Line, ParsedDocument, ParsedPage, RawTable, Word, union_bbox
from app.models.enums import TableType, TextSource

# --------------------------------------------------------------------------- header vocabulary
_MONEY_EXTRA = {"amt", "amount", "inr", "rs", "in", "₹", "rupees", "(inr)", "(rs)"}
COLUMN_VOCAB: list[tuple[str, set[str], set[str]]] = [
    # canon, key tokens (need one), allowed tokens (all tokens must be in key|allowed)
    ("value_date", {"value"}, {"date", "dt"}),
    ("balance", {"balance", "bal"}, {"closing", "running", "available", "avl", "dr", "cr"} | _MONEY_EXTRA),
    ("drcr", {"type"}, {"dr", "cr", "txn", "d", "c"}),
    ("reference", {"chq", "cheque", "check", "ref", "reference", "instrument", "utr", "inst"},
     {"no", "number", "num", "id", "txn", "transaction", "details"}),
    ("description", {"narration", "description", "particulars", "details", "remarks", "desc", "naration"},
     {"transaction", "txn", "of", "the", "tran"}),
    ("debit", {"debit", "debits", "withdrawal", "withdrawals", "withdrawl", "dr", "payments", "payment"},
     {"paid", "out"} | _MONEY_EXTRA),
    ("credit", {"credit", "credits", "deposit", "deposits", "cr", "receipts", "receipt"},
     {"paid", "in"} | _MONEY_EXTRA),
    ("date", {"date", "dt"}, {"txn", "tran", "trans", "transaction", "posting", "post", "book", "booking", "entry"}),
    ("amount", {"amount", "amt"}, {"txn", "transaction", "inr", "rs", "in", "₹"}),
]
SUFFIX_TOKENS = {"amt", "amount", "no", "number", "inr", "rs", "dr", "cr", "₹", "in", "(inr)", "(rs)"}
HEADER_TOKENS = {t for _, k, a in COLUMN_VOCAB for t in k | a} | SUFFIX_TOKENS


def _tokens(text: str | None) -> list[str]:
    if not text:
        return []
    t = text.lower().replace("₹", " ₹ ")
    t = re.sub(r"[^a-z₹]+", " ", t)
    return [x for x in t.split() if x]


def classify_header_phrase(text: str | None) -> str | None:
    toks = _tokens(text)
    if not toks:
        return None
    ts = set(toks)
    # "Dr/Cr", "Cr/Dr", "Dr Cr" indicator column
    if ts <= {"dr", "cr", "d", "c", "txn"} and {"dr", "cr"} <= ts:
        return "drcr"
    for canon, keys, allowed in COLUMN_VOCAB:
        if ts & keys and ts <= (keys | allowed):
            if canon == "date" and "value" in ts:
                continue
            if canon == "value_date" and not ts & {"date", "dt"}:
                continue
            return canon
    return None


def map_transaction_header(cells: list[str | None]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(cells):
        canon = classify_header_phrase(cell)
        if canon and canon not in mapping:
            mapping[canon] = idx
    return mapping


def is_transaction_mapping(mapping: dict[str, int]) -> bool:
    has_money = "balance" in mapping or ("debit" in mapping and "credit" in mapping) or "amount" in mapping
    return "date" in mapping and has_money and len(mapping) >= 3


@dataclass
class TableAnalysis:
    table_type: TableType
    header_index: int | None
    column_mapping: dict[str, object]


def analyze_table(raw: RawTable, inherited: dict[str, int] | None = None) -> TableAnalysis:
    for i, row in enumerate(raw.rows[:4]):
        mapping = map_transaction_header(row)
        if is_transaction_mapping(mapping):
            return TableAnalysis(TableType.TRANSACTIONS, i, dict(mapping))
    if inherited and raw.n_cols >= max(inherited.values()) + 1 and _looks_like_txn_rows(raw, inherited.get("date", 0)):
        return TableAnalysis(TableType.TRANSACTIONS, None, {**inherited, "inherited": True})
    amount_rows = sum(1 for r in raw.rows if any(_is_amount_cell(c) for c in r[1:]))
    if raw.rows and amount_rows >= max(2, len(raw.rows) // 3):
        amount_cols = sorted({j for r in raw.rows for j, c in enumerate(r) if j > 0 and _is_amount_cell(c)})
        header_idx = None
        for i, r in enumerate(raw.rows[:3]):
            joined = " ".join(c or "" for c in r).lower()
            if ("particular" in joined or re.search(r"20\d{2}", joined)) and not any(_is_amount_cell(c) for c in r[1:]):
                header_idx = i
                break
        return TableAnalysis(TableType.FINANCIAL_STATEMENT, header_idx, {"label": 0, "amounts": amount_cols})
    return TableAnalysis(TableType.GENERIC, None, {})


def _is_amount_cell(c: str | None) -> bool:
    if not c:
        return False
    s = re.sub(r"\s+", " ", c).strip()
    if AMOUNT_IN_TEXT.fullmatch(s):
        return True
    plain = find_plain_numbers(s)
    return bool(plain) and plain[0][0] == s


def _looks_like_txn_rows(raw: RawTable, date_col: int = 0) -> bool:
    dated = sum(1 for r in raw.rows if len(r) > date_col and r[date_col] and DATE_IN_TEXT.search(r[date_col]))
    return dated >= max(1, len(raw.rows) // 3)


# --------------------------------------------------------------------------- pdfplumber
def _pdfplumber_tables(path: Path, page_numbers: list[int]) -> dict[int, list[RawTable]]:
    import pdfplumber

    out: dict[int, list[RawTable]] = {}
    settings = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
    with pdfplumber.open(path) as pdf:
        for pno in page_numbers:
            page = pdf.pages[pno - 1]
            found = []
            for t in page.find_tables(table_settings=settings):
                rows = t.extract()
                if len(rows) < 2 or max(len(r) for r in rows) < 2:
                    continue
                rows = [[(c.strip() if isinstance(c, str) else c) or None for c in r] for r in rows]
                row_boxes: list[BBox | None] = []
                for r in t.rows:
                    try:
                        row_boxes.append(tuple(float(v) for v in r.bbox))  # type: ignore[arg-type]
                    except Exception:
                        row_boxes.append(None)
                if len(row_boxes) != len(rows):
                    row_boxes = [None] * len(rows)
                found.append(RawTable(page_number=pno, index=0, rows=rows, method="pdfplumber_lines",
                                      row_bboxes=row_boxes, bbox=tuple(float(v) for v in t.bbox)))  # type: ignore[arg-type]
            if found:
                out[pno] = found
    return out


# --------------------------------------------------------------------------- layout: header band
@dataclass
class _Column:
    canon: str | None
    label: str
    x0: float
    x1: float


@dataclass
class _Cluster:
    text: str
    x0: float
    x1: float


def _is_numeric_word(text: str) -> bool:
    """Amount-like token (right-aligned in statements). Dates are excluded: they are left-aligned."""
    if DATE_IN_TEXT.fullmatch(text):
        return False
    if not (re.fullmatch(r"[\d,.()₹-]+(?:/-)?(?:dr|cr)?\.?", text.lower()) and any(ch.isdigit() for ch in text)):
        return False
    # money needs grouping/decimals or a long figure; "7788" inside a narration is text, not an amount
    return bool(re.search(r"[,.]", text)) or sum(ch.isdigit() for ch in text) >= 5


def is_header_ish(line: Line) -> bool:
    if not line.words or DATE_IN_TEXT.search(line.text) or find_amounts(line.text):
        return False
    toks = _tokens(line.text)
    return bool(toks) and sum(1 for t in toks if t in HEADER_TOKENS) / len(toks) >= 0.5


def _clusters_for_band(lines: list[Line]) -> list[_Cluster]:
    """Words of the first line are tokens; words of a second line stack onto the token they overlap."""
    clusters = [_Cluster(w.text, w.x0, w.x1) for w in sorted(lines[0].words, key=lambda w: w.x0)]
    for ln in lines[1:]:
        for w in sorted(ln.words, key=lambda w: w.x0):
            best, best_ov = None, 0.0
            for c in clusters:
                ov = min(c.x1, w.x1) - max(c.x0, w.x0)
                if ov > best_ov:
                    best, best_ov = c, ov
            if best is not None:
                best.text += " " + w.text
                best.x0, best.x1 = min(best.x0, w.x0), max(best.x1, w.x1)
            else:
                clusters.append(_Cluster(w.text, w.x0, w.x1))
    return sorted(clusters, key=lambda c: c.x0)


def _match_columns(clusters: list[_Cluster], char_w: float) -> list[_Column]:
    cols: list[_Column] = []
    i, n = 0, len(clusters)
    while i < n:
        hit = None
        for j in range(min(n, i + 4), i, -1):
            # phrase words must be close together (same header cell)
            if any(clusters[k + 1].x0 - clusters[k].x1 > 3 * char_w for k in range(i, j - 1)):
                continue
            phrase = " ".join(c.text for c in clusters[i:j])
            canon = classify_header_phrase(phrase)
            if canon:
                hit = (j, canon, phrase)
                break
        if hit:
            j, canon, phrase = hit
            cols.append(_Column(canon, phrase, clusters[i].x0, clusters[j - 1].x1))
            i = j
            continue
        c = clusters[i]
        toks = _tokens(c.text)
        if cols and toks and set(toks) <= SUFFIX_TOKENS and c.x0 - cols[-1].x1 <= 3 * char_w:
            cols[-1].label += " " + c.text  # "Amt.", "(INR)", "No." belong to the previous column
            cols[-1].x1 = max(cols[-1].x1, c.x1)
        else:
            cols.append(_Column(None, c.text, c.x0, c.x1))  # unknown column: kept so its cells stay put
        i += 1
    # merge adjacent unknown fragments that are part of one header cell (e.g. "S." "No")
    merged: list[_Column] = []
    for col in cols:
        if merged and col.canon is None and merged[-1].canon is None and col.x0 - merged[-1].x1 <= 1.5 * char_w:
            merged[-1].label += " " + col.label
            merged[-1].x1 = col.x1
        else:
            merged.append(col)
    return merged


def _mapping(cols: list[_Column]) -> dict[str, int]:
    m: dict[str, int] = {}
    for i, c in enumerate(cols):
        if c.canon and c.canon not in m:
            m[c.canon] = i
    return m


def detect_header_band(lines: list[Line], i: int) -> tuple[list[_Column], int] | None:
    """Header at line i (optionally plus line i+1). Returns (columns, number of header lines)."""
    line = lines[i]
    if not line.words:
        return None
    char_w = median(w.char_width for w in line.words)
    options: list[tuple[list[_Column], int]] = []
    single = _match_columns(_clusters_for_band([line]), char_w)
    options.append((single, 1))
    if i + 1 < len(lines):
        nxt = lines[i + 1]
        close = nxt.words and (nxt.bbox[1] - line.bbox[3]) < 1.2 * line.height
        if close and is_header_ish(nxt):
            options.append((_match_columns(_clusters_for_band([line, nxt]), char_w), 2))
    valid = [(c, n) for c, n in options if is_transaction_mapping(_mapping(c))]
    if not valid:
        return None
    # prefer the band that maps more columns; a following header-ish line belongs to the header anyway
    valid.sort(key=lambda cn: (len(_mapping(cn[0])), cn[1]), reverse=True)
    return valid[0]


def _boundaries(cols: list[_Column]) -> list[float]:
    return [(cols[k].x1 + cols[k + 1].x0) / 2 for k in range(len(cols) - 1)]


_MONEY_CANONS = {"debit", "credit", "balance", "amount"}
_DRCR_WORD = re.compile(r"^\(?(dr|cr)\)?\.?$", re.IGNORECASE)


def _amount_like(text: str) -> bool:
    """Amount-shaped token, including OCR-damaged ones ('1,2O,000.00'): mostly digits with grouping."""
    if _is_numeric_word(text):
        return True
    if DATE_IN_TEXT.fullmatch(text):
        return False
    core = re.sub(r"[₹()\-/]|(?i:dr|cr)\.?$", "", text)
    digits = sum(ch.isdigit() for ch in core)
    return bool(core) and digits >= 3 and digits / len(core) >= 0.6 and bool(re.search(r"[,.]", core))


def _assign(cols: list[_Column], bounds: list[float], w: Word) -> int:
    if _amount_like(w.text):
        # amounts are right-aligned: place by the right edge between column midpoints
        k = 0
        while k < len(bounds) and w.x1 > bounds[k]:
            k += 1
        return k
    # text / dates are left-aligned: the last non-money column whose header starts at or before the
    # word (a long narration overflowing towards the next header stays in its own column)
    tol = w.char_width
    k = 0
    for i, c in enumerate(cols):
        if c.x0 - tol <= w.x0 and c.canon not in _MONEY_CANONS:
            k = i
    return k


def _line_to_cells(line: Line, cols: list[_Column], bounds: list[float]) -> list[str | None]:
    cells: list[list[str]] = [[] for _ in cols]
    prev: tuple[Word, int] | None = None
    for w in sorted(line.words, key=lambda w: w.x0):
        if prev and _DRCR_WORD.match(w.text) and _is_numeric_word(prev[0].text) and \
                w.x0 - prev[0].x1 < 2 * w.char_width:
            k = prev[1]  # "12,500.00 Cr": the indicator belongs to its amount
        else:
            k = _assign(cols, bounds, w)
        cells[k].append(w.text)
        prev = (w, k)
    return [" ".join(c) if c else None for c in cells]


def _is_table_line(cells: list[str | None], cols: list[_Column]) -> bool:
    for c, col in zip(cells, cols):
        if not c:
            continue
        if col.canon in ("date", "value_date") and DATE_IN_TEXT.search(c):
            return True
        if col.canon in ("debit", "credit", "balance", "amount") and (
                AMOUNT_IN_TEXT.search(c) or re.search(r"\d", c)):
            return True
    return False


def _layout_transaction_table(
    page: ParsedPage, inherited: list[_Column] | None
) -> tuple[RawTable | None, list[_Column] | None]:
    lines = page.lines
    start = None
    cols: list[_Column] | None = None
    n_header = 0
    for i in range(len(lines)):
        band = detect_header_band(lines, i)
        if band:
            cols, n_header = band
            start = i
            break
    if cols is not None and start is not None:
        body_start = start + n_header
        method = "layout_header"
    elif inherited:
        cols = inherited
        body_start = 0
        method = "layout_continuation"
    else:
        return None, None
    bounds = _boundaries(cols)

    rows: list[list[str | None]] = []
    boxes: list[BBox | None] = []
    hints: list[str | None] = []
    flags: list[bool] = []
    i = body_start
    while i < len(lines):
        band = detect_header_band(lines, i)
        if band:  # repeated header inside the table (same page)
            for k in range(band[1]):
                rows.append(_line_to_cells(lines[i + k], cols, bounds))
                boxes.append(lines[i + k].bbox)
                hints.append("HEADER")
                flags.append(False)
            i += band[1]
            continue
        cells = _line_to_cells(lines[i], cols, bounds)
        rows.append(cells)
        boxes.append(lines[i].bbox)
        hints.append(None)
        flags.append(_is_table_line(cells, cols))
        i += 1
    if not any(flags):
        return None, cols
    first = flags.index(True) if method == "layout_continuation" else 0
    last = len(flags) - 1 - flags[::-1].index(True)
    # keep wrapped narration lines that follow the last dated/amount row (e.g. at the page bottom):
    # same row pitch, text only in non-numeric columns. Footers sit further away and are excluded.
    tops = [b[1] for b, fl in zip(boxes, flags) if fl and b]
    pitch = median([b - a for a, b in zip(tops, tops[1:]) if b > a] or [20.0])
    text_cols = {k for k, c in enumerate(cols) if c.canon in (None, "description", "reference")}
    while last + 1 < len(rows) and hints[last + 1] is None and boxes[last + 1] and boxes[last]:
        cells = rows[last + 1]
        gap = boxes[last + 1][1] - boxes[last][1]
        if gap > 1.25 * pitch or any(c for k, c in enumerate(cells) if c and k not in text_cols):
            break
        last += 1
    rows, boxes, hints = rows[first: last + 1], boxes[first: last + 1], hints[first: last + 1]
    header_rows: list[list[str | None]] = []
    if method == "layout_header":
        header_cells = [c.label for c in cols]
        rows.insert(0, header_cells)  # type: ignore[arg-type]
        hband = [lines[start + k].bbox for k in range(n_header)]  # type: ignore[operator]
        boxes.insert(0, union_bbox(hband))
        hints.insert(0, "HEADER")
        header_rows = [header_cells]  # type: ignore[list-item]
    table = RawTable(
        page_number=page.page_number, index=0, rows=rows, method=method, row_bboxes=boxes, row_hints=hints,
        bbox=union_bbox(boxes), header_rows=header_rows,
        column_bboxes=[(c.x0, 0.0, c.x1, 0.0) for c in cols],
        meta={"columns": [{"canon": c.canon, "label": c.label, "x0": round(c.x0, 1), "x1": round(c.x1, 1)}
                          for c in cols]},
    )
    return table, cols


# --------------------------------------------------------------------------- layout: financial statements
_FY_HEADER = re.compile(r"(20\d{2}\s*[-–]\s*\d{2,4}|31[./-]0?3[./-]20\d{2}|march\s*,?\s*20\d{2}|20\d{2})", re.I)


def _is_amount_segment(text: str) -> bool:
    t = text.strip()
    if AMOUNT_IN_TEXT.fullmatch(t):
        return True
    plain = find_plain_numbers(t)
    return bool(plain) and plain[0][0] == t


def _split_groups(line: Line) -> list[tuple[str, list[str], BBox | None]]:
    """[(label, [amounts...], bbox)] - a text cell after an amount cell starts a new group (T-format)."""
    groups: list[tuple[list[str], list[str], list[BBox | None]]] = []
    for seg in line.segments():
        text = seg.text.strip()
        # a segment can hold "label 1,23,000" when the gap is narrow: split trailing amounts off
        amts = find_amounts(text)
        parts: list[tuple[str, bool]] = []
        if _is_amount_segment(text):
            parts = [(text, True)]
        elif amts:
            cut = amts[0][1]
            if text[:cut].strip():
                parts.append((text[:cut].strip(), False))
            parts += [(a[0], True) for a in amts]
        else:
            parts = [(text, False)]
        for ptxt, is_amt in parts:
            if not is_amt and re.fullmatch(r"\d{1,2}[a-z]?", ptxt):
                continue  # note number column ("1", "12a") - not a label, not an amount
            if not groups or (not is_amt and groups[-1][1]):
                groups.append(([], [], []))
            (groups[-1][1] if is_amt else groups[-1][0]).append(ptxt)
            groups[-1][2].append(seg.bbox)
    return [(" ".join(lbl).strip(" .:-|"), am, union_bbox(bx)) for lbl, am, bx in groups]


def _layout_financial_table(page: ParsedPage) -> RawTable | None:
    rows: list[list[str | None]] = []
    boxes: list[BBox | None] = []
    header: tuple[list[str | None], BBox | None] | None = None
    amount_lines = 0
    for line in page.lines:
        text = line.text.strip()
        groups = _split_groups(line) if line.words else []
        has_amounts = any(g[1] for g in groups)
        if has_amounts:
            amount_lines += 1
            for label, amts, bbox in groups:
                if label or amts:
                    rows.append([label or None, *amts])
                    boxes.append(bbox)
        elif not rows and ("particular" in text.lower() or len(_FY_HEADER.findall(text)) >= 2):
            header = ([s.text for s in line.segments()], line.bbox)
        elif rows and len(text) > 2:
            rows.append([text])  # section headings (e.g. "EXPENSES") kept as label-only rows
            boxes.append(line.bbox)
    if amount_lines < 4:
        return None
    while rows and len(rows[-1]) == 1:  # trailing prose after the statement is not table content
        rows.pop()
        boxes.pop()
    if header:
        rows.insert(0, header[0])
        boxes.insert(0, header[1])
    return RawTable(page_number=page.page_number, index=0, rows=rows, method="layout_amount_lines",
                    row_bboxes=boxes, bbox=union_bbox(boxes))


# --------------------------------------------------------------------------- multi-page reconstruction
def merge_continuations(tables: list[RawTable]) -> tuple[list[RawTable], list[str]]:
    """Merge consecutive page segments of the same transaction table into one logical table."""
    warnings: list[str] = []
    out: list[RawTable] = []
    for t in tables:
        prev = out[-1] if out else None
        if prev is not None:
            pa, ta = analyze_table(prev), analyze_table(t, _int_mapping(analyze_table(prev)))
            prev_end = prev.page_end or prev.page_number
            if (pa.table_type == ta.table_type == TableType.TRANSACTIONS
                    and t.page_number in (prev_end, prev_end + 1)):
                if _canon_order(pa) == _canon_order(ta) and prev.n_cols == t.n_cols:
                    hints = list(t.row_hints)
                    if ta.header_index is not None:
                        hints[ta.header_index] = "HEADER"
                    prev.rows += t.rows
                    prev.row_pages += t.row_pages
                    prev.row_bboxes += t.row_bboxes
                    prev.row_hints += hints
                    prev.page_end = t.page_number
                    prev.meta.setdefault("segments", [{"page": prev.page_number, "method": prev.method,
                                                       "rows": len(prev.rows) - len(t.rows)}])
                    prev.meta["segments"].append({"page": t.page_number, "method": t.method, "rows": len(t.rows)})
                    prev.bbox = prev.bbox or t.bbox
                    continue
                warnings.append(f"TABLE_CONTINUATION_COLUMNS_DIFFER: table on page {t.page_number} "
                                "looks like a continuation but its columns differ; kept separate")
        out.append(t)
    for t in out:
        if t.meta.get("segments"):
            t.method = "merged:" + "+".join(sorted({s["method"] for s in t.meta["segments"]}))
    return out, warnings


def _int_mapping(a: TableAnalysis) -> dict[str, int] | None:
    m = {k: v for k, v in a.column_mapping.items() if isinstance(v, int) and not isinstance(v, bool)}
    return m or None


def _canon_order(a: TableAnalysis) -> list[str]:
    m = _int_mapping(a) or {}
    return [k for k, _ in sorted(m.items(), key=lambda kv: kv[1])]


# --------------------------------------------------------------------------- entry point
def extract_tables(path: Path, parsed: ParsedDocument) -> tuple[list[RawTable], list[str]]:
    """Returns (tables, warnings). Pages are processed in order so headers carry over."""
    warnings: list[str] = []
    if parsed.source_kind == "spreadsheet":
        return list(parsed.sheet_tables), warnings

    ruled: dict[int, list[RawTable]] = {}
    if parsed.source_kind == "pdf":
        text_pages = [p.page_number for p in parsed.pages if p.text_source == TextSource.TEXT_LAYER]
        try:
            ruled = _pdfplumber_tables(path, text_pages)
        except Exception as exc:  # recorded, then fall back to layout
            warnings.append(f"PDFPLUMBER_FAILED: {type(exc).__name__}: {exc}; using layout fallback")

    tables: list[RawTable] = []
    inherited_cols: list[_Column] | None = None
    for page in parsed.pages:
        if page.text_source == TextSource.NONE:
            warnings.append(f"PAGE_{page.page_number}_NO_TEXT: tables on this page could not be extracted (OCR required)")
            continue
        page_tables = ruled.get(page.page_number, [])
        has_txn = False
        for t in page_tables:
            if analyze_table(t).table_type == TableType.TRANSACTIONS or (
                    tables and analyze_table(tables[-1]).table_type == TableType.TRANSACTIONS
                    and analyze_table(t, _int_mapping(analyze_table(tables[-1]))).table_type == TableType.TRANSACTIONS):
                has_txn = True
            tables.append(t)
        if has_txn:
            continue
        layout, cols = _layout_transaction_table(page, inherited_cols)
        if layout is not None:
            inherited_cols = cols
            tables.append(layout)
            continue
        if cols is not None:
            inherited_cols = cols
        if not page_tables:
            fin = _layout_financial_table(page)
            if fin is not None:
                tables.append(fin)
    tables, merge_warnings = merge_continuations(tables)
    warnings += merge_warnings
    for i, t in enumerate(tables):
        t.index = i
    return tables, warnings
