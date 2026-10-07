"""Read APIs for deterministic financial forecasts (+ explicit rebuild). Projections only - no
repayment capacity, risk score or credit decision."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_application_or_404
from app.database import get_db
from app.forecasting.series import TARGETS
from app.forecasting.service import build_forecasts, forecasts_view

router = APIRouter(prefix="/api", tags=["forecasts"])


@router.get("/applications/{application_id}/forecasts")
def forecasts(application_id: uuid.UUID, db: Session = Depends(get_db)):
    """All targets: historical actuals, forecast, model, backtest summary, uncertainty, data quality."""
    get_application_or_404(db, application_id)
    return forecasts_view(db, application_id)


@router.get("/applications/{application_id}/forecasts/{metric}")
def forecast_metric(application_id: uuid.UUID, metric: str, db: Session = Depends(get_db)):
    """One target with full detail: model selection (all candidates' backtests), observation sources,
    evidence and provenance."""
    get_application_or_404(db, application_id)
    if metric not in TARGETS:
        raise HTTPException(status_code=404, detail=f"Unknown forecast metric '{metric}'. Known: {list(TARGETS)}")
    out = forecasts_view(db, application_id, metric)
    if out is None:
        raise HTTPException(status_code=404, detail="Forecasts not built yet - POST .../forecasts/rebuild")
    return out


@router.post("/applications/{application_id}/forecasts/rebuild")
def rebuild_forecasts(application_id: uuid.UUID, db: Session = Depends(get_db)):
    app = get_application_or_404(db, application_id)
    report = build_forecasts(db, app)
    db.commit()
    return report.__dict__
