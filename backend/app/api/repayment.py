"""Repayment-capacity APIs. Descriptive analysis only - no risk score, approval, rejection or
credit recommendation."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_application_or_404
from app.database import get_db
from app.repayment.loan import LoanTermsIn
from app.repayment.service import build_repayment_capacity, repayment_view, save_terms

router = APIRouter(prefix="/api", tags=["repayment-capacity"])


@router.post("/applications/{application_id}/repayment-capacity")
def submit_loan_terms(application_id: uuid.UUID, terms: LoanTermsIn, db: Session = Depends(get_db)):
    """Store the proposed loan terms (officer / user-provided; audited) and build the analysis."""
    app = get_application_or_404(db, application_id)
    save_terms(db, app, terms)
    build_repayment_capacity(db, app)
    db.commit()
    return repayment_view(db, application_id)


@router.get("/applications/{application_id}/repayment-capacity")
def get_repayment_capacity(application_id: uuid.UUID, db: Session = Depends(get_db)):
    get_application_or_404(db, application_id)
    return repayment_view(db, application_id)


@router.post("/applications/{application_id}/repayment-capacity/rebuild")
def rebuild_repayment_capacity(application_id: uuid.UUID, db: Session = Depends(get_db)):
    """Recalculate with the stored loan terms and the current financial data."""
    app = get_application_or_404(db, application_id)
    build_repayment_capacity(db, app)
    db.commit()
    return repayment_view(db, application_id)
