"""Builds the canonical financial layer for one application from extraction output.

Rules
  * One fact per (source document, metric, period). Facts from different sources are never
    merged or overwritten; disagreements become FinancialConflict rows (not resolved here).
  * Missing values are never zero: no source -> no fact (profile says NOT_AVAILABLE); a derived
    metric with a missing component -> PARTIAL fact with value NULL and the missing parts listed.
  * Every fact keeps its full provenance chain (field -> document -> page -> bbox), and derived
    facts keep the provenance of each component.
  * Bank credits are banking activity (`total_credits`), never revenue/turnover.
The layer is a projection: it is deleted and rebuilt on every run (idempotent).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.financials.metrics import FIELD_METRICS, METRICS, NON_CONFLICTING_CATEGORIES, conflict_group
from app.financials.periods import (
    CanonicalPeriod,
    from_assessment_year,
    from_range,
    normalize_period_expression,
    parse_fiscal_year,
)
from app.financials.transactions import balance_stats, build_transactions
from app.models import (
    Application,
    BankTransaction,
    Document,
    ExtractedField,
    ExtractedTable,
    FinancialConflict,
    FinancialFact,
    FinancialPeriod,
)
from app.models.enums import (
    ConflictStatus,
    DocumentStatus,
    DocumentType,
    ExtractionStatus,
    FactAvailability,
    FactSourceKind,
    FieldValidationStatus,
    MeasureType,
    TxnCategory,
    TxnDirection,
)

DT = DocumentType
ELIGIBLE_STATUSES = (DocumentStatus.PROCESSED, DocumentStatus.NEEDS_REVIEW)
A = FactAvailability


@dataclass
class BuildReport:
    documents: int = 0
    facts: int = 0
    periods: int = 0
    conflicts: int = 0
    transactions: int = 0
    facts_without_period: int = 0
    warnings: list[str] = field(default_factory=list)


def _dec(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


class _Builder:
    def __init__(self, db: Session, application: Application):
        self.db = db
        self.app = application
        self.periods: dict[str, FinancialPeriod] = {}
        self.facts: list[FinancialFact] = []
        self.report = BuildReport()
        self.tolerance = get_settings().fact_conflict_tolerance

    # ------------------------------------------------------------------ periods
    def period(self, cp: CanonicalPeriod | None, expression: str | None, doc: Document) -> FinancialPeriod | None:
        if cp is None:
            return None
        p = self.periods.get(cp.key)
        if p is None:
            p = FinancialPeriod(id=uuid.uuid4(), application_id=self.app.id, period_key=cp.key, period_type=cp.period_type,
                                start_date=cp.start, end_date=cp.end, fiscal_year=cp.fiscal_year, label=cp.label,
                                source_expressions=[])
            self.db.add(p)
            self.periods[cp.key] = p
        exprs = list(p.source_expressions or [])
        entry = {"expression": expression, "document_code": doc.document_code, "document_id": str(doc.id)}
        if entry not in exprs:
            exprs.append(entry)
        p.source_expressions = exprs
        return p

    def doc_period(self, doc: Document, f: dict[str, ExtractedField]) -> tuple[CanonicalPeriod | None, str | None, list[str]]:
        def val(name: str) -> str | None:
            x = f.get(name)
            return x.normalized_value if x is not None and not x.is_missing else None

        notes: list[str] = []
        t = doc.document_type
        if t in (DT.PROFIT_LOSS, DT.BALANCE_SHEET):
            expr = val("period")
            return parse_fiscal_year(expr), expr, notes
        if t == DT.ITR:
            expr = val("assessment_year")
            cp = from_assessment_year(expr)
            if cp:
                notes.append(f"period from assessment year {expr} (AY - 1)")
            return cp, expr, notes
        if t == DT.GST_RETURN:
            rtype = (val("return_type") or "").replace("-", "").upper()
            if rtype == "GSTR9":
                expr = val("financial_year")
                return parse_fiscal_year(expr), expr, notes
            expr = val("tax_period")
            cp = normalize_period_expression(expr) if expr else None
            if cp is None:
                notes.append("periodic GST return without a readable tax period - not assigned to a financial year")
            return cp, expr, notes
        if t == DT.BANK_STATEMENT:
            s, e = f.get("period_start"), f.get("period_end")
            if s and e and not s.is_missing and not e.is_missing and s.normalized_value and e.normalized_value:
                from datetime import date as _d

                start, end = _d.fromisoformat(s.normalized_value), _d.fromisoformat(e.normalized_value)
                return from_range(start, end), f"{s.raw_value} to {e.raw_value}", notes
            notes.append("statement period not extracted")
            return None, None, notes
        return None, None, notes

    # ------------------------------------------------------------------ facts
    def _base_fact(self, metric: str, doc: Document, period: FinancialPeriod | None, cp: CanonicalPeriod | None,
                   **kw) -> FinancialFact:
        m = METRICS[metric]
        fact = FinancialFact(
            id=uuid.uuid4(), application_id=self.app.id, metric=metric, category=m.category, measure_type=m.measure,
            unit="INR", currency="INR",
            period_type=cp.period_type if cp else None, period_start=cp.start if cp else None,
            period_end=cp.end if cp else None,
            as_of_date=cp.end if (cp and m.measure == MeasureType.STOCK and m.category != "BANKING") else None,
            source_document_id=doc.id, source_document_type=doc.document_type, **kw,
        )
        if period is not None:
            fact.period_id = period.id
        notes = list(fact.notes or [])
        if cp is None:
            notes.append("PERIOD_UNKNOWN: the source does not state a readable period; not compared with other facts")
            self.report.facts_without_period += 1
        fact.notes = notes or None
        self.db.add(fact)
        self.facts.append(fact)
        return fact

    @staticmethod
    def field_provenance(doc: Document, f: ExtractedField) -> dict[str, Any]:
        return {
            "chain": "fact -> extracted_field -> document -> page -> bbox",
            "field_id": str(f.id), "field_name": f.field_name, "label": f.label,
            "document_id": str(doc.id), "document_code": doc.document_code, "filename": doc.filename,
            "document_type": doc.document_type.value, "page": f.source_page,
            "source_location": f.source_location, "source_text": f.source_snippet,
            "table_id": str(f.source_table_id) if f.source_table_id else None, "row_index": f.source_row_index,
            "extraction_method": f.extraction_method.value, "extraction_status": f.extraction_status.value,
            "validation_status": f.validation_status.value, "raw_value": f.raw_value,
            "extracted_normalized_value": f.normalized_value, "processing_run": f.processing_run,
        }

    @staticmethod
    def availability_for(f: ExtractedField, value: Decimal | None) -> tuple[FactAvailability, list[str]]:
        notes: list[str] = []
        if value is None:
            notes.append(f"value present in the document but not usable ({f.extraction_status.value})")
            return A.NOT_AVAILABLE, notes
        if f.extraction_status in (ExtractionStatus.LOW_CONFIDENCE, ExtractionStatus.AMBIGUOUS):
            notes.append(f"extraction {f.extraction_status.value}")
            return A.LOW_CONFIDENCE, notes
        if f.validation_status in (FieldValidationStatus.INVALID, FieldValidationStatus.NEEDS_REVIEW):
            notes.append(f"field validation {f.validation_status.value}")
            return A.LOW_CONFIDENCE, notes
        return A.AVAILABLE, notes

    def fact_from_field(self, metric: str, doc: Document, f: ExtractedField, period, cp) -> FinancialFact:
        value = _dec(f.normalized_value)
        availability, notes = self.availability_for(f, value)
        factors = (f.source_location or {}).get("confidence_factors") or {}
        scale = _dec(factors.get("unit_multiplier"))
        if scale is not None:
            notes.append(f"UNIT_NORMALISED: printed '{f.raw_value}' x{scale} -> INR")
        fact = self._base_fact(
            metric, doc, period, cp, value=value, raw_value=f.raw_value, scale_applied=scale,
            source_kind=FactSourceKind.EXTRACTED_FIELD, source_field_id=f.id, source_field_name=f.field_name,
            confidence=f.confidence, extraction_status=f.extraction_status, availability=availability,
            provenance=self.field_provenance(doc, f),
        )
        fact.notes = (fact.notes or []) + notes or None
        return fact

    def derived_fact(self, metric: str, doc: Document, period, cp, formula: str,
                     components: list[tuple[str, ExtractedField | None]]) -> FinancialFact | None:
        """Sum of components. Missing component -> PARTIAL fact with NULL value (never zero-filled)."""
        present = [(n, f) for n, f in components if f is not None and not f.is_missing and _dec(f.normalized_value) is not None]
        if not present:
            return None
        missing = [n for n, f in components if (n, f) not in present]
        comp_prov = [{"component": n, "value": f.normalized_value, **self.field_provenance(doc, f)} for n, f in present]
        conf = round(min(f.confidence for _, f in present) * 0.98, 3)
        derivation = {"formula": formula, "components": comp_prov, "missing_components": missing}
        provenance = {"chain": "fact -> derivation -> extracted_fields -> document -> page -> bbox",
                      "document_id": str(doc.id), "document_code": doc.document_code, "filename": doc.filename,
                      "document_type": doc.document_type.value, "pages": sorted({f.source_page for _, f in present
                                                                                   if f.source_page}),
                      "components": comp_prov}
        if missing:
            return self._base_fact(metric, doc, period, cp, value=None, raw_value=None,
                                   source_kind=FactSourceKind.DERIVED, confidence=conf, availability=A.PARTIAL,
                                   provenance=provenance, derivation=derivation,
                                   notes=[f"PARTIAL: missing component(s) {', '.join(missing)}; not computed"])
        total = sum((_dec(f.normalized_value) for _, f in present), Decimal(0))
        avail = A.AVAILABLE
        for _, f in present:
            a, _ = self.availability_for(f, _dec(f.normalized_value))
            if a == A.LOW_CONFIDENCE:
                avail = A.LOW_CONFIDENCE
        return self._base_fact(metric, doc, period, cp, value=total, raw_value=None,
                               source_kind=FactSourceKind.DERIVED, confidence=conf, availability=avail,
                               provenance=provenance, derivation=derivation, notes=[f"DERIVED: {formula}"])

    # ------------------------------------------------------------------ documents
    def add_document(self, doc: Document, fields: dict[str, ExtractedField]) -> None:
        cp, expr, pnotes = self.doc_period(doc, fields)
        period = self.period(cp, expr, doc)
        if cp is None and doc.document_type in (DT.PROFIT_LOSS, DT.BALANCE_SHEET, DT.ITR, DT.GST_RETURN, DT.BANK_STATEMENT):
            self.report.warnings.append(f"{doc.document_code}: period not determinable ({'; '.join(pnotes) or 'no period field'})")
        self.db.flush()
        start = len(self.facts)
        for (dtype, fname), metric in FIELD_METRICS.items():
            if dtype != doc.document_type:
                continue
            f = fields.get(fname)
            if f is None or f.is_missing:
                continue
            self.fact_from_field(metric, doc, f, period, cp)
        if doc.document_type == DT.BALANCE_SHEET:
            self.balance_sheet_derivations(doc, fields, period, cp)
        if doc.document_type == DT.BANK_STATEMENT:
            self.bank_statement(doc, fields, period, cp)
        for fct in self.facts[start:]:
            if pnotes:
                fct.notes = (fct.notes or []) + pnotes

    def balance_sheet_derivations(self, doc, f, period, cp) -> None:
        cash = f.get("cash")
        if cash is not None and not cash.is_missing and re.search(r"cash\s+and\s+cash\s+equivalents", cash.label or "", re.I):
            self.derived_fact("cash_and_bank", doc, period, cp, "cash and cash equivalents (single line)",
                              [("cash_and_cash_equivalents", cash)])
        else:
            self.derived_fact("cash_and_bank", doc, period, cp, "cash + bank_balance",
                              [("cash", cash), ("bank_balance", f.get("bank_balance"))])
        nw_field = f.get("net_worth")
        if nw_field is None or nw_field.is_missing:
            self.derived_fact("net_worth", doc, period, cp, "capital + reserves",
                              [("capital", f.get("capital")), ("reserves", f.get("reserves"))])
        tl = f.get("total_liabilities")
        if tl is not None and not tl.is_missing:
            if re.search(r"equity|capital", tl.label or "", re.I):
                # "Total equity and liabilities" includes net worth: outside liabilities = total - net worth
                nw = next((x for x in self.facts if x.metric == "net_worth" and x.source_document_id == doc.id
                           and x.value is not None), None)
                tl_comp = {"component": "total_equity_and_liabilities", "value": tl.normalized_value,
                           **self.field_provenance(doc, tl)}
                if nw is None:
                    self._base_fact("total_liabilities", doc, period, cp, value=None, raw_value=tl.raw_value,
                                    source_kind=FactSourceKind.DERIVED, confidence=tl.confidence,
                                    availability=A.PARTIAL, provenance=self.field_provenance(doc, tl),
                                    derivation={"formula": f"'{tl.label}' - net_worth", "components": [tl_comp],
                                                "missing_components": ["net_worth"]},
                                    notes=[f"PARTIAL: '{tl.label}' includes net worth, which is not available"])
                else:
                    value = _dec(tl.normalized_value) - nw.value
                    nw_comp = {"component": "net_worth", "fact_id": str(nw.id), "value": str(nw.value),
                               **(nw.provenance or {})}
                    self._base_fact(
                        "total_liabilities", doc, period, cp, value=value, raw_value=None,
                        source_kind=FactSourceKind.DERIVED,
                        confidence=round(min(tl.confidence, nw.confidence or 0) * 0.98, 3),
                        availability=A.AVAILABLE if nw.availability == A.AVAILABLE and
                        self.availability_for(tl, _dec(tl.normalized_value))[0] == A.AVAILABLE else A.LOW_CONFIDENCE,
                        provenance={"chain": "fact -> derivation -> extracted_fields/facts -> document -> page -> bbox",
                                    "document_id": str(doc.id), "document_code": doc.document_code,
                                    "filename": doc.filename, "document_type": doc.document_type.value,
                                    "components": [tl_comp, nw_comp]},
                        derivation={"formula": f"'{tl.label}' - net_worth", "components": [tl_comp, nw_comp],
                                    "missing_components": []},
                        notes=[f"DERIVED: '{tl.label}' minus net worth"])
            else:
                self.fact_from_field("total_liabilities", doc, tl, period, cp)

    def bank_statement(self, doc: Document, f: dict[str, ExtractedField], period, cp) -> None:
        tables = list(self.db.scalars(select(ExtractedTable).where(ExtractedTable.document_id == doc.id)))
        acct = f.get("account_number")
        rows = build_transactions(self.app.id, doc, tables, acct.normalized_value if acct and not acct.is_missing else None)
        for t in rows.transactions:
            self.db.add(t)
        self.db.flush()
        self.report.transactions += len(rows.transactions)
        txns = rows.transactions
        if not txns:
            self.report.warnings.append(f"{doc.document_code}: no transactions to aggregate")
            return
        unknown_dir = sum(1 for t in txns if t.direction == TxnDirection.UNKNOWN)
        conf = round(sum(t.confidence or 0 for t in txns) / len(txns), 3)
        complete = rows.failed_rows == 0 and unknown_dir == 0
        if complete:
            availability = A.LOW_CONFIDENCE if (rows.review_rows or cp is None) else A.AVAILABLE
        else:
            availability = A.PARTIAL
        base_prov = {
            "chain": "fact -> bank_transactions -> extracted_table_rows -> document -> page -> bbox",
            "document_id": str(doc.id), "document_code": doc.document_code, "filename": doc.filename,
            "document_type": doc.document_type.value, "account_number": acct.normalized_value if acct else None,
            "pages": sorted({t.source_page for t in txns if t.source_page}), "table_ids": rows.tables,
        }
        common_notes = ["BANK_ACTIVITY: statement activity, not revenue or turnover"]
        if rows.failed_rows:
            common_notes.append(f"{rows.failed_rows} statement row(s) could not be parsed")
        if unknown_dir:
            common_notes.append(f"{unknown_dir} transaction(s) without a debit/credit direction")
        if rows.review_rows:
            common_notes.append(f"{rows.review_rows} transaction(s) flagged NEEDS_REVIEW by extraction")

        def flow(metric: str, selected: list[BankTransaction], amount_of, rule: str) -> None:
            observed = sum((amount_of(t) for t in selected), Decimal(0))
            derivation = {"method": "SUM_TRANSACTIONS", "rule": rule, "transaction_count": len(selected),
                          "transaction_ids": [str(t.id) for t in selected], "observed_sum": str(observed),
                          "statement_transactions": len(txns), "unparsed_rows": rows.failed_rows}
            notes = list(common_notes)
            if not selected:
                notes.append("no matching transactions in a fully parsed statement" if complete
                             else "no matching transactions among the parsed rows")
            self._base_fact(metric, doc, period, cp,
                            value=observed if complete else None,  # PARTIAL -> NULL, observed sum kept in derivation
                            raw_value=None, source_kind=FactSourceKind.BANK_TRANSACTIONS, confidence=conf,
                            availability=availability, provenance=base_prov, derivation=derivation, notes=notes)

        credits = [t for t in txns if t.direction == TxnDirection.CREDIT]
        debits = [t for t in txns if t.direction == TxnDirection.DEBIT]
        flow("total_credits", credits, lambda t: t.credit, "all credits")
        flow("total_debits", debits, lambda t: t.debit, "all debits")
        for metric, cat, side, amt in (("cash_deposits", TxnCategory.CASH_DEPOSIT, credits, lambda t: t.credit),
                                       ("cash_withdrawals", TxnCategory.CASH_WITHDRAWAL, debits, lambda t: t.debit),
                                       ("bank_charges", TxnCategory.BANK_CHARGES, debits, lambda t: t.debit),
                                       ("debt_payments", TxnCategory.DEBT_PAYMENT, debits, lambda t: t.debit)):
            flow(metric, [t for t in side if t.category == cat], amt, f"narration classified as {cat.value}")

        opening = f.get("opening_balance")
        stats = balance_stats(txns, _dec(opening.normalized_value) if opening and not opening.is_missing else None,
                              cp.start if cp else None, cp.end if cp else None)
        if stats is None:
            self.report.warnings.append(f"{doc.document_code}: no running balances - balance statistics not available")
            return
        bal_avail = availability if availability != A.PARTIAL else A.LOW_CONFIDENCE
        if stats.notes and bal_avail == A.AVAILABLE:
            bal_avail = A.LOW_CONFIDENCE
        for metric, value in (("average_balance", stats.average), ("minimum_balance", stats.minimum),
                              ("maximum_balance", stats.maximum)):
            self._base_fact(metric, doc, period, cp, value=value, raw_value=None,
                            source_kind=FactSourceKind.BANK_TRANSACTIONS, confidence=conf, availability=bal_avail,
                            provenance=base_prov,
                            derivation={"method": "END_OF_DAY_BALANCES", "days": stats.days,
                                        "from": stats.start.isoformat(), "to": stats.end.isoformat(),
                                        "opening_balance_used": opening.normalized_value if opening and not opening.is_missing else None},
                            notes=common_notes + stats.notes)

    # ------------------------------------------------------------------ conflicts
    def conflicts(self) -> list[FinancialConflict]:
        out: list[FinancialConflict] = []
        groups: dict[tuple[Any, str], list[FinancialFact]] = {}
        for fct in self.facts:
            if fct.period_id is None or fct.value is None or fct.category in NON_CONFLICTING_CATEGORIES:
                continue
            groups.setdefault((fct.period_id, conflict_group(fct.metric)), []).append(fct)
        for (period_id, group), facts in groups.items():
            for a, b in combinations(facts, 2):
                if a.source_document_id == b.source_document_id:
                    continue
                hi = max(abs(a.value), abs(b.value))
                diff = a.value - b.value
                tol_abs = max(Decimal("1"), hi * Decimal(str(self.tolerance)))
                if abs(diff) <= tol_abs:
                    continue
                c = FinancialConflict(
                    application_id=self.app.id, metric=group, period_id=period_id, fact_a_id=a.id, fact_b_id=b.id,
                    value_a=a.value, value_b=b.value, source_a=_source_summary(a), source_b=_source_summary(b),
                    difference=diff, difference_pct=round(float(abs(diff) / hi), 4) if hi else 0.0,
                    tolerance=self.tolerance, status=ConflictStatus.CONFLICTING,
                )
                self.db.add(c)
                out.append(c)
        return out


def _source_summary(f: FinancialFact) -> dict[str, Any]:
    p = f.provenance or {}
    return {"fact_id": str(f.id), "metric": f.metric, "document_id": p.get("document_id"),
            "document_code": p.get("document_code"), "document_type": p.get("document_type"),
            "field": f.source_field_name, "page": p.get("page"), "raw_value": f.raw_value,
            "value": str(f.value), "confidence": f.confidence, "availability": f.availability.value}


def clear_financial_layer(db: Session, application_id) -> None:
    db.execute(delete(FinancialConflict).where(FinancialConflict.application_id == application_id))
    db.execute(delete(FinancialFact).where(FinancialFact.application_id == application_id))
    db.execute(delete(BankTransaction).where(BankTransaction.application_id == application_id))
    db.execute(delete(FinancialPeriod).where(FinancialPeriod.application_id == application_id))


def build_financial_layer(db: Session, application: Application) -> BuildReport:
    clear_financial_layer(db, application.id)
    db.flush()
    b = _Builder(db, application)
    docs = db.scalars(select(Document).where(Document.application_id == application.id,
                                             Document.duplicate_of_id.is_(None),
                                             Document.document_status.in_(ELIGIBLE_STATUSES))
                      .order_by(Document.sequence_no)).all()
    for doc in docs:
        fields = {f.field_name: f for f in db.scalars(select(ExtractedField).where(ExtractedField.document_id == doc.id))}
        b.add_document(doc, fields)
        b.report.documents += 1
    db.flush()
    conflicts = b.conflicts()
    db.flush()
    b.report.facts = len(b.facts)
    b.report.periods = len(b.periods)
    b.report.conflicts = len(conflicts)
    return b.report
