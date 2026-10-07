"""Deterministic financial forecasts (projection over the canonical financial layer and the
monthly cash flow; deleted and rebuilt on every run).

  ForecastRun          one per (application, target metric): selected model + engine / config
                       versions, data-quality checks, seasonality, backtests, assumptions, provenance
  ForecastObservation  every historical period considered, with its value, whether it was used for
                       training and why not, and its source ids (facts / monthly aggregates / transactions)
  ForecastResult       one forecast period: predicted value, optional interval, status, explanation

Forecasts are projections, never actuals; no repayment-capacity or credit logic lives here.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, JSONType, utcnow
from app.models._types import enum_column
from app.models.enums import FactAvailability, ForecastFrequency

MONEY = Numeric(20, 2)


class ForecastRun(Base):
    __tablename__ = "forecast_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    metric: Mapped[str] = mapped_column(String(48), index=True)
    frequency: Mapped[ForecastFrequency] = mapped_column(enum_column(ForecastFrequency))
    engine_version: Mapped[str] = mapped_column(String(64))
    config_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[FactAvailability] = mapped_column(enum_column(FactAvailability))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(32), nullable=True)  # NULL = no forecast
    model_params: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    training_start: Mapped[str | None] = mapped_column(String(64), nullable=True)
    training_end: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observations_used: Mapped[int] = mapped_column(Integer)
    observations_total: Mapped[int] = mapped_column(Integer)
    basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    selection: Mapped[dict[str, Any]] = mapped_column(JSONType)  # candidates, rejected, rule
    backtest: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)  # selected model's backtest
    uncertainty: Mapped[dict[str, Any]] = mapped_column(JSONType)
    seasonality: Mapped[dict[str, Any]] = mapped_column(JSONType)
    data_quality: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)
    assumptions: Mapped[list[str]] = mapped_column(JSONType)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ForecastObservation(Base):
    __tablename__ = "forecast_observations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("forecast_runs.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    period_key: Mapped[str] = mapped_column(String(64))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # historical actual; NULL = not usable
    source_status: Mapped[FactAvailability] = mapped_column(enum_column(FactAvailability))
    used_for_training: Mapped[bool] = mapped_column(Boolean)
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    outlier: Mapped[bool] = mapped_column(Boolean, default=False)
    sources: Mapped[dict[str, Any]] = mapped_column(JSONType)  # fact / monthly aggregate / transaction ids
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)  # documents and pages
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ForecastResult(Base):
    __tablename__ = "forecast_results"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("forecast_runs.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    metric: Mapped[str] = mapped_column(String(48))
    period_key: Mapped[str] = mapped_column(String(64))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    horizon: Mapped[int] = mapped_column(Integer)  # steps after the last training observation
    predicted_value: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    lower_bound: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    upper_bound: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    interval_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String(32))
    status: Mapped[FactAvailability] = mapped_column(enum_column(FactAvailability))
    explanation: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
