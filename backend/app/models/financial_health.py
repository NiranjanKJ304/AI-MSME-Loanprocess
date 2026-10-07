"""Financial health analysis (projection over the canonical financial layer and transaction
intelligence; deleted and rebuilt on every run).

  FinancialHealthMetric     one calculated value of one metric for one period, with its formula,
                            inputs, status, deterministic explanation and provenance
  FinancialHealthIndicator  descriptive condition of one dimension in one period (STRONG / STABLE /
                            DECLINING / WEAK / INSUFFICIENT_DATA / CONFLICTING_DATA) with its evidence

Describes financial condition only - no risk score, no repayment capacity, no decision.
Periods are referenced by key (not FK) so rebuilding the financial layer never cascades into here.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, Float, ForeignKey, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, JSONType, utcnow
from app.models._types import enum_column
from app.models.enums import FactAvailability, HealthDimension, HealthIndicator, HealthPeriodKind


class FinancialHealthMetric(Base):
    __tablename__ = "financial_health_metrics"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    dimension: Mapped[HealthDimension] = mapped_column(enum_column(HealthDimension), index=True)
    metric: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(128))
    period_key: Mapped[str] = mapped_column(String(64), index=True)
    period_kind: Mapped[HealthPeriodKind] = mapped_column(enum_column(HealthPeriodKind))
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    compare_period_key: Mapped[str | None] = mapped_column(String(64), nullable=True)  # trends only
    scope: Mapped[str | None] = mapped_column(String(128), nullable=True)  # e.g. one bank account
    value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)  # NULL = not calculated
    unit: Mapped[str] = mapped_column(String(8))  # INR | RATIO | COUNT | MONTHS
    status: Mapped[FactAvailability] = mapped_column(enum_column(FactAvailability))
    formula: Mapped[str] = mapped_column(Text)
    inputs: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)  # each input with value, status, sources
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)  # change, direction, series...
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # why not calculated / why limited
    explanation: Mapped[str] = mapped_column(Text)  # deterministic, template-based
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)  # source documents and pages
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONType)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class FinancialHealthIndicator(Base):
    __tablename__ = "financial_health_indicators"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    dimension: Mapped[HealthDimension] = mapped_column(enum_column(HealthDimension))
    period_key: Mapped[str] = mapped_column(String(64), index=True)
    period_kind: Mapped[HealthPeriodKind] = mapped_column(enum_column(HealthPeriodKind))
    indicator: Mapped[HealthIndicator] = mapped_column(enum_column(HealthIndicator))
    rule: Mapped[str] = mapped_column(Text)  # the rule that produced the indicator
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)  # metrics used: id, value, status
    explanation: Mapped[str] = mapped_column(Text)
    thresholds_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
