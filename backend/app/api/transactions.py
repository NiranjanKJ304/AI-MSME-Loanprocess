"""Read APIs for transaction intelligence (+ explicit rebuild). Cash-flow information only -
no risk scoring, forecasting or decisions."""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_application_or_404
from app.database import get_db
from app.models import (
    BankTransaction,
    CashflowMetric,
    CashflowMonthly,
    Document,
    ExtractedTableRow,
    RecurringPattern,
    TransactionClassification,
)
from app.models.enums import BusinessNature, ClassificationStatus, TxnClass
from app.financial_health.service import build_financial_health
from app.forecasting.service import build_forecasts
from app.repayment.service import build_repayment_capacity
from app.transaction_intel.config import get_rules
from app.transaction_intel.service import build_transaction_intelligence

router = APIRouter(prefix="/api", tags=["transaction-intelligence"])


def _txn_view(t: BankTransaction, c: TransactionClassification | None) -> dict[str, Any]:
    return {
        "transaction_id": str(t.id), "document_id": str(t.source_document_id), "sequence": t.sequence,
        "transaction_date": t.transaction_date.isoformat() if t.transaction_date else None,
        "description": t.description, "debit": str(t.debit) if t.debit is not None else None,
        "credit": str(t.credit) if t.credit is not None else None,
        "amount": str(t.amount) if t.amount is not None else None, "direction": t.direction.value,
        "source_page": t.source_page,
        "classification": None if c is None else {
            "id": str(c.id), "category": c.category.value, "category_group": c.category_group.value,
            "nature": c.nature.value, "status": c.status.value, "confidence": c.confidence,
            "raw_counterparty": c.raw_counterparty, "normalized_counterparty": c.normalized_counterparty,
            "counterparty_type": c.counterparty_type.value, "channel": c.channel, "references": c.references,
            "evidence": c.evidence, "candidates": c.candidates,
            "linked_transaction_id": str(c.linked_transaction_id) if c.linked_transaction_id else None,
            "link_type": c.link_type, "excluded_from_aggregates": c.excluded_from_aggregates,
            "exclusion_reason": c.exclusion_reason,
            "recurring_pattern_id": str(c.recurring_pattern_id) if c.recurring_pattern_id else None,
            "rules_version": c.rules_version,
        },
    }


@router.get("/applications/{application_id}/transactions/intelligence")
def intelligence_summary(application_id: uuid.UUID, db: Session = Depends(get_db)):
    get_application_or_404(db, application_id)
    cls = list(db.scalars(select(TransactionClassification)
                          .where(TransactionClassification.application_id == application_id)))
    n = len(cls)
    unknown = sum(1 for c in cls if c.status == ClassificationStatus.UNKNOWN)
    return {
        "transactions": n,
        "by_status": dict(Counter(c.status.value for c in cls)),
        "by_category": dict(Counter(c.category.value for c in cls)),
        "by_group": dict(Counter(c.category_group.value for c in cls)),
        "by_nature": dict(Counter(c.nature.value for c in cls)),
        "excluded_from_aggregates": sum(1 for c in cls if c.excluded_from_aggregates),
        "classification_coverage_by_count": round((n - unknown) / n, 4) if n else None,
        "rules_version": cls[0].rules_version if cls else get_rules().version,
        "note": "Bank credits are classified individually; only BUSINESS + INCOME transactions count as business "
                "inflow. Transfers, loans, refunds, reversals and unknown credits are never business revenue.",
    }


@router.get("/applications/{application_id}/transactions/classified")
def classified_transactions(
    application_id: uuid.UUID,
    category: TxnClass | None = None,
    nature: BusinessNature | None = None,
    status: ClassificationStatus | None = None,
    document_id: uuid.UUID | None = None,
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    get_application_or_404(db, application_id)
    stmt = (select(BankTransaction, TransactionClassification)
            .join(TransactionClassification, TransactionClassification.transaction_id == BankTransaction.id,
                  isouter=True)
            .where(BankTransaction.application_id == application_id))
    if category:
        stmt = stmt.where(TransactionClassification.category == category)
    if nature:
        stmt = stmt.where(TransactionClassification.nature == nature)
    if status:
        stmt = stmt.where(TransactionClassification.status == status)
    if document_id:
        stmt = stmt.where(BankTransaction.source_document_id == document_id)
    stmt = stmt.order_by(BankTransaction.source_document_id, BankTransaction.sequence).offset(offset).limit(limit)
    return [_txn_view(t, c) for t, c in db.execute(stmt).all()]


@router.get("/transactions/{transaction_id}/classification")
def transaction_classification(transaction_id: uuid.UUID, db: Session = Depends(get_db)):
    """Classification with its full provenance chain down to the statement row and page/bbox."""
    t = db.get(BankTransaction, transaction_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    c = db.scalars(select(TransactionClassification).where(TransactionClassification.transaction_id == t.id)).first()
    row = db.get(ExtractedTableRow, t.source_row_id) if t.source_row_id else None
    doc = db.get(Document, t.source_document_id)
    return {
        **_txn_view(t, c),
        "provenance": {
            "chain": "classification -> canonical bank transaction -> extracted statement row -> document -> page/bbox",
            "canonical_transaction": t.provenance,
            "statement_row": None if row is None else {
                "row_id": str(row.id), "row_index": row.row_index, "page_number": row.page_number,
                "bbox": row.bbox, "raw_cells": row.raw_cells, "status": row.status.value},
            "document": None if doc is None else {"id": str(doc.id), "document_code": doc.document_code,
                                                  "filename": doc.filename},
        },
    }


@router.get("/applications/{application_id}/transactions/recurring")
def recurring_patterns(application_id: uuid.UUID, db: Session = Depends(get_db)):
    get_application_or_404(db, application_id)
    pats = db.scalars(select(RecurringPattern).where(RecurringPattern.application_id == application_id)
                      .order_by(RecurringPattern.direction, RecurringPattern.average_amount.desc()))
    return [{
        "id": str(p.id), "direction": p.direction, "counterparty": p.counterparty, "group_key": p.group_key,
        "pattern_type": p.pattern_type, "frequency": p.frequency.value, "average_amount": str(p.average_amount),
        "min_amount": str(p.min_amount), "max_amount": str(p.max_amount), "amount_cv": p.amount_cv,
        "occurrences": p.occurrences, "distinct_months": p.distinct_months, "first_seen": p.first_seen.isoformat(),
        "last_seen": p.last_seen.isoformat(), "median_interval_days": p.median_interval_days,
        "confidence": p.confidence, "transaction_ids": p.transaction_ids, "document_ids": p.document_ids,
    } for p in pats]


@router.get("/applications/{application_id}/cashflow/monthly")
def cashflow_monthly(
    application_id: uuid.UUID,
    document_id: uuid.UUID | None = Query(None, description="One statement; omit for all statements combined"),
    db: Session = Depends(get_db),
):
    get_application_or_404(db, application_id)
    stmt = select(CashflowMonthly).where(CashflowMonthly.application_id == application_id)
    stmt = stmt.where(CashflowMonthly.document_id == document_id) if document_id else \
        stmt.where(CashflowMonthly.document_id.is_(None))
    rows = db.scalars(stmt.order_by(CashflowMonthly.month)).all()
    money_cols = [c.name for c in CashflowMonthly.__table__.columns if str(c.type).startswith("NUMERIC")]
    return [{
        "id": str(r.id), "document_id": str(r.document_id) if r.document_id else None, "month": r.month,
        "days_in_month": r.days_in_month, "days_covered": r.days_covered,
        "values": {c: str(getattr(r, c)) for c in money_cols},
        "counts": {k: getattr(r, k) for k in ("txn_count", "business_txn_count", "unknown_txn_count",
                                               "low_confidence_txn_count", "excluded_txn_count")},
        "classification_coverage_count": r.classification_coverage_count,
        "classification_coverage_amount": r.classification_coverage_amount, "confidence": r.confidence,
        "availability": r.availability.value, "partial_reasons": r.partial_reasons, "provenance": r.provenance,
    } for r in rows]


@router.get("/applications/{application_id}/cashflow/metrics")
def cashflow_metrics(application_id: uuid.UUID, db: Session = Depends(get_db)):
    get_application_or_404(db, application_id)
    rows = db.scalars(select(CashflowMetric).where(CashflowMetric.application_id == application_id)
                      .order_by(CashflowMetric.metric)).all()
    return [{
        "id": str(r.id), "metric": r.metric, "value": str(r.value) if r.value is not None else None, "unit": r.unit,
        "availability": r.availability.value, "confidence": r.confidence, "months_used": r.months_used,
        "months_total": r.months_total, "details": r.details, "provenance": r.provenance,
    } for r in rows]


@router.post("/applications/{application_id}/transactions/intelligence/rebuild")
def rebuild_intelligence(application_id: uuid.UUID, db: Session = Depends(get_db)):
    app = get_application_or_404(db, application_id)
    report = build_transaction_intelligence(db, app)
    health = build_financial_health(db, app)  # health and forecasts read the cash-flow aggregates just rebuilt
    forecast = build_forecasts(db, app)
    repayment = build_repayment_capacity(db, app)
    db.commit()
    return {**report.__dict__, "financial_health": health.__dict__, "forecasts": forecast.__dict__,
            "repayment_capacity": repayment.__dict__}


@router.get("/transaction-rules")
def transaction_rules():
    """The active classification rule set (read-only)."""
    return get_rules().model_dump(mode="json")
