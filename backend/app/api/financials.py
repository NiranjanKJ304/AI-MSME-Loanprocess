"""Read APIs for the canonical financial layer (+ explicit rebuild). No scoring, no decisions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_application_or_404
from app.database import get_db
from app.financial_health.service import build_financial_health
from app.forecasting.service import build_forecasts
from app.repayment.service import build_repayment_capacity
from app.financials.builder import build_financial_layer
from app.financials.metrics import METRICS
from app.financials.profile import build_profile
from app.transaction_intel.service import build_transaction_intelligence
from app.models import BankTransaction, DocumentPage, ExtractedField, FinancialConflict, FinancialFact, FinancialPeriod
from app.models.enums import TxnCategory
from app.schemas.financial import BankTransactionOut, ConflictOut, FactOut, PeriodOut

router = APIRouter(prefix="/api", tags=["financials"])


@router.get("/applications/{application_id}/financials/profile")
def financial_profile(application_id: uuid.UUID, db: Session = Depends(get_db)):
    get_application_or_404(db, application_id)
    return build_profile(db, application_id)


@router.get("/applications/{application_id}/financials/facts", response_model=list[FactOut])
def financial_facts(
    application_id: uuid.UUID,
    metric: str | None = None,
    category: str | None = None,
    period_key: str | None = None,
    db: Session = Depends(get_db),
):
    get_application_or_404(db, application_id)
    if metric and metric not in METRICS:
        raise HTTPException(status_code=422, detail=f"Unknown metric '{metric}'. Known: {sorted(METRICS)}")
    stmt = select(FinancialFact).where(FinancialFact.application_id == application_id)
    if metric:
        stmt = stmt.where(FinancialFact.metric == metric)
    if category:
        stmt = stmt.where(FinancialFact.category == category.upper())
    if period_key:
        stmt = stmt.join(FinancialPeriod, FinancialPeriod.id == FinancialFact.period_id).where(
            FinancialPeriod.period_key == period_key)
    return db.scalars(stmt.order_by(FinancialFact.category, FinancialFact.metric, FinancialFact.period_start)).all()


@router.get("/financial-facts/{fact_id}")
def financial_fact_detail(fact_id: uuid.UUID, db: Session = Depends(get_db)):
    """One fact with its provenance chain resolved: fact -> extracted field -> document -> page."""
    fact = db.get(FinancialFact, fact_id)
    if fact is None:
        raise HTTPException(status_code=404, detail="Fact not found")
    field = db.get(ExtractedField, fact.source_field_id) if fact.source_field_id else None
    page = None
    page_no = (fact.provenance or {}).get("page")
    if fact.source_document_id and isinstance(page_no, int):
        p = db.scalars(select(DocumentPage).where(DocumentPage.document_id == fact.source_document_id,
                                                  DocumentPage.page_number == page_no)).first()
        if p:
            page = {"page_number": p.page_number, "width": p.width, "height": p.height,
                    "text_source": p.text_source.value}
    return {
        "fact": FactOut.model_validate(fact).model_dump(mode="json"),
        "extracted_field": None if field is None else {
            "id": str(field.id), "field_name": field.field_name, "raw_value": field.raw_value,
            "normalized_value": field.normalized_value, "confidence": field.confidence,
            "source_page": field.source_page, "source_location": field.source_location,
            "source_snippet": field.source_snippet, "extraction_status": field.extraction_status.value,
        },
        "page": page,
    }


@router.get("/applications/{application_id}/financials/periods", response_model=list[PeriodOut])
def financial_periods(application_id: uuid.UUID, db: Session = Depends(get_db)):
    get_application_or_404(db, application_id)
    return db.scalars(select(FinancialPeriod).where(FinancialPeriod.application_id == application_id)
                      .order_by(FinancialPeriod.start_date, FinancialPeriod.end_date)).all()


@router.get("/applications/{application_id}/financials/conflicts", response_model=list[ConflictOut])
def financial_conflicts(application_id: uuid.UUID, db: Session = Depends(get_db)):
    get_application_or_404(db, application_id)
    return db.scalars(select(FinancialConflict).where(FinancialConflict.application_id == application_id)
                      .order_by(FinancialConflict.metric)).all()


@router.get("/applications/{application_id}/financials/bank-transactions", response_model=list[BankTransactionOut])
def bank_transactions(
    application_id: uuid.UUID,
    document_id: uuid.UUID | None = None,
    category: TxnCategory | None = None,
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    get_application_or_404(db, application_id)
    stmt = select(BankTransaction).where(BankTransaction.application_id == application_id)
    if document_id:
        stmt = stmt.where(BankTransaction.source_document_id == document_id)
    if category:
        stmt = stmt.where(BankTransaction.category == category)
    stmt = stmt.order_by(BankTransaction.source_document_id, BankTransaction.sequence).offset(offset).limit(limit)
    return db.scalars(stmt).all()


@router.post("/applications/{application_id}/financials/rebuild")
def rebuild_financials(application_id: uuid.UUID, db: Session = Depends(get_db)):
    app = get_application_or_404(db, application_id)
    report = build_financial_layer(db, app)
    # canonical transactions were rebuilt: their intelligence must be rebuilt too
    intel = build_transaction_intelligence(db, app)
    health = build_financial_health(db, app)
    forecast = build_forecasts(db, app)
    repayment = build_repayment_capacity(db, app)
    db.commit()
    return {**report.__dict__, "transaction_intelligence": intel.__dict__, "financial_health": health.__dict__,
            "forecasts": forecast.__dict__, "repayment_capacity": repayment.__dict__}
