"""Risk-feature snapshot APIs. Builds an auditable input vector for future risk modelling - no model
is run, and no risk score, probability of default or decision is produced."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_application_or_404
from app.database import get_db
from app.risk_features.service import generate, latest_view, set_view

router = APIRouter(prefix="/api", tags=["risk-features"])


@router.post("/applications/{application_id}/risk-features")
def generate_risk_features(application_id: uuid.UUID, db: Session = Depends(get_db)):
    """Generate a feature snapshot from the application's existing data (reuses an identical snapshot)."""
    app = get_application_or_404(db, application_id)
    gen = generate(db, app)
    db.commit()
    return set_view(db, gen.feature_set, gen)


@router.get("/applications/{application_id}/risk-features")
def get_risk_features(application_id: uuid.UUID, db: Session = Depends(get_db)):
    get_application_or_404(db, application_id)
    return latest_view(db, application_id)


@router.post("/applications/{application_id}/risk-features/rebuild")
def rebuild_risk_features(application_id: uuid.UUID, db: Session = Depends(get_db)):
    """Recompute; verifies an identical snapshot (same id) or stores a new one when the source data changed."""
    app = get_application_or_404(db, application_id)
    gen = generate(db, app, rebuild=True)
    db.commit()
    return set_view(db, gen.feature_set, gen)
