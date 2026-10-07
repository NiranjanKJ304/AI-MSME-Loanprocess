"""Common extraction interface.

Every extractor returns an ExtractionResult with:
    document_type, fields, tables, pages, warnings, confidence, status (+ structured schema dump)

Pipeline per field:  locate (layout / tables / pattern)  ->  normalise (keeps raw)  ->  score
                     ->  choose between candidates  ->  extraction status
Validation happens later (app/validation) and never mutates extracted values.

Every field - found or not - is a FieldCandidate carrying raw value, normalised value, confidence
(with its factors), page, bounding box, source text, position relative to its label, text source
(native / OCR) and extraction method. Missing fields are NOT_FOUND, or OCR_REQUIRED when part of
the document could not be read. Values are never invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import BaseModel, Field, create_model

from app.config import get_settings
from app.document_ai import confidence as C
from app.document_ai import normalizers as N
from app.document_ai.layout import (
    BILINGUAL_PREFIX,
    ENUM_PREFIX,
    LabelVocabulary,
    find_label_hits,
    locate_values,
)
from app.document_ai.table_extractor import TableAnalysis, analyze_table
from app.document_ai.types import BBox, Line, ParsedDocument, ParsedPage, RawTable
from app.llm import LLMResponseError, LLMUnavailable, get_llm_provider
from app.models.enums import (
    DocumentType,
    ExtractionMethod,
    ExtractionStatus,
    RowKind,
    RowStatus,
    TableType,
    TextSource,
)

LLM_FIELD_CONFIDENCE = 0.6  # always below FIELD_CONFIDENCE_THRESHOLD => human review
GENERIC_FORM_LABELS = [
    r"name", r"address", r"date", r"status", r"pan", r"gstin", r"place", r"signature", r"mobile(?:\s+no\.?)?",
    r"e-?mail(?:\s+id)?", r"phone", r"state", r"district", r"pin\s*code", r"father'?s\s+name",
]


# --------------------------------------------------------------------------- data classes
@dataclass
class FieldSpec:
    name: str
    value_type: str = "string"  # string|name|text|date|amount|percent|pan|gstin|ifsc|udyam|cin|code|fy|ay|enum
    labels: list[str] = field(default_factory=list)
    required: bool = False
    pattern: str | None = None
    unlabeled: bool = False
    multiline: bool = False
    non_negative: bool = False
    description: str = ""


@dataclass
class FieldCandidate:
    name: str
    value_type: str
    raw_value: str | None = None
    normalized_value: str | None = None
    typed_value: Any = None
    confidence: float = 0.0
    page: int | None = None
    bbox: BBox | None = None
    page_size: tuple[float | None, float | None] | None = None
    snippet: str | None = None
    method: ExtractionMethod = ExtractionMethod.NONE
    label: str | None = None
    table_index: int | None = None
    row_index: int | None = None
    warnings: list[str] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    components: list[dict[str, Any]] | None = None
    is_missing: bool = False
    required: bool = False
    non_negative: bool = False
    priority: int = 0  # lower = preferred source (e.g. more specific label)
    extraction_status: ExtractionStatus = ExtractionStatus.EXTRACTED
    position: str | None = None  # RIGHT / INLINE / BELOW / ABOVE / TABLE_* / PATTERN / ...
    text_source: str | None = None  # TEXT_LAYER / OCR / HYBRID / SPREADSHEET
    confidence_factors: dict[str, Any] | None = None
    label_bbox: BBox | None = None
    ref: dict[str, Any] | None = None  # spreadsheet sheet/row reference
    ambiguous: bool = False

    def source_location(self) -> dict[str, Any] | None:
        loc: dict[str, Any] = {}
        if self.bbox is not None:
            loc["bbox"] = [round(v, 2) for v in self.bbox]
        if self.page_size and self.bbox is not None:
            loc["page_width"], loc["page_height"] = self.page_size
        if self.label_bbox is not None:
            loc["label_bbox"] = [round(v, 2) for v in self.label_bbox]
        if self.components:
            loc["components"] = self.components
        if self.ref:
            loc["cell_ref"] = self.ref
        if self.position:
            loc["position"] = self.position
        if self.text_source:
            loc["text_source"] = self.text_source
        if self.confidence_factors:
            loc["confidence_factors"] = self.confidence_factors
        return loc or None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.raw_value,
            "normalized_value": self.normalized_value,
            "confidence": round(self.confidence, 3),
            "page": self.page,
            "source_location": self.source_location(),
            "source_text": self.snippet,
            "method": self.method.value,
            "extraction_status": self.extraction_status.value,
            "is_missing": self.is_missing,
            "warnings": self.warnings,
        }


@dataclass
class RowResult:
    row_index: int
    page_number: int
    bbox: BBox | None
    raw_cells: list[str | None]
    kind: RowKind
    status: RowStatus
    parsed: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    confidence: float | None = None


@dataclass
class TableResult:
    raw: RawTable
    analysis: TableAnalysis
    rows: list[RowResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float | None = None

    @property
    def table_type(self) -> TableType:
        return self.analysis.table_type


@dataclass
class ExtractionResult:
    document_type: DocumentType
    fields: list[FieldCandidate]
    tables: list[TableResult]
    pages: list[dict[str, Any]]
    warnings: list[str]
    confidence: float
    structured: dict[str, Any] = field(default_factory=dict)
    status: str = "OK"  # OK | PARTIAL | OCR_REQUIRED | UNSUPPORTED | EXTRACTION_FAILED


# --------------------------------------------------------------------------- normalisation
_TYPE_PATTERNS = {
    "pan": N.PAN_RE,
    "gstin": N.GSTIN_RE,
    "ifsc": N.IFSC_RE,
    "udyam": N.UDYAM_RE,
    "cin": N.CIN_RE,
}


@dataclass
class NormResult:
    normalized: str | None
    typed: Any
    matched: str | None  # exact substring of the raw text that was interpreted
    error: str | None = None  # value present but could not be read as its type
    notes: list[str] = field(default_factory=list)
    repaired: bool = False
    unformatted: bool = False

    @property
    def ok(self) -> bool:
        return self.typed is not None


def normalize(value_type: str, raw: str | None, *, allow_repair: bool = False) -> NormResult:
    """Interpret `raw` as `value_type` without destroying it (raw is kept by the caller)."""
    if raw is None or not str(raw).strip():
        return NormResult(None, None, None)
    raw_s = str(raw).strip()
    if value_type in ("string", "name", "text", "enum"):
        v = N.normalize_whitespace(raw_s.strip(" :-|,"))
        if value_type == "name" and v:
            v = v.upper()
        return NormResult(v, v, raw_s)
    if value_type == "code":
        v = N.normalize_id(raw_s)
        return NormResult(v, v, raw_s)
    if value_type in _TYPE_PATTERNS:
        compact = raw_s.upper().replace(" ", "")
        m = _TYPE_PATTERNS[value_type].search(compact)
        if m:
            # map back onto the raw text for exact provenance when possible
            idx = raw_s.upper().find(m.group(1))
            matched = raw_s[idx: idx + len(m.group(1))] if idx >= 0 else raw_s
            return NormResult(m.group(1), m.group(1), matched)
        if allow_repair:
            fixed = N.repair_identifier(raw_s, value_type)
            if fixed:
                return NormResult(fixed, fixed, raw_s, notes=[f"OCR_CHARS_CORRECTED: '{raw_s}' read as '{fixed}'"],
                                  repaired=True)
        return NormResult(None, None, raw_s, error=f"FORMAT_MISMATCH: '{raw_s}' is not a valid {value_type.upper()}")
    if value_type == "date":
        m = N.DATE_IN_TEXT.search(raw_s)
        token = m.group(0) if m else raw_s
        d = N.parse_date(token) or N.parse_date(raw_s)
        if d is None:
            return NormResult(None, None, raw_s, error=f"UNPARSEABLE_DATE: '{raw_s}'")
        return NormResult(d.isoformat(), d, token)
    if value_type == "amount":
        found = N.find_amounts(raw_s)
        unformatted = False
        if found:
            token = found[0][0]
        else:
            plain = N.find_plain_numbers(raw_s)
            if not plain:
                whole = N.parse_amount(raw_s)
                if whole is None:
                    return NormResult(None, None, raw_s, error=f"UNPARSEABLE_AMOUNT: '{raw_s}'")
                token = raw_s
            else:
                token = plain[-1][0]  # e.g. ITR-V "Gross Total Income  1  1250000": the figure is last
            unformatted = True
        a = N.parse_amount(token)
        if a is None:
            return NormResult(None, None, raw_s, error=f"UNPARSEABLE_AMOUNT: '{raw_s}'")
        notes = []
        if N.amount_drcr(token) == "DR" and a > 0:
            a = -a
            notes.append("DR_SIGN_APPLIED")
        if unformatted:
            notes.append(f"UNFORMATTED_NUMBER: '{token}' has no digit grouping")
        return NormResult(N.decimal_str(a), a, token, notes=notes, unformatted=unformatted)
    if value_type == "percent":
        p = N.parse_percent(raw_s)
        if p is None:
            return NormResult(None, None, raw_s, error=f"UNPARSEABLE_PERCENT: '{raw_s}'")
        return NormResult(str(p), p, raw_s)
    if value_type in ("fy", "ay"):
        fy = N.parse_fy(raw_s)
        if not fy:
            kind = "PERIOD" if value_type == "fy" else "ASSESSMENT_YEAR"
            return NormResult(None, None, raw_s, error=f"UNPARSEABLE_{kind}: '{raw_s}'")
        return NormResult(fy, fy, raw_s)
    v = N.normalize_whitespace(raw_s)
    return NormResult(v, raw_s, raw_s)


def normalize_value(value_type: str, raw: str | None) -> tuple[str | None, Any, str | None]:
    """Backward-compatible wrapper: (normalized_str, typed_value, error)."""
    r = normalize(value_type, raw)
    return r.normalized, r.typed, r.error


# --------------------------------------------------------------------------- context
class ExtractionContext:
    def __init__(self, parsed: ParsedDocument, tables: list[RawTable], filename: str = ""):
        self.parsed = parsed
        self.raw_tables = tables
        self.filename = filename
        self.settings = get_settings()
        self.vocab = LabelVocabulary(GENERIC_FORM_LABELS)

    def set_vocabulary(self, label_regexes: list[str]) -> None:
        self.vocab = LabelVocabulary(label_regexes + GENERIC_FORM_LABELS)

    # -- document-level text availability
    @property
    def text_incomplete(self) -> bool:
        """True when some content could not be read (scanned page without OCR, OCR failure)."""
        for p in self.parsed.pages:
            if p.text_source == TextSource.NONE:
                return True
            if any(w["code"] in ("PARTIAL_TEXT_OCR_REQUIRED", "OCR_FAILED") for w in p.warnings):
                return True
        return False

    # -- provenance helpers
    def page_factor(self, page_no: int | None) -> float:
        page = self.parsed.page(page_no) if page_no else None
        return C.source_factor(page, None)[0]

    def page_size(self, page_no: int | None) -> tuple[float | None, float | None] | None:
        page = self.parsed.page(page_no) if page_no else None
        return (page.width, page.height) if page else None

    def lines(self) -> list[tuple[ParsedPage, int, Line]]:
        return [(p, i, ln) for p in self.parsed.pages for i, ln in enumerate(p.lines)]

    def candidate(
        self,
        spec: FieldSpec,
        raw: str | None,
        *,
        page: ParsedPage | None,
        bbox: BBox | None,
        snippet: str | None,
        method: ExtractionMethod,
        base_conf: float | None = None,
        position: str | None = None,
        label: str | None = None,
        table_index: int | None = None,
        row_index: int | None = None,
        priority: int = 0,
        word_conf: float | None = None,
        label_bbox: BBox | None = None,
        ref: dict[str, Any] | None = None,
    ) -> FieldCandidate:
        ocr_text = page is not None and page.text_source in (TextSource.OCR, TextSource.HYBRID)
        norm = normalize(spec.value_type, raw, allow_repair=ocr_text)
        typed_check: bool | None = None if spec.value_type in ("string", "name", "text", "enum", "code") else norm.ok
        conf, factors = C.score(
            position or "CUSTOM", page=page, word_conf=word_conf, type_ok=typed_check,
            unformatted=norm.unformatted, repaired=norm.repaired, label_priority=min(priority, 5),
            base_override=base_conf,
        )
        warnings = list(norm.notes)
        if norm.error:
            warnings.append(norm.error)
        page_no = page.page_number if page else None
        if ocr_text:
            warnings.append(f"OCR_SOURCE: page {page_no} text from {page.text_source.value}"
                            f" ({(word_conf if word_conf is not None else page.ocr_confidence) or 0:.0f}%)")
        raw_value = (norm.matched or raw).strip() if isinstance(raw, str) else raw
        return FieldCandidate(
            name=spec.name,
            value_type=spec.value_type,
            raw_value=raw_value,
            normalized_value=norm.normalized,
            typed_value=norm.typed,
            confidence=conf,
            page=page_no,
            bbox=bbox,
            page_size=self.page_size(page_no),
            snippet=(snippet or "")[:300] or None,
            method=method,
            label=label,
            table_index=table_index,
            row_index=row_index,
            warnings=warnings,
            required=spec.required,
            non_negative=spec.non_negative,
            priority=priority,
            position=position,
            text_source=page.text_source.value if page else None,
            confidence_factors=factors,
            label_bbox=label_bbox,
            ref=ref,
            extraction_status=ExtractionStatus.EXTRACTED if norm.ok else ExtractionStatus.EXTRACTION_FAILED,
        )

    def _accept(self, spec: FieldSpec):
        def ok(text: str) -> bool:
            if not text or self.vocab.is_label(text):
                return False
            if spec.pattern and spec.value_type in _TYPE_PATTERNS:
                return bool(re.search(spec.pattern, text.upper().replace(" ", ""))) or bool(
                    N.repair_identifier(text, spec.value_type))
            if spec.pattern:
                flags = re.IGNORECASE if spec.value_type in ("ay", "fy") else 0
                return bool(re.search(spec.pattern, text, flags))
            if spec.value_type in ("string", "name", "text", "enum", "code"):
                return bool(re.search(r"[A-Za-z0-9]", text))
            if spec.value_type == "amount" and re.fullmatch(r"\(?\d{1,3}[a-z]?\)?\.?", text.strip()):
                return False  # bare 1-3 digit integers are row / note / schedule numbers, not amounts
            return normalize(spec.value_type, text).ok
        return ok

    # -- search strategies
    def find_labeled(self, spec: FieldSpec) -> list[FieldCandidate]:
        out: list[FieldCandidate] = []
        accept = self._accept(spec)
        for priority, label in enumerate(spec.labels):
            for page in self.parsed.pages:
                for hit in find_label_hits(page, label, priority):
                    for vc in locate_values(hit, self.vocab, accept, multiline=spec.multiline):
                        raw, start, end = vc.text, vc.start, vc.end
                        if spec.pattern and not spec.multiline:
                            flags = re.IGNORECASE if spec.value_type in ("ay", "fy") else 0
                            pm = re.search(spec.pattern, vc.text, flags)
                            if pm:
                                raw = pm.group(0)
                                off = vc.line.text.find(raw, vc.start)
                                if off >= 0:
                                    start, end = off, off + len(raw)
                        line = vc.line
                        bbox = vc.bbox if spec.multiline else (line.bbox_for_span(start, end) if line.words else None)
                        out.append(self.candidate(
                            spec, raw, page=page, bbox=bbox, snippet=vc.snippet, method=ExtractionMethod.LABEL,
                            position=vc.position, label=hit.label_text, priority=priority,
                            word_conf=line.ocr_conf_for_span(start, end) if line.words else None,
                            label_bbox=hit.bbox, ref=line.ref,
                        ))
            for c in self.find_in_tables(spec, label, priority):
                out.append(c)
        return out

    def find_in_tables(self, spec: FieldSpec, label: str, priority: int) -> list[FieldCandidate]:
        """Label in a table cell -> value in the same cell, the next cell, or the cell below."""
        rx = re.compile(rf"^\s*{ENUM_PREFIX}{BILINGUAL_PREFIX}(?P<label>{label})(?![A-Za-z0-9])\s*[:\-–]?\s*",
                        re.IGNORECASE)
        accept = self._accept(spec)
        out: list[FieldCandidate] = []
        for t in self.raw_tables:
            if t.method.startswith("layout"):
                continue  # layout tables are rebuilt from lines already searched above
            for r_i, row in enumerate(t.rows):
                for c_i, cell in enumerate(row):
                    if not cell:
                        continue
                    text = re.sub(r"\s+", " ", cell)
                    m = rx.match(text)
                    if not m:
                        continue
                    options: list[tuple[str, int, str]] = []
                    rest = text[m.end():].strip()
                    if rest:
                        options.append((rest, r_i, "INLINE"))
                    nxt = next((c for c in row[c_i + 1:] if c and c.strip()), None)
                    if nxt:
                        options.append((re.sub(r"\s+", " ", nxt), r_i, "TABLE_RIGHT"))
                    if r_i + 1 < len(t.rows) and c_i < len(t.rows[r_i + 1]) and t.rows[r_i + 1][c_i]:
                        options.append((re.sub(r"\s+", " ", t.rows[r_i + 1][c_i]), r_i + 1, "TABLE_BELOW"))
                    chosen = next((o for o in options if accept(o[0])), None)
                    if chosen is None:
                        continue
                    value, vr, pos = chosen
                    if spec.pattern and spec.value_type in _TYPE_PATTERNS:
                        pm = re.search(spec.pattern, value)
                        value = pm.group(0) if pm else value
                    page = self.parsed.page(t.row_pages[vr]) if vr < len(t.row_pages) else None
                    out.append(self.candidate(
                        spec, value, page=page, bbox=t.row_bboxes[vr] if vr < len(t.row_bboxes) else None,
                        snippet=" | ".join(c or "" for c in t.rows[vr]), method=ExtractionMethod.TABLE,
                        position=pos, label=m.group("label"), table_index=t.index, row_index=vr,
                        priority=priority,
                        ref={"sheet": t.title, "row": vr + 1, "col": c_i + 1} if t.method == "spreadsheet" else None,
                    ))
        return out

    def find_pattern(self, spec: FieldSpec, base_conf: float | None = None) -> list[FieldCandidate]:
        if not spec.pattern:
            return []
        out: list[FieldCandidate] = []
        rx = re.compile(spec.pattern)
        for page, _, line in self.lines():
            for m in rx.finditer(line.text):
                out.append(self.candidate(
                    spec, m.group(0), page=page, bbox=line.bbox_for_span(m.start(), m.end()) if line.words else None,
                    snippet=line.text, method=ExtractionMethod.REGEX, base_conf=base_conf, position="PATTERN",
                    priority=100, word_conf=line.ocr_conf_for_span(m.start(), m.end()) if line.words else None,
                    ref=line.ref,
                ))
        return out


def choose(spec: FieldSpec, candidates: list[FieldCandidate]) -> FieldCandidate | None:
    """Pick the best candidate; keep every other distinct value as an alternative; mark the
    result AMBIGUOUS when a different value is nearly as well supported."""
    if not candidates:
        return None
    ranked = sorted(
        candidates,
        key=lambda c: (c.normalized_value is None, c.priority, -c.confidence, c.page or 0),
    )
    best = ranked[0]
    seen = {best.normalized_value}
    for c in ranked[1:]:
        if c.normalized_value in seen and c.normalized_value is not None:
            continue
        seen.add(c.normalized_value)
        best.alternatives.append({
            "value": c.raw_value, "normalized_value": c.normalized_value, "page": c.page,
            "confidence": c.confidence, "method": c.method.value, "label": c.label,
            "position": c.position, "bbox": list(c.bbox) if c.bbox else None,
        })
        if (
            c.normalized_value is not None
            and best.normalized_value is not None
            and c.priority == best.priority
            and c.confidence >= best.confidence - 0.1
            and not best.ambiguous
        ):
            best.ambiguous = True
            best.warnings.append(f"CONFLICTING_VALUES: also found '{c.raw_value}' on page {c.page}")
            best.confidence = round(best.confidence * C.AMBIGUITY_FACTOR, 3)
            if best.confidence_factors is not None:
                best.confidence_factors["ambiguity"] = C.AMBIGUITY_FACTOR
                best.confidence_factors["final"] = best.confidence
    return best


def finalize_status(c: FieldCandidate, threshold: float) -> FieldCandidate:
    if c.is_missing:
        return c
    if c.normalized_value is None:
        c.extraction_status = ExtractionStatus.EXTRACTION_FAILED
    elif c.ambiguous:
        c.extraction_status = ExtractionStatus.AMBIGUOUS
    elif c.confidence < threshold:
        c.extraction_status = ExtractionStatus.LOW_CONFIDENCE
    else:
        c.extraction_status = ExtractionStatus.EXTRACTED
    return c


def missing_field(spec: FieldSpec, reason: str = "NOT_FOUND", ocr_required: bool = False) -> FieldCandidate:
    status = ExtractionStatus.OCR_REQUIRED if ocr_required else ExtractionStatus(reason) \
        if reason in ExtractionStatus.__members__ else ExtractionStatus.NOT_FOUND
    message = (
        "OCR_REQUIRED: not found in the readable text; part of the document has no text layer and was not OCR'd"
        if ocr_required else f"{reason}: field not present in document"
    )
    return FieldCandidate(
        name=spec.name, value_type=spec.value_type, is_missing=True, required=spec.required,
        non_negative=spec.non_negative, method=ExtractionMethod.NONE, warnings=[message],
        extraction_status=status,
    )


# --------------------------------------------------------------------------- base extractor
class BaseExtractor:
    document_type: ClassVar[DocumentType] = DocumentType.UNKNOWN
    schema: ClassVar[type[BaseModel] | None] = None
    field_specs: ClassVar[list[FieldSpec]] = []
    llm_fallback_enabled: ClassVar[bool] = True
    llm_max_pages: ClassVar[int | None] = None

    def extract(self, ctx: ExtractionContext, tables: list[TableResult] | None = None) -> ExtractionResult:
        warnings: list[str] = []
        ctx.set_vocabulary([lab for s in self.field_specs for lab in s.labels])
        if tables is None:
            tables = self.process_tables(ctx)
        found: dict[str, FieldCandidate] = {}
        for spec in self.field_specs:
            try:
                cands = ctx.find_labeled(spec) if spec.labels else []
                if spec.unlabeled and not any(c.normalized_value for c in cands):
                    cands += ctx.find_pattern(spec)
            except Exception as exc:  # one broken field must not hide the others - recorded
                warnings.append(f"EXTRACTION_FAILED: {spec.name}: {type(exc).__name__}: {exc}")
                f = missing_field(spec)
                f.extraction_status = ExtractionStatus.EXTRACTION_FAILED
                f.warnings = [f"EXTRACTION_FAILED: {type(exc).__name__}: {exc}"]
                found[spec.name] = f
                continue
            best = choose(spec, cands)
            if best is not None:
                found[spec.name] = best
        self.post_process(ctx, found, tables, warnings)

        missing_specs = [s for s in self.field_specs if s.name not in found]
        if missing_specs and self.llm_fallback_enabled:
            found.update(self.llm_fill(ctx, missing_specs, warnings))

        threshold = ctx.settings.field_confidence_threshold
        incomplete = ctx.text_incomplete
        fields: list[FieldCandidate] = []
        for spec in self.field_specs:
            c = found.get(spec.name)
            fields.append(finalize_status(c, threshold) if c else missing_field(spec, ocr_required=incomplete))
        # extra derived fields not declared as specs are appended as-is
        fields += [finalize_status(c, threshold) for n, c in found.items()
                   if n not in {s.name for s in self.field_specs}]

        present = [f for f in fields if not f.is_missing]
        confidence = round(sum(f.confidence for f in present) / len(present), 3) if present else 0.0
        req = [f for f in fields if f.required]
        if any(f.is_missing for f in req):
            warnings.append("MISSING_REQUIRED_FIELDS: " + ", ".join(f.name for f in req if f.is_missing))
        if incomplete:
            warnings.append("OCR_REQUIRED: some pages have no readable text; missing fields may be on them")
        structured = self.build_structured(fields, tables, warnings)
        if not ctx.parsed.pages or all(p.text_source == TextSource.NONE for p in ctx.parsed.pages):
            status = "OCR_REQUIRED"
        elif incomplete or any(f.is_missing and f.required for f in fields):
            status = "PARTIAL"
        else:
            status = "OK"
        return ExtractionResult(
            document_type=self.document_type,
            fields=fields,
            tables=tables,
            pages=[_page_summary(p) for p in ctx.parsed.pages],
            warnings=warnings,
            confidence=confidence,
            structured=structured,
            status=status,
        )

    # hooks ---------------------------------------------------------------
    def post_process(
        self,
        ctx: ExtractionContext,
        found: dict[str, FieldCandidate],
        tables: list[TableResult],
        warnings: list[str],
    ) -> None:
        return None

    def process_tables(self, ctx: ExtractionContext) -> list[TableResult]:
        return [process_generic_table(t, analyze_table(t)) for t in ctx.raw_tables]

    def build_structured(
        self, fields: list[FieldCandidate], tables: list[TableResult], warnings: list[str]
    ) -> dict[str, Any]:
        if self.schema is None:
            return {}
        data = {f.name: f.typed_value for f in fields if not f.is_missing and f.typed_value is not None}
        data = {k: v for k, v in data.items() if k in self.schema.model_fields}
        try:
            return self.schema.model_validate(data).model_dump(mode="json")
        except Exception as exc:  # never lose data because of a schema error - record it
            warnings.append(f"SCHEMA_VALIDATION_ERROR: {exc}")
            return {k: str(v) for k, v in data.items()}

    # LLM fallback ----------------------------------------------------------
    def llm_fill(
        self, ctx: ExtractionContext, specs: list[FieldSpec], warnings: list[str]
    ) -> dict[str, FieldCandidate]:
        provider = get_llm_provider()
        if not provider.available():
            return {}
        pages = ctx.parsed.pages[: self.llm_max_pages] if self.llm_max_pages else ctx.parsed.pages
        text = "\n".join(f"[page {p.page_number}]\n" + "\n".join(l.text for l in p.lines) for p in pages)
        text = text[: ctx.settings.llm_max_input_chars]
        if not text.strip():
            return {}

        class LLMValue(BaseModel):
            value: str | None = Field(description="Value exactly as printed, or null if absent")
            verbatim_quote: str | None = Field(description="The exact line of text containing the value")

        model = create_model(  # type: ignore[call-overload]
            "LLMFieldExtraction", **{s.name: (LLMValue | None, None) for s in specs}
        )
        system = (
            "You extract fields from Indian MSME loan documents. Return a value ONLY if it is "
            "printed in the text; otherwise return null. Copy values exactly as printed and give "
            "the verbatim line containing it. Never compute, infer, or guess values."
        )
        fields_desc = "\n".join(f"- {s.name} ({s.value_type}) {s.description}" for s in specs)
        try:
            out = provider.complete_json(
                system, f"Fields to extract:\n{fields_desc}\n\nDocument text:\n---\n{text}\n---", model
            )
        except (LLMUnavailable, LLMResponseError) as exc:
            warnings.append(f"LLM_EXTRACTION_FAILED: {exc}")
            return {}

        result: dict[str, FieldCandidate] = {}
        for spec in specs:
            item = getattr(out, spec.name, None)
            if item is None or not item.value:
                continue
            located = _locate_quote(pages, item.verbatim_quote or item.value, item.value)
            if located is None:
                warnings.append(
                    f"LLM_VALUE_REJECTED: {spec.name}='{item.value}' not found verbatim in source text"
                )
                continue
            page, line = located
            c = ctx.candidate(
                spec, item.value, page=page, bbox=line.bbox, snippet=line.text,
                method=ExtractionMethod.LLM, position="LLM", priority=50, ref=line.ref,
            )
            c.warnings.append("LLM_EXTRACTED: verified against source text; requires human review")
            result[spec.name] = c
        return result


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _locate_quote(pages: list[ParsedPage], quote: str, value: str) -> tuple[ParsedPage, Line] | None:
    q, v = _squash(quote), _squash(value)
    if v not in q:
        q = v
    for page in pages:
        for line in page.lines:
            lt = _squash(line.text)
            if v in lt and (q in lt or lt in q):
                return page, line
    return None


def _page_summary(p: ParsedPage) -> dict[str, Any]:
    return {
        "page_number": p.page_number,
        "text_source": p.text_source.value,
        "char_count": len(p.raw_text),
        "is_scanned": p.is_scanned,
        "ocr_confidence": p.ocr_confidence,
        "text_quality": p.text_quality,
        "warnings": p.warnings,
        "errors": p.errors,
    }


# --------------------------------------------------------------------------- generic tables
def process_generic_table(raw: RawTable, analysis: TableAnalysis) -> TableResult:
    """Keep every row. Financial-statement rows get their amounts parsed; anything that
    looks numeric but fails to parse is NEEDS_REVIEW (never dropped)."""
    tr = TableResult(raw=raw, analysis=analysis)
    amount_cols = analysis.column_mapping.get("amounts", []) if analysis.column_mapping else []
    for i, cells in enumerate(raw.rows):
        page = raw.row_pages[i] if i < len(raw.row_pages) else raw.page_number
        bbox = raw.row_bboxes[i] if i < len(raw.row_bboxes) else None
        hint = raw.row_hints[i] if i < len(raw.row_hints) else None
        if (analysis.header_index is not None and i == analysis.header_index) or hint == "HEADER":
            tr.rows.append(RowResult(i, page, bbox, cells, RowKind.HEADER, RowStatus.EXTRACTED))
            continue
        if analysis.table_type == TableType.FINANCIAL_STATEMENT:
            label = cells[0] if cells else None
            amounts: list[str | None] = []
            errors: list[str] = []
            for j, c in enumerate(cells[1:], start=1):
                s = re.sub(r"\s+", " ", (c or "")).strip()
                if not s or s.lower() in {"-", "--", "—", "nil"}:
                    continue
                if N.AMOUNT_IN_TEXT.fullmatch(s) or (N.find_plain_numbers(s) and N.find_plain_numbers(s)[0][0] == s):
                    a = N.parse_amount(s)
                    if a is not None:
                        amounts.append(N.decimal_str(a))
                        continue
                if j in amount_cols:
                    errors.append(f"UNPARSEABLE_AMOUNT col {j}: '{c}'")
            status = RowStatus.NEEDS_REVIEW if errors else RowStatus.EXTRACTED
            tr.rows.append(RowResult(i, page, bbox, cells, RowKind.LINE_ITEM, status,
                                     parsed={"label": label, "amounts": amounts}, errors=errors))
        else:
            tr.rows.append(RowResult(i, page, bbox, cells, RowKind.LINE_ITEM, RowStatus.EXTRACTED))
    return tr


def money(v: Any) -> Decimal | None:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))
