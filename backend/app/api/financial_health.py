"""Read APIs for the financial health profile (+ explicit rebuild). Descriptive financial condition
only - no risk score, repayment capacity, forecast or loan decision."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_application_or_404
from app.database import get_db
from app.financial_health.service import build_financial_health, health_profile, metric_view
from app.models import BankTransaction, Document, ExtractedField, ExtractedTableRow, FinancialFact, FinancialHealthMetric

router = APIRouter(prefix="/api", tags=["financial-health"])


@router.get("/applications/{application_id}/financial-health")
def financial_health(application_id: uuid.UUID, db: Session = Depends(get_db)):
    """All periods: dimensions -> indicator + metrics (value, status, formula, inputs, evidence)."""
    get_application_or_404(db, application_id)
    return health_profile(db, application_id)


@router.get("/applications/{application_id}/financial-health/{period_key}")
def financial_health_period(application_id: uuid.UUID, period_key: str, db: Session = Depends(get_db)):
    """One period: FY2024-25 (annual / partial bank year), 2025-03 (month), FY2024-25-Q1, custom range key."""
    get_application_or_404(db, application_id)
    out = health_profile(db, application_id, period_key)
    if out is None:
        raise HTTPException(status_code=404, detail=f"No financial health analysis for period '{period_key}'")
    return out


@router.post("/applications/{application_id}/financial-health/rebuild")
def rebuild_financial_health(application_id: uuid.UUID, db: Session = Depends(get_db)):
    app = get_application_or_404(db, application_id)
    report = build_financial_health(db, app)
    db.commit()
    return report.__dict__


@router.get("/financial-health/metrics/{metric_id}")
def financial_health_metric(metric_id: uuid.UUID, transactions_limit: int = Query(200, ge=0, le=5000),
                            db: Session = Depends(get_db)):
    """One metric with its provenance resolved down to extracted fields / statement rows and pages."""
    m = db.get(FinancialHealthMetric, metric_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Metric not found")
    prov = m.provenance or {}
    facts = []
    for fid in prov.get("fact_ids", []):
        f = db.get(FinancialFact, uuid.UUID(fid))
        if f is None:
            facts.append({"fact_id": fid, "missing": "fact no longer exists (financial layer rebuilt) - rebuild health"})
            continue
        field = db.get(ExtractedField, f.source_field_id) if f.source_field_id else None
        doc = db.get(Document, f.source_document_id) if f.source_document_id else None
        facts.append({
            "fact_id": fid, "metric": f.metric, "value": None if f.value is None else str(f.value),
            "availability": f.availability.value, "source_kind": f.source_kind.value, "derivation": f.derivation,
            "document": None if doc is None else {"id": str(doc.id), "document_code": doc.document_code,
                                                  "filename": doc.filename},
            "extracted_field": None if field is None else {
                "id": str(field.id), "field_name": field.field_name, "raw_value": field.raw_value,
                "page": field.source_page, "bbox": (field.source_location or {}).get("bbox"),
                "source_snippet": field.source_snippet, "extraction_status": field.extraction_status.value},
        })
    txns = []
    txn_ids = prov.get("transaction_ids", [])
    for tid in txn_ids[:transactions_limit]:
        t = db.get(BankTransaction, uuid.UUID(tid))
        if t is None:
            txns.append({"transaction_id": tid, "missing": "transaction no longer exists - rebuild health"})
            continue
        row = db.get(ExtractedTableRow, t.source_row_id) if t.source_row_id else None
        txns.append({
            "transaction_id": tid, "date": t.transaction_date.isoformat() if t.transaction_date else None,
            "description": t.description, "amount": None if t.amount is None else str(t.amount),
            "document_id": str(t.source_document_id), "page": t.source_page,
            "statement_row": None if row is None else {"row_id": str(row.id), "row_index": row.row_index,
                                                       "page_number": row.page_number, "bbox": row.bbox},
        })
    return {**metric_view(m), "resolved": {
        "chain": prov.get("chain"), "facts": facts, "transactions": txns,
        "transactions_total": len(txn_ids), "transactions_shown": len(txns)}}
