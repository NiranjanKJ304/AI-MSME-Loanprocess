"""Cross-document reconciliation.

Identity checks : PAN (PAN card <-> GSTIN chars 3-12 <-> ITR), GSTIN (certificate <-> returns),
                  business name (GST <-> bank <-> ITR <-> PAN <-> Udyam <-> registration <-> application)
Financial checks: turnover (GST returns <-> ITR <-> P&L <-> bank credits), period alignment
                  (P&L <-> balance sheet <-> ITR), bank closing balance <-> balance-sheet bank balance.

Variance thresholds are configurable (RECONCILIATION_THRESHOLDS_FILE); there is no single
universal threshold. A difference is reported as INCONSISTENCY - never as fraud.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.document_ai import normalizers as N
from app.models import Application, Document, ExtractedField, ExtractedTableRow
from app.models.enums import CheckStatus, DocumentType, RowKind, RowStatus, Severity
from app.validation.rules import dec

DT = DocumentType

DEFAULT_VARIANCE_THRESHOLDS: dict[str, float] = {
    # relative difference |a-b| / max(|a|,|b|) above which a pair is flagged
    "TURNOVER_ITR_VS_PL": 0.05,
    "TURNOVER_GST_VS_PL": 0.10,
    "TURNOVER_GST_VS_ITR": 0.10,
    "TURNOVER_BANK_VS_PL": 0.25,
    "TURNOVER_BANK_VS_GST": 0.25,
    "BANK_BALANCE_VS_BS": 0.01,
}


def variance_thresholds() -> dict[str, float]:
    th = dict(DEFAULT_VARIANCE_THRESHOLDS)
    path = get_settings().reconciliation_thresholds_file
    if path:
        th.update({k: float(v) for k, v in json.loads(Path(path).read_text(encoding="utf-8")).items()})
    return th


@dataclass
class Fact:
    document: Document
    field: ExtractedField

    @property
    def value(self) -> str | None:
        return self.field.normalized_value

    def source(self, value: Any = None) -> dict[str, Any]:
        return {
            "document_id": str(self.document.id),
            "document_code": self.document.document_code,
            "filename": self.document.filename,
            "document_type": self.document.document_type.value,
            "field": self.field.field_name,
            "field_id": str(self.field.id),
            "value": self.field.raw_value,
            "normalized_value": value if value is not None else self.field.normalized_value,
            "page": self.field.source_page,
            "confidence": self.field.confidence,
        }


@dataclass
class CheckResult:
    check_code: str
    check_group: str
    status: CheckStatus
    severity: Severity
    message: str
    confidence: float | None
    sources: list[dict[str, Any]]
    details: dict[str, Any] = field(default_factory=dict)


class FactIndex:
    def __init__(self, docs: list[Document], fields: list[ExtractedField], rows: list[ExtractedTableRow]):
        self.docs = docs
        self.by_doc: dict[Any, dict[str, ExtractedField]] = {}
        for f in fields:
            if not f.is_missing:
                self.by_doc.setdefault(f.document_id, {})[f.field_name] = f
        self.rows_by_doc: dict[Any, list[ExtractedTableRow]] = {}
        for r in rows:
            self.rows_by_doc.setdefault(r.document_id, []).append(r)

    def facts(self, doc_type: DocumentType, field_name: str) -> list[Fact]:
        out = []
        for d in self.docs:
            if d.document_type == doc_type:
                f = self.by_doc.get(d.id, {}).get(field_name)
                if f is not None and f.normalized_value:
                    out.append(Fact(d, f))
        return out

    def field_of(self, doc: Document, name: str) -> ExtractedField | None:
        return self.by_doc.get(doc.id, {}).get(name)


# --------------------------------------------------------------------------- name matching
def name_similarity(a: str | None, b: str | None) -> float:
    na, nb = N.normalize_name(a), N.normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(N.name_core_tokens(a)), set(N.name_core_tokens(b))
    if not ta or not tb:
        return round(seq, 3)
    jaccard = len(ta & tb) / len(ta | tb)
    contain = 0.95 if (ta <= tb or tb <= ta) else 0.0
    return round(max(seq, jaccard, contain), 3)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


# --------------------------------------------------------------------------- identity checks
def check_pan(ix: FactIndex) -> CheckResult:
    values: list[tuple[str, dict[str, Any], float]] = []
    for f in ix.facts(DT.PAN, "pan") + ix.facts(DT.ITR, "pan"):
        values.append((f.value, f.source(), f.field.confidence))  # type: ignore[arg-type]
    for t in (DT.GST_CERTIFICATE, DT.GST_RETURN):
        for f in ix.facts(t, "gstin"):
            if f.value and len(f.value) == 15:
                values.append((f.value[2:12], f.source(f.value[2:12]) | {"derived": "GSTIN chars 3-12"}, f.field.confidence))
    return _equality_check("PAN_CONSISTENCY", "IDENTITY", "PAN", values)


def check_gstin(ix: FactIndex) -> CheckResult:
    values = [(f.value, f.source(), f.field.confidence) for t in (DT.GST_CERTIFICATE, DT.GST_RETURN)
              for f in ix.facts(t, "gstin")]
    return _equality_check("GSTIN_CONSISTENCY", "IDENTITY", "GSTIN", values)


def _equality_check(code: str, group: str, label: str, values) -> CheckResult:
    sources = [v[1] for v in values]
    distinct = {v[0] for v in values}
    if len(values) < 2:
        return CheckResult(code, group, CheckStatus.INSUFFICIENT_DATA, Severity.INFO,
                           f"{label}: fewer than two documents provide a value to compare", None, sources)
    conf = round(min(v[2] for v in values), 3)
    if len(distinct) == 1:
        return CheckResult(code, group, CheckStatus.PASS, Severity.INFO,
                           f"{label} '{next(iter(distinct))}' consistent across {len(values)} sources", conf, sources)
    return CheckResult(code, group, CheckStatus.INCONSISTENCY, Severity.ERROR,
                       f"{label} differs across documents: {sorted(distinct)}", conf, sources,
                       {"distinct_values": sorted(distinct)})


NAME_SOURCES: list[tuple[DocumentType, str]] = [
    (DT.GST_CERTIFICATE, "legal_name"),
    (DT.BUSINESS_REGISTRATION, "entity_name"),
    (DT.PAN, "name"),
    (DT.ITR, "name"),
    (DT.BANK_STATEMENT, "account_holder"),
    (DT.UDYAM, "enterprise_name"),
    (DT.GST_RETURN, "legal_name"),
]


def check_business_name(ix: FactIndex, application: Application) -> CheckResult:
    threshold = get_settings().name_match_threshold
    facts = [f for t, n in NAME_SOURCES for f in ix.facts(t, n)]
    trade_names = {f.document.id: f.value for f in ix.facts(DT.GST_CERTIFICATE, "trade_name")}
    sources = [f.source() for f in facts]
    declared = {"document_code": "APPLICATION", "filename": None, "document_type": "APPLICATION",
                "field": "business_name", "value": application.business_name,
                "normalized_value": N.normalize_name(application.business_name), "page": None, "confidence": 1.0}
    if not facts:
        return CheckResult("BUSINESS_NAME_CONSISTENCY", "IDENTITY", CheckStatus.INSUFFICIENT_DATA, Severity.INFO,
                           "No business/holder names extracted from documents", None, [declared])
    reference = facts[0]
    comparisons = []
    mismatches = []
    for f in facts[1:]:
        sim = name_similarity(reference.value, f.value)
        alt = trade_names.get(reference.document.id)
        if alt:
            sim = max(sim, name_similarity(alt, f.value))
        comparisons.append({"a": reference.document.document_code, "b": f.document.document_code,
                            "a_value": reference.value, "b_value": f.value, "similarity": sim})
        if sim < threshold:
            mismatches.append(f)
    app_sim = max(name_similarity(application.business_name, f.value) for f in facts)
    comparisons.append({"a": "APPLICATION", "b": "best document match", "a_value": application.business_name,
                        "similarity": app_sim})
    conf_fields = _mean([f.field.confidence for f in facts])
    min_sim = min([c["similarity"] for c in comparisons[:-1]], default=1.0)
    if mismatches:
        names = ", ".join(f"{m.document.document_code}='{m.value}'" for m in mismatches)
        return CheckResult(
            "BUSINESS_NAME_CONSISTENCY", "IDENTITY", CheckStatus.INCONSISTENCY, Severity.ERROR,
            f"Name on {names} does not match '{reference.value}' ({reference.document.document_code}) "
            f"at threshold {threshold:.2f}", round(conf_fields, 3), sources + [declared],
            {"comparisons": comparisons, "threshold": threshold,
             # only the deviating documents are flagged for review, not every document compared
             "flag_document_ids": sorted({str(m.document.id) for m in mismatches})},
        )
    status = CheckStatus.PASS if len(facts) > 1 else CheckStatus.INSUFFICIENT_DATA
    msg = (f"Business name consistent across {len(facts)} document(s)" if len(facts) > 1
           else "Only one document provides a business name")
    if app_sim < threshold:
        msg += f"; declared application name '{application.business_name}' differs (similarity {app_sim:.2f})"
        status = CheckStatus.INCONSISTENCY if status == CheckStatus.PASS else status
    return CheckResult("BUSINESS_NAME_CONSISTENCY", "IDENTITY", status,
                       Severity.INFO if status == CheckStatus.PASS else Severity.WARNING, msg,
                       round(conf_fields * min_sim, 3), sources + [declared],
                       {"comparisons": comparisons, "threshold": threshold})


# --------------------------------------------------------------------------- financial checks
@dataclass
class Figure:
    label: str
    amount: Decimal
    fy: str | None
    sources: list[dict[str, Any]]
    confidence: float
    notes: list[str] = field(default_factory=list)


def _turnover_figures(ix: FactIndex) -> dict[str, list[Figure]]:
    figs: dict[str, list[Figure]] = {"ITR": [], "PL": [], "GST": [], "BANK": []}
    for f in ix.facts(DT.ITR, "turnover"):
        ay = ix.field_of(f.document, "assessment_year")
        fy = N.assessment_year_to_fy(ay.normalized_value) if ay and ay.normalized_value else None
        figs["ITR"].append(Figure("ITR turnover", dec(f.value) or Decimal(0), fy, [f.source()], f.field.confidence))
    for f in ix.facts(DT.PROFIT_LOSS, "revenue"):
        p = ix.field_of(f.document, "period")
        figs["PL"].append(Figure("P&L revenue", dec(f.value) or Decimal(0), p.normalized_value if p else None,
                                 [f.source()], f.field.confidence))
    # GST: annual return if present, else sum of periodic returns per FY (annualised if partial)
    by_fy: dict[str | None, list[Fact]] = {}
    for f in ix.facts(DT.GST_RETURN, "taxable_turnover"):
        fyf = ix.field_of(f.document, "financial_year")
        rt = ix.field_of(f.document, "return_type")
        key = fyf.normalized_value if fyf else None
        if rt and rt.normalized_value and rt.normalized_value.replace("-", "") == "GSTR9":
            figs["GST"].append(Figure("GST annual return turnover", dec(f.value) or Decimal(0), key,
                                      [f.source()], f.field.confidence, ["GSTR-9 annual return"]))
        else:
            by_fy.setdefault(key, []).append(f)
    for fy, facts in by_fy.items():
        if any(g.fy == fy for g in figs["GST"]):
            continue
        total = sum((dec(f.value) or Decimal(0) for f in facts), Decimal(0))
        n = len(facts)
        notes = [f"sum of {n} periodic GST return(s)"]
        conf = _mean([f.field.confidence for f in facts])
        if n < 12:
            total = total * Decimal(12) / Decimal(n)
            notes.append(f"ANNUALISED from {n} return(s) assuming monthly periods - indicative only")
            conf *= 0.7
        figs["GST"].append(Figure("GST returns turnover", total.quantize(Decimal("0.01")), fy,
                                  [f.source() for f in facts], round(conf, 3), notes))
    # Bank: total credits of transaction rows, annualised to 12 months
    for d in ix.docs:
        if d.document_type != DT.BANK_STATEMENT:
            continue
        rows = [r for r in ix.rows_by_doc.get(d.id, []) if r.row_kind == RowKind.TRANSACTION
                and r.status != RowStatus.FAILED and r.parsed]
        if not rows:
            continue
        credits = sum((dec(r.parsed.get("credit")) or Decimal(0) for r in rows), Decimal(0))
        start = ix.field_of(d, "period_start")
        end = ix.field_of(d, "period_end")
        notes = ["total credits incl. non-business inflows (transfers, loans) - indicative only"]
        conf = 0.6
        fy = None
        if start and end and start.normalized_value and end.normalized_value:
            ds, de = date.fromisoformat(start.normalized_value), date.fromisoformat(end.normalized_value)
            months = max(1, round(((de - ds).days + 1) / 30.44))
            fy = N.fy_label(de) if months >= 11 else None
            if months < 12:
                credits = credits * Decimal(12) / Decimal(months)
                notes.append(f"ANNUALISED from {months} month(s)")
                conf = 0.45
        figs["BANK"].append(Figure("Bank credits", credits.quantize(Decimal("0.01")), fy,
                                   [{"document_id": str(d.id), "document_code": d.document_code,
                                     "filename": d.filename, "document_type": d.document_type.value,
                                     "field": "sum(transactions.credit)", "value": str(credits),
                                     "page": None, "confidence": conf, "rows": len(rows)}], conf, notes))
    return figs


def _compare(code: str, a: Figure, b: Figure, threshold: float) -> CheckResult:
    hi = max(abs(a.amount), abs(b.amount))
    variance = float(abs(a.amount - b.amount) / hi) if hi else 0.0
    notes = a.notes + b.notes
    conf = round(min(a.confidence, b.confidence), 3)
    if a.fy and b.fy and a.fy != b.fy:
        notes.append(f"periods differ: {a.fy} vs {b.fy}")
        conf = round(conf * 0.5, 3)
    details = {"variance": round(variance, 4), "threshold": threshold, "a": {"label": a.label, "amount": str(a.amount),
               "fy": a.fy}, "b": {"label": b.label, "amount": str(b.amount), "fy": b.fy}, "notes": notes,
               "difference": str(a.amount - b.amount)}
    if variance <= threshold:
        return CheckResult(code, "FINANCIAL", CheckStatus.PASS, Severity.INFO,
                           f"{a.label} {a.amount} vs {b.label} {b.amount}: variance {variance:.1%} "
                           f"within {threshold:.0%}", conf, a.sources + b.sources, details)
    return CheckResult(code, "FINANCIAL", CheckStatus.INCONSISTENCY,
                       Severity.ERROR if variance > 2 * threshold else Severity.WARNING,
                       f"{a.label} {a.amount} vs {b.label} {b.amount}: variance {variance:.1%} exceeds "
                       f"{threshold:.0%} - material difference to review", conf, a.sources + b.sources, details)


def _pick(figs: list[Figure], fy: str | None) -> Figure | None:
    if not figs:
        return None
    if fy:
        for f in figs:
            if f.fy == fy:
                return f
    return sorted(figs, key=lambda f: f.fy or "", reverse=True)[0]


def check_turnover(ix: FactIndex) -> list[CheckResult]:
    th = variance_thresholds()
    figs = _turnover_figures(ix)
    pairs = [("TURNOVER_ITR_VS_PL", "ITR", "PL"), ("TURNOVER_GST_VS_PL", "GST", "PL"),
             ("TURNOVER_GST_VS_ITR", "GST", "ITR"), ("TURNOVER_BANK_VS_PL", "BANK", "PL"),
             ("TURNOVER_BANK_VS_GST", "BANK", "GST")]
    out = []
    for code, ka, kb in pairs:
        if not figs[ka] or not figs[kb]:
            have = [k for k in (ka, kb) if figs[k]]
            out.append(CheckResult(code, "FINANCIAL", CheckStatus.INSUFFICIENT_DATA, Severity.INFO,
                                   f"Turnover comparison {ka} vs {kb} not possible "
                                   f"(available: {', '.join(have) or 'none'})", None,
                                   [s for k in (ka, kb) for f in figs[k] for s in f.sources]))
            continue
        # every figure on the left is compared (e.g. two ITRs => two results), so no
        # document's figure is silently left out of reconciliation
        for a in figs[ka]:
            b = _pick(figs[kb], a.fy)
            out.append(_compare(code, a, b, th.get(code, 0.1)))  # type: ignore[arg-type]
    return out


def check_periods(ix: FactIndex) -> CheckResult:
    items = []
    for t, n, conv in ((DT.PROFIT_LOSS, "period", None), (DT.BALANCE_SHEET, "period", None),
                       (DT.ITR, "assessment_year", N.assessment_year_to_fy)):
        for f in ix.facts(t, n):
            fy = conv(f.value) if conv else f.value
            items.append((fy, f.source(fy)))
    if len(items) < 2:
        return CheckResult("FINANCIAL_PERIOD_ALIGNMENT", "FINANCIAL", CheckStatus.INSUFFICIENT_DATA, Severity.INFO,
                           "Fewer than two financial documents with an identifiable period", None, [s for _, s in items])
    fys = {fy for fy, _ in items}
    if len(fys) == 1:
        return CheckResult("FINANCIAL_PERIOD_ALIGNMENT", "FINANCIAL", CheckStatus.PASS, Severity.INFO,
                           f"P&L / balance sheet / ITR all relate to FY {next(iter(fys))}", 0.9, [s for _, s in items])
    return CheckResult("FINANCIAL_PERIOD_ALIGNMENT", "FINANCIAL", CheckStatus.INCONSISTENCY, Severity.WARNING,
                       f"Financial documents cover different years: {sorted(fys)}", 0.9, [s for _, s in items],
                       {"periods": sorted(fys)})


def check_bank_vs_balance_sheet(ix: FactIndex) -> CheckResult:
    code = "BANK_BALANCE_VS_BALANCE_SHEET"
    bs = ix.facts(DT.BALANCE_SHEET, "bank_balance")
    closings = ix.facts(DT.BANK_STATEMENT, "closing_balance")
    if not bs or not closings:
        return CheckResult(code, "FINANCIAL", CheckStatus.INSUFFICIENT_DATA, Severity.INFO,
                           "Need a balance-sheet bank balance and a bank statement closing balance", None,
                           [f.source() for f in bs + closings])
    b = bs[0]
    period = ix.field_of(b.document, "period")
    fy_end = date(int(period.normalized_value[:4]) + 1, 3, 31) if period and period.normalized_value else None
    for c in closings:
        end = ix.field_of(c.document, "period_end")
        if fy_end and end and end.normalized_value == fy_end.isoformat():
            th = variance_thresholds()["BANK_BALANCE_VS_BS"]
            fa = Figure("Bank statement closing balance", dec(c.value) or Decimal(0), None, [c.source()], c.field.confidence)
            fb = Figure("Balance-sheet bank balance", dec(b.value) or Decimal(0), None, [b.source()], b.field.confidence)
            r = _compare(code, fa, fb, th)
            r.details["note"] = "Single-account comparison; the balance sheet may aggregate several accounts"
            return r
    return CheckResult(code, "FINANCIAL", CheckStatus.INSUFFICIENT_DATA, Severity.INFO,
                       f"No bank statement ends on the balance-sheet date ({fy_end or 'unknown'})", None,
                       [b.source()] + [c.source() for c in closings])


def reconcile(application: Application, docs: list[Document], fields: list[ExtractedField],
              rows: list[ExtractedTableRow]) -> list[CheckResult]:
    ix = FactIndex(docs, fields, rows)
    results = [check_pan(ix), check_gstin(ix), check_business_name(ix, application)]
    results += check_turnover(ix)
    results += [check_periods(ix), check_bank_vs_balance_sheet(ix)]
    return results
