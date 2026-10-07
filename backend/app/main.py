"""FastAPI entry point.

Prototype decision-support system for MSME loan *document processing*. It extracts,
validates and reconciles documents; it does not score, approve or reject loans.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import database
from app.api import (
    applications,
    documents,
    financial_health,
    financials,
    forecasts,
    processing,
    repayment,
    risk_features,
    transactions,
)
from app.config import get_settings
from app.utils.logging import configure_logging

configure_logging()
settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Document ingestion -> classification -> extraction -> validation -> traceability. "
    "No loan approval logic in this phase.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(applications.router)
app.include_router(documents.router)
app.include_router(processing.router)
app.include_router(financials.router)
app.include_router(transactions.router)
app.include_router(financial_health.router)
app.include_router(forecasts.router)
app.include_router(repayment.router)
app.include_router(risk_features.router)


@app.get("/health", tags=["meta"])
def health():
    db_ok = True
    try:
        with database.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok}
