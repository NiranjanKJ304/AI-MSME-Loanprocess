"""Financial profile: per canonical period, the state of every metric.

  AVAILABLE       at least one usable fact, all usable facts agree
  LOW_CONFIDENCE  only low-confidence facts
  PARTIAL         only incomplete facts (derived with missing parts / partly parsed statements /
                  bank data covering part of the financial year)
  CONFLICTING     sources disagree (see conflicts) - the profile does not pick a winner
  NOT_AVAILABLE   no fact; `reason` says what was looked at

A metric `value` is shown only when it is unambiguous (no conflict, one distinct usable value).
Missing values are null - never zero.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.financials.metrics import FIELD_METRICS, METRICS, conflict_group
from app.models import Document, ExtractedField, FinancialConflict, FinancialFact, FinancialPeriod
from app.models.enums import DocumentStatus, FactAvailability, PeriodType

A = FactAvailability
# where each metric can come from (for NOT_AVAILABLE reasons)
_EXPECTED_SOURCES: dict[str, set[str]] = defaultdict(set)
for (dt, fname), m in FIELD_METRICS.items():
    _EXPECTED_SOURCES[m].add(f"{dt.value}.{fname}")
for m in ("cash_and_bank", "net_worth", "total_liabilities"):
    _EXPECTED_SOURCES[m].add("BALANCE_SHEET (derived)")
for code, metric in METRICS.items():
    if metric.category == "BANKING":
        _EXPECTED_SOURCES[code].add("BANK_STATEMENT transactions")


def _fact_brief(f: FinancialFact) -> dict[str, Any]:
    p = f.provenance or {}
    return {"fact_id": str(f.id), "value": str(f.value) if f.value is not None else None,
            "availability": f.availability.value, "confidence": f.confidence,
            "source_document": p.get("document_code"), "source_document_type": p.get("document_type"),
            "source_field": f.source_field_name, "page": p.get("page") or p.get("pages")}


def build_profile(db: Session, application_id) -> dict[str, Any]:
    periods = list(db.scalars(select(FinancialPeriod).where(FinancialPeriod.application_id == application_id)
                              .order_by(FinancialPeriod.start_date, FinancialPeriod.end_date)))
    facts = list(db.scalars(select(FinancialFact).where(FinancialFact.application_id == application_id)))
    conflicts = list(db.scalars(select(FinancialConflict).where(FinancialConflict.application_id == application_id)))
    conflicted = {(c.period_id, c.metric) for c in conflicts}

    # extraction state of the fields that could have supplied a metric (for NOT_AVAILABLE reasons)
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.application_id == application_id,
                                                               Document.duplicate_of_id.is_(None)))}
    field_states: dict[str, list[str]] = defaultdict(list)
    for f in db.scalars(select(ExtractedField).where(ExtractedField.application_id == application_id,
                                                     ExtractedField.is_missing.is_(True))):
        d = docs.get(f.document_id)
        if d is None:
            continue
        metric = FIELD_METRICS.get((d.document_type, f.field_name))
        if metric:
            field_states[metric].append(f"{d.document_code}.{f.field_name}: {f.extraction_status.value}")
    unprocessed = [d.document_code for d in docs.values()
                   if d.document_status not in (DocumentStatus.PROCESSED, DocumentStatus.NEEDS_REVIEW)]

    by_period: dict[Any, dict[str, list[FinancialFact]]] = defaultdict(lambda: defaultdict(list))
    for f in facts:
        by_period[f.period_id][f.metric].append(f)

    def cell(period: FinancialPeriod | None, metric: str) -> dict[str, Any]:
        pid = period.id if period else None
        fs = by_period[pid].get(metric, [])
        usable = [f for f in fs if f.value is not None and f.availability != A.NOT_AVAILABLE]
        out: dict[str, Any] = {"metric": metric, "category": METRICS[metric].category,
                               "unit": "INR", "value": None, "facts": [_fact_brief(f) for f in fs]}
        if (pid, conflict_group(metric)) in conflicted:
            out["status"] = A.CONFLICTING.value
            out["reason"] = "sources disagree - see conflicts (not resolved in this phase)"
            return out
        if not fs:
            # bank data for part of this financial year only?
            if period is not None and period.period_type == PeriodType.FY and METRICS[metric].category == "BANKING":
                partial = [f for f in facts if f.metric == metric and f.period_start and f.period_end
                           and f.period_start <= period.end_date and period.start_date <= f.period_end
                           and f.period_id != pid]
                if partial:
                    out["status"] = A.PARTIAL.value
                    out["reason"] = "bank statements cover only part of this financial year"
                    out["facts"] = [_fact_brief(f) for f in partial]
                    return out
            out["status"] = A.NOT_AVAILABLE.value
            reasons = field_states.get(metric, [])
            out["reason"] = ("; ".join(reasons) if reasons else
                             f"no source document for this period (expected from: {', '.join(sorted(_EXPECTED_SOURCES[metric]))})")
            return out
        if not usable:
            out["status"] = (A.PARTIAL if any(f.availability == A.PARTIAL for f in fs) else A.NOT_AVAILABLE).value
            out["reason"] = "; ".join(n for f in fs for n in (f.notes or [])[:2]) or "no usable value"
            return out
        values = {f.value for f in usable}
        if all(f.availability == A.LOW_CONFIDENCE for f in usable):
            out["status"] = A.LOW_CONFIDENCE.value
        else:
            out["status"] = A.AVAILABLE.value
        if len(values) == 1:
            out["value"] = str(next(iter(values)))
        else:
            out["reason"] = "several values within tolerance - see facts"
        return out

    result_periods = []
    for p in periods:
        metrics = {code: cell(p, code) for code in METRICS}
        result_periods.append({
            "period_id": str(p.id), "period_key": p.period_key, "period_type": p.period_type.value,
            "start_date": p.start_date.isoformat(), "end_date": p.end_date.isoformat(), "fiscal_year": p.fiscal_year,
            "label": p.label,
            "summary": {s.value: sum(1 for c in metrics.values() if c["status"] == s.value) for s in A},
            "metrics": metrics,
        })
    unknown = [f for f in facts if f.period_id is None]
    return {
        "application_id": str(application_id),
        "unit": "INR",
        "notes": [
            "Bank activity (total_credits etc.) is reported separately and is never treated as revenue or turnover.",
            "Missing values are null, never zero. Conflicts are recorded, not resolved.",
        ],
        "documents_not_yet_processed": unprocessed,
        "periods": result_periods,
        "facts_without_period": [_fact_brief(f) | {"metric": f.metric} for f in unknown],
        "conflicts": len(conflicts),
    }
