"""Loads the canonical financial layer + transaction intelligence of one application and turns
them into formula `Input`s with status and full source references.

Fact resolution for one (period, metric) - the same rules as the financial profile:
  CONFLICTING     a FinancialConflict exists for the metric's conflict group in that period;
                  every conflicting source is listed and NO value is selected
  NOT_AVAILABLE   no fact
  PARTIAL         only incomplete facts (value NULL, missing components listed)
  LOW_CONFIDENCE  the selected fact is low confidence
  AVAILABLE       otherwise
When several sources agree within the conflict tolerance, the value is taken by a documented
source priority (P&L > balance sheet > ITR > GST return) and every source stays listed.

Bank cash flow is read from the combined monthly aggregates and grouped by financial year.
A year the statements do not fully cover is a PARTIAL_PERIOD and its sums are PARTIAL.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.financial_health.formulas import Input
from app.financials.metrics import CONFLICT_GROUPS, conflict_group
from app.financials.periods import fiscal_year, fy_start_year
from app.models import (
    BankTransaction,
    CashflowMonthly,
    Document,
    FinancialConflict,
    FinancialFact,
    FinancialPeriod,
    RecurringPattern,
    TransactionClassification,
)
from app.models.enums import DocumentType, FactAvailability, HealthPeriodKind, PeriodType

A = FactAvailability
DT = DocumentType
SOURCE_PRIORITY = [DT.PROFIT_LOSS, DT.BALANCE_SHEET, DT.ITR, DT.GST_RETURN, DT.BANK_STATEMENT]
PERIOD_KIND = {PeriodType.FY: HealthPeriodKind.ANNUAL, PeriodType.QUARTER: HealthPeriodKind.QUARTERLY,
               PeriodType.MONTH: HealthPeriodKind.MONTHLY, PeriodType.CUSTOM: HealthPeriodKind.PARTIAL_PERIOD}
USABLE = (A.AVAILABLE, A.LOW_CONFIDENCE)


def fact_source(f: FinancialFact) -> dict[str, Any]:
    """Reference from a health input to a canonical fact and through it to field / document / page / bbox."""
    p = f.provenance or {}
    src: dict[str, Any] = {
        "kind": "FINANCIAL_FACT", "fact_id": str(f.id), "metric": f.metric,
        "value": None if f.value is None else str(f.value), "availability": f.availability.value,
        "confidence": f.confidence, "source_kind": f.source_kind.value, "document_id": p.get("document_id"),
        "document_code": p.get("document_code"), "document_type": p.get("document_type"),
        "field_id": p.get("field_id"), "field_name": f.source_field_name, "page": p.get("page"),
        "pages": p.get("pages"), "bbox": (p.get("source_location") or {}).get("bbox"),
        "source_text": p.get("source_text"), "raw_value": f.raw_value,
    }
    if f.derivation:
        d = f.derivation
        src["derivation"] = {
            "formula": d.get("formula") or d.get("method"), "missing_components": d.get("missing_components"),
            "components": [{"component": c.get("component"), "value": c.get("value"), "field_id": c.get("field_id"),
                            "fact_id": c.get("fact_id"), "page": c.get("page"),
                            "bbox": (c.get("source_location") or {}).get("bbox")}
                           for c in d.get("components") or []],
        }
    return src


def _priority(f: FinancialFact) -> tuple[int, str]:
    t = f.source_document_type
    rank = SOURCE_PRIORITY.index(t) if t in SOURCE_PRIORITY else len(SOURCE_PRIORITY)
    return rank, (f.provenance or {}).get("document_code") or ""


def _conflict_source(c: FinancialConflict) -> dict[str, Any]:
    return {"conflict_id": str(c.id), "metric": c.metric, "value_a": str(c.value_a), "value_b": str(c.value_b),
            "difference": str(c.difference), "difference_pct": c.difference_pct, "tolerance": c.tolerance,
            "source_a": c.source_a, "source_b": c.source_b}


@dataclass
class CashWindow:
    """Combined bank cash-flow months that fall in one financial year."""

    fy_key: str
    fy_label: str
    months: list[CashflowMonthly]
    kind: HealthPeriodKind
    status: FactAvailability
    reason: str | None
    start: date
    end: date
    complete: list[CashflowMonthly] = field(default_factory=list)  # months not PARTIAL


class Snapshot:
    def __init__(self, db: Session, application_id):
        self.application_id = application_id
        self.periods = list(db.scalars(select(FinancialPeriod).where(FinancialPeriod.application_id == application_id)
                                       .order_by(FinancialPeriod.start_date, FinancialPeriod.end_date)))
        self.period_by_id = {p.id: p for p in self.periods}
        self.facts = list(db.scalars(select(FinancialFact).where(FinancialFact.application_id == application_id)))
        self.conflicts = list(db.scalars(select(FinancialConflict)
                                         .where(FinancialConflict.application_id == application_id)))
        self.months = list(db.scalars(select(CashflowMonthly).where(CashflowMonthly.application_id == application_id,
                                                                    CashflowMonthly.document_id.is_(None))
                                      .order_by(CashflowMonthly.month)))
        self.classifications = {str(c.transaction_id): c for c in db.scalars(
            select(TransactionClassification).where(TransactionClassification.application_id == application_id))}
        self.transactions = {str(t.id): t for t in db.scalars(
            select(BankTransaction).where(BankTransaction.application_id == application_id))}
        self.patterns = {str(p.id): p for p in db.scalars(
            select(RecurringPattern).where(RecurringPattern.application_id == application_id))}
        self.documents = {str(d.id): d for d in db.scalars(select(Document).where(
            Document.application_id == application_id, Document.duplicate_of_id.is_(None)))}

        self._facts: dict[tuple[Any, str], list[FinancialFact]] = defaultdict(list)
        for f in self.facts:
            self._facts[(f.period_id, f.metric)].append(f)
        self._conflicts: dict[tuple[Any, str], list[FinancialConflict]] = defaultdict(list)
        for c in self.conflicts:
            self._conflicts[(c.period_id, c.metric)].append(c)

    # ------------------------------------------------------------------ facts
    def facts_in(self, period: FinancialPeriod) -> list[FinancialFact]:
        return [f for f in self.facts if f.period_id == period.id]

    def fact_input(self, period: FinancialPeriod, metrics: list[str], name: str, label: str,
                   basis: str | None = None) -> Input:
        """Resolve one value for `metrics` (one metric, or one conflict group) in `period`."""
        inp = Input(name=name, label=label, value=None, status=A.NOT_AVAILABLE, period_key=period.period_key,
                    basis=basis)
        facts = [f for m in metrics for f in self._facts.get((period.id, m), [])]
        groups = {conflict_group(m) for m in metrics}
        conflicts = [c for g in groups for c in self._conflicts.get((period.id, g), [])]
        inp.sources = [fact_source(f) for f in sorted(facts, key=_priority)]
        if conflicts:
            inp.status = A.CONFLICTING
            inp.conflicts = [_conflict_source(c) for c in conflicts]
            docs = sorted({s.get("document_code") or "?" for c in conflicts for s in (c.source_a, c.source_b)})
            inp.reason = f"sources disagree beyond tolerance ({', '.join(docs)}); not resolved"
            return inp
        if not facts:
            inp.reason = f"no {' / '.join(metrics)} fact for {period.label}"
            return inp
        usable = [f for f in facts if f.value is not None and f.availability in USABLE]
        if not usable:
            inp.status = A.PARTIAL if any(f.availability == A.PARTIAL for f in facts) else A.NOT_AVAILABLE
            inp.reason = "; ".join(n for f in facts for n in (f.notes or [])[:2]) or "no usable value"
            return inp
        chosen = sorted(usable, key=_priority)[0]
        inp.value = chosen.value
        inp.status = chosen.availability
        inp.confidence = chosen.confidence
        if len({f.value for f in usable}) > 1 or len(usable) > 1:
            inp.notes.append(f"{len(usable)} sources agree within the conflict tolerance; value taken from "
                             f"{(chosen.provenance or {}).get('document_code')} ({chosen.source_document_type.value}) "
                             "by source priority P&L > balance sheet > ITR > GST return")
        if inp.status == A.LOW_CONFIDENCE:
            inp.reason = "; ".join((chosen.notes or [])[:2]) or "low-confidence extraction"
        return inp

    def revenue_input(self, period: FinancialPeriod) -> Input:
        """Revenue basis (documented, never silent): P&L revenue from operations; when the period has
        no P&L revenue, the declared turnover (ITR / GST annual return, one conflict group)."""
        rev = self.fact_input(period, ["revenue"], "revenue", "Revenue", basis="P&L revenue from operations")
        declared = self.fact_input(period, sorted(CONFLICT_GROUPS["turnover"]), "revenue", "Revenue",
                                   basis="declared turnover (ITR / GST return)")
        if rev.status != A.NOT_AVAILABLE:
            if declared.has_value and rev.has_value and declared.value:
                diff = (rev.value - declared.value) / declared.value
                rev.notes.append(f"declared turnover for the same period is {declared.value} "
                                 f"({diff:+.2%} vs P&L revenue); not used because P&L revenue is available")
            return rev
        if declared.status == A.NOT_AVAILABLE:
            declared.basis = None
            declared.reason = f"no P&L revenue and no declared turnover (ITR / GST) for {period.label}"
        return declared

    def bank_balance_facts(self) -> list[FinancialFact]:
        return [f for f in self.facts if f.metric in ("average_balance", "minimum_balance")]

    def fy_periods(self) -> list[FinancialPeriod]:
        return [p for p in self.periods if p.period_type == PeriodType.FY]

    def conflicts_within(self, start: date, end: date) -> list[FinancialConflict]:
        out = []
        for c in self.conflicts:
            p = self.period_by_id.get(c.period_id)
            if p is not None and start <= p.start_date and p.end_date <= end:
                out.append(c)
        return out

    # ------------------------------------------------------------------ bank cash flow
    def cash_windows(self) -> dict[str, CashWindow]:
        by_fy: dict[int, list[CashflowMonthly]] = defaultdict(list)
        for m in self.months:
            by_fy[fy_start_year(m.month_start)].append(m)
        out: dict[str, CashWindow] = {}
        for start_year, months in sorted(by_fy.items()):
            fy = fiscal_year(start_year)
            months = sorted(months, key=lambda m: m.month)
            partial = [m for m in months if m.availability == A.PARTIAL]
            reasons = []
            if len(months) < 12:
                reasons.append(f"bank statements cover {len(months)} of 12 months of {fy.label} "
                               f"({months[0].month_start:%b %Y} - {months[-1].month_end:%b %Y})")
            if partial:
                reasons.append("PARTIAL month(s): " + ", ".join(
                    f"{m.month} ({(m.partial_reasons or ['incomplete'])[0]})" for m in partial))
            if reasons:
                status = A.PARTIAL
            elif any(m.availability == A.LOW_CONFIDENCE for m in months):
                status = A.LOW_CONFIDENCE
                reasons.append("low classification coverage in: " + ", ".join(
                    m.month for m in months if m.availability == A.LOW_CONFIDENCE))
            else:
                status = A.AVAILABLE
            out[fy.key] = CashWindow(
                fy_key=fy.key, fy_label=fy.label, months=months,
                kind=HealthPeriodKind.ANNUAL if len(months) == 12 else HealthPeriodKind.PARTIAL_PERIOD,
                status=status, reason="; ".join(reasons) or None,
                start=months[0].month_start, end=months[-1].month_end,
                complete=[m for m in months if m.availability != A.PARTIAL])
        return out

    @staticmethod
    def month_source(m: CashflowMonthly, buckets: list[str]) -> dict[str, Any]:
        ids = (m.provenance or {}).get("transaction_ids") or {}
        return {"kind": "MONTHLY_CASHFLOW", "monthly_aggregate_id": str(m.id), "month": m.month,
                "values": {b: str(getattr(m, b)) for b in buckets}, "availability": m.availability.value,
                "classification_coverage_amount": m.classification_coverage_amount,
                "transaction_ids": sorted({t for b in buckets for t in ids.get(b, [])}),
                "document_ids": (m.provenance or {}).get("document_ids") or []}

    def window_sum(self, w: CashWindow, bucket: str, name: str, label: str) -> Input:
        total = sum((getattr(m, bucket) for m in w.months), Decimal("0.00"))
        confs = [m.confidence for m in w.months if m.confidence is not None]
        return Input(name=name, label=label, value=total, status=w.status, period_key=w.fy_key,
                     basis=f"sum of monthly '{bucket}' over {len(w.months)} month(s) of classified bank transactions",
                     reason=w.reason, confidence=round(sum(confs) / len(confs), 3) if confs else None,
                     sources=[self.month_source(m, [bucket]) for m in w.months])

    def business_inflow_transactions(self, months: list[CashflowMonthly]) -> list[str]:
        out: list[str] = []
        for m in months:
            out.extend(((m.provenance or {}).get("transaction_ids") or {}).get("business_inflow", []))
        return out

    def txn_amount(self, txn_id: str) -> Decimal:
        t = self.transactions.get(txn_id)
        if t is None:
            return Decimal("0")
        return (t.credit if t.credit is not None else t.debit) or Decimal("0")

    # ------------------------------------------------------------------ evidence
    def document_ref(self, doc_id: str | None) -> dict[str, Any] | None:
        d = self.documents.get(str(doc_id)) if doc_id else None
        if d is None:
            return None
        return {"document_id": str(d.id), "document_code": d.document_code,
                "document_type": d.document_type.value if d.document_type else None, "filename": d.filename}

    def evidence_for(self, inputs: list[Input]) -> list[dict[str, Any]]:
        """Source documents with the pages used, from fact sources, conflicts and transactions."""
        pages: dict[str, set[int]] = defaultdict(set)
        docs: set[str] = set()
        meta: dict[str, dict[str, Any]] = {}

        def add(doc_id, page=None, pages_list=None, code=None, dtype=None):
            if not doc_id:
                return
            docs.add(str(doc_id))
            if code and str(doc_id) not in meta:
                meta[str(doc_id)] = {"document_id": str(doc_id), "document_code": code, "document_type": dtype}
            if isinstance(page, int):
                pages[str(doc_id)].add(page)
            for p in pages_list or []:
                if isinstance(p, int):
                    pages[str(doc_id)].add(p)

        for i in inputs:
            for s in i.sources:
                if s.get("kind") == "FINANCIAL_FACT":
                    add(s.get("document_id"), s.get("page"), s.get("pages"), s.get("document_code"),
                        s.get("document_type"))
                    for c in (s.get("derivation") or {}).get("components", []):
                        add(s.get("document_id"), c.get("page"))
                elif s.get("kind") == "MONTHLY_CASHFLOW":
                    for tid in s.get("transaction_ids", []):
                        t = self.transactions.get(tid)
                        if t is not None:
                            add(t.source_document_id, t.source_page)
                    for d in s.get("document_ids", []):
                        add(d)
                elif s.get("kind") == "TRANSACTIONS":
                    for tid in s.get("transaction_ids", []):
                        t = self.transactions.get(tid)
                        if t is not None:
                            add(t.source_document_id, t.source_page)
                elif s.get("kind") == "DOCUMENT":
                    add(s.get("document_id"), code=s.get("document_code"), dtype=s.get("document_type"))
            for c in i.conflicts:
                for side in (c["source_a"], c["source_b"]):
                    add(side.get("document_id"), side.get("page"), code=side.get("document_code"),
                        dtype=side.get("document_type"))
        out = []
        for d in sorted(docs, key=lambda x: (self.documents[x].document_code if x in self.documents
                                             else (meta.get(x) or {}).get("document_code") or x)):
            ref = self.document_ref(d) or meta.get(d) or {"document_id": d}
            out.append({**ref, "pages": sorted(pages.get(d, set()))})
        return out
