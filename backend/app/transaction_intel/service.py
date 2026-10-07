"""Persists transaction intelligence for an application (deleted and rebuilt on every run).

Entity context used by the engine (all from data already extracted - nothing invented):
  own names      application business name, GST legal/trade name, bank account holder,
                 ITR / incorporation / Udyam names, PAN name of a non-individual PAN
  related names  PAN name of an individual PAN (proprietor) + configured related parties
  accounts       account numbers of every bank statement of the application
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    Application,
    BankTransaction,
    CashflowMetric,
    CashflowMonthly,
    Document,
    ExtractedField,
    ExtractedTable,
    RecurringPattern,
    TransactionClassification,
)
from app.models.enums import DocumentType, RowKind, RowStatus, TxnDirection
from app.transaction_intel.aggregation import BUCKETS, StatementInfo, aggregate, cashflow_metrics
from app.transaction_intel.config import get_rules
from app.transaction_intel.engine import Engine, EntityContext, TxnInput

OWN_NAME_FIELDS = {"legal_name", "trade_name", "account_holder", "entity_name", "enterprise_name"}


@dataclass
class IntelReport:
    transactions: int = 0
    classified: int = 0
    low_confidence: int = 0
    unknown: int = 0
    excluded: int = 0
    recurring_patterns: int = 0
    months: int = 0
    rules_version: str = ""
    warnings: list[str] = field(default_factory=list)


def clear_transaction_intelligence(db: Session, application_id) -> None:
    db.execute(delete(CashflowMetric).where(CashflowMetric.application_id == application_id))
    db.execute(delete(CashflowMonthly).where(CashflowMonthly.application_id == application_id))
    db.execute(delete(TransactionClassification).where(TransactionClassification.application_id == application_id))
    db.execute(delete(RecurringPattern).where(RecurringPattern.application_id == application_id))


def _entity_context(db: Session, application: Application, txns: list[BankTransaction]) -> EntityContext:
    rules = get_rules()
    own = [application.business_name]
    related = list(rules.related_party_names)
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.application_id == application.id,
                                                               Document.duplicate_of_id.is_(None)))}
    fields = db.scalars(select(ExtractedField).where(ExtractedField.application_id == application.id,
                                                     ExtractedField.is_missing.is_(False)))
    pan_entity: dict = {}
    pan_names: dict = {}
    for f in fields:
        d = docs.get(f.document_id)
        if d is None or not f.normalized_value:
            continue
        if f.field_name in OWN_NAME_FIELDS:
            own.append(f.normalized_value)
        elif f.field_name == "name" and d.document_type == DocumentType.ITR:
            own.append(f.normalized_value)
        elif d.document_type == DocumentType.PAN and f.field_name == "name":
            pan_names[d.id] = f.normalized_value
        elif d.document_type == DocumentType.PAN and f.field_name == "entity_type":
            pan_entity[d.id] = f.normalized_value
    for doc_id, name in pan_names.items():
        (related if pan_entity.get(doc_id) == "Individual" else own).append(name)
    accounts = {str(t.source_document_id): t.account_number for t in txns}
    return EntityContext(own_names=sorted(set(own)), related_names=sorted(set(related)), account_numbers=accounts)


def _statements(db: Session, txns: list[BankTransaction]) -> list[StatementInfo]:
    out = []
    for doc_id in sorted({t.source_document_id for t in txns}, key=str):
        f = {x.field_name: x for x in db.scalars(select(ExtractedField).where(ExtractedField.document_id == doc_id))}

        def d(name: str) -> date | None:
            x = f.get(name)
            try:
                return date.fromisoformat(x.normalized_value) if x and x.normalized_value else None
            except ValueError:
                return None

        failed = 0
        for t in db.scalars(select(ExtractedTable).where(ExtractedTable.document_id == doc_id)):
            rows = list(t.rows)
            if any(r.row_kind == RowKind.TRANSACTION for r in rows):
                failed += sum(1 for r in rows if r.row_kind == RowKind.UNPARSED or r.status == RowStatus.FAILED)
        acct = next((t.account_number for t in txns if t.source_document_id == doc_id), None)
        out.append(StatementInfo(str(doc_id), acct, d("period_start"), d("period_end"), failed))
    return out


def build_transaction_intelligence(db: Session, application: Application) -> IntelReport:
    rules = get_rules()
    clear_transaction_intelligence(db, application.id)
    db.flush()
    report = IntelReport(rules_version=rules.version)
    txns = list(db.scalars(select(BankTransaction).where(BankTransaction.application_id == application.id)
                           .order_by(BankTransaction.source_document_id, BankTransaction.sequence)))
    report.transactions = len(txns)
    if not txns:
        return report
    inputs = [TxnInput(
        id=str(t.id), document_id=str(t.source_document_id), account_number=t.account_number, date=t.transaction_date,
        description=t.description, reference=t.reference, debit=t.debit, credit=t.credit,
        direction=t.direction.value if t.direction != TxnDirection.UNKNOWN else "UNKNOWN", sequence=t.sequence,
        page=t.source_page) for t in txns]
    ctx = _entity_context(db, application, txns)
    result = Engine(rules).run(inputs, ctx)

    pattern_ids: dict[str, uuid.UUID] = {}
    for p in result.patterns:
        pid = uuid.uuid4()
        pattern_ids[p.key] = pid
        db.add(RecurringPattern(
            id=pid, application_id=application.id, group_key=p.key, direction=p.direction, counterparty=p.counterparty,
            pattern_type=p.pattern_type, frequency=p.frequency, average_amount=p.average_amount,
            min_amount=p.min_amount, max_amount=p.max_amount, amount_cv=p.amount_cv, occurrences=p.occurrences,
            distinct_months=p.distinct_months, first_seen=p.first_seen, last_seen=p.last_seen,
            median_interval_days=p.median_interval_days, confidence=p.confidence, transaction_ids=p.txn_ids,
            document_ids=p.document_ids))
    db.flush()
    for t in inputs:
        c = result.classifications[t.id]
        db.add(TransactionClassification(
            application_id=application.id, transaction_id=uuid.UUID(t.id), category=c.category,
            category_group=c.group, nature=c.nature, status=c.status, confidence=c.confidence,
            raw_counterparty=c.narration.raw_counterparty, normalized_counterparty=c.narration.normalized_counterparty,
            counterparty_type=c.counterparty_type, channel=c.narration.channel,
            references=c.narration.references or None, vpa=c.narration.vpa, evidence=c.evidence,
            candidates=c.candidates or None,
            linked_transaction_id=uuid.UUID(c.linked_transaction_id) if c.linked_transaction_id else None,
            link_type=c.link_type, excluded_from_aggregates=c.excluded_from_aggregates,
            exclusion_reason=c.exclusion_reason,
            recurring_pattern_id=pattern_ids.get(c.recurring_key) if c.recurring_key else None,
            rules_version=result.rules_version))
        report.classified += c.status.value == "CLASSIFIED"
        report.low_confidence += c.status.value == "LOW_CONFIDENCE"
        report.unknown += c.status.value == "UNKNOWN"
        report.excluded += c.excluded_from_aggregates
    report.recurring_patterns = len(result.patterns)

    statements = _statements(db, txns)
    months = aggregate(inputs, result.classifications, statements, rules.thresholds.monthly_coverage_ok)
    month_ids: dict[tuple[str | None, str], uuid.UUID] = {}
    for m in months:
        mid = uuid.uuid4()
        month_ids[(m.scope_document_id, m.month)] = mid
        db.add(CashflowMonthly(
            id=mid, application_id=application.id,
            document_id=uuid.UUID(m.scope_document_id) if m.scope_document_id else None, month=m.month,
            month_start=m.month_start, month_end=m.month_end, days_in_month=m.days_in_month,
            days_covered=m.days_covered, **{b: m.values[b] for b in BUCKETS}, **m.counts,
            classification_coverage_count=m.coverage_count, classification_coverage_amount=m.coverage_amount,
            confidence=m.confidence, availability=m.availability, partial_reasons=m.partial_reasons or None,
            provenance=m.provenance))
        if m.partial_reasons and m.scope_document_id is None:
            report.warnings.append(f"{m.month}: PARTIAL - {m.partial_reasons[0]}")
    report.months = sum(1 for m in months if m.scope_document_id is None)
    for metric in cashflow_metrics(months, inputs, result.classifications, statements):
        db.add(CashflowMetric(
            application_id=application.id, metric=metric.metric, value=metric.value, unit=metric.unit,
            availability=metric.availability, confidence=metric.confidence, months_used=metric.months_used,
            months_total=metric.months_total, details=metric.details,
            provenance={"chain": "metric -> monthly aggregates -> classified transactions -> canonical transactions "
                                 "-> statement rows -> document -> page/bbox",
                        "monthly_aggregate_ids": [str(month_ids[(None, k)]) for k in metric.month_keys
                                                  if (None, k) in month_ids]}))
    db.flush()
    return report
