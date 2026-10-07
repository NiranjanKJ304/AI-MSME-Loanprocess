"""Repayment-capacity analysis.

  RepaymentLoanTerms        proposed loan terms supplied by an officer / user (one active set per
                            application; changes are audited). Never assumed or defaulted.
  RepaymentAnalysis         one per application (rebuilt): repayment calculation and schedule, existing
                            debt evidence, data quality, outcome, assumptions, provenance
  RepaymentCapacityMetric   historical / forecast / statement capacity metrics (cash flow, debt service,
                            DSCR ...) with formula, inputs, status and provenance
  RepaymentScenario         BASE / REVENUE_DOWN / EXPENSE_UP / COMBINED_STRESS per basis

Descriptive only: no risk score, approval, rejection or credit recommendation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, JSONType, utcnow
from app.models._types import enum_column
from app.models.enums import CapacityOutcome, FactAvailability, GraceTreatment, RepaymentFrequency

MONEY = Numeric(20, 2)


class RepaymentLoanTerms(Base):
    __tablename__ = "repayment_loan_terms"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), unique=True, index=True)
    requested_amount: Mapped[Decimal] = mapped_column(MONEY)
    annual_interest_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4))  # percent per year
    tenure_months: Mapped[int] = mapped_column(Integer)
    repayment_frequency: Mapped[RepaymentFrequency] = mapped_column(enum_column(RepaymentFrequency))
    grace_period_months: Mapped[int] = mapped_column(Integer, default=0)
    grace_period_treatment: Mapped[GraceTreatment | None] = mapped_column(enum_column(GraceTreatment), nullable=True)
    revenue_down_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # stress overrides
    expense_up_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    provided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="USER_PROVIDED")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class RepaymentAnalysis(Base):
    __tablename__ = "repayment_analyses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    loan_terms_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("repayment_loan_terms.id", ondelete="SET NULL"), nullable=True)
    engine_version: Mapped[str] = mapped_column(String(64))
    config_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[FactAvailability] = mapped_column(enum_column(FactAvailability))
    outcome: Mapped[CapacityOutcome] = mapped_column(enum_column(CapacityOutcome))
    outcome_reasons: Mapped[list[str]] = mapped_column(JSONType)
    loan_terms: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)  # snapshot used
    repayment: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)  # formula, schedule, totals
    existing_debt: Mapped[dict[str, Any]] = mapped_column(JSONType)  # evidence, uncertain items
    by_period: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)
    data_quality: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)
    health_context: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)
    assumptions: Mapped[list[str]] = mapped_column(JSONType)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class RepaymentCapacityMetric(Base):
    __tablename__ = "repayment_capacity_metrics"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repayment_analyses.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    basis: Mapped[str] = mapped_column(String(16))  # HISTORICAL | FORECAST | STATEMENT
    metric: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(128))
    period: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. 2024-04..2025-03 / FY2024-25
    value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)  # NULL = not calculated
    unit: Mapped[str] = mapped_column(String(8))  # INR | TIMES
    status: Mapped[FactAvailability] = mapped_column(enum_column(FactAvailability))
    formula: Mapped[str] = mapped_column(Text)
    inputs: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class RepaymentScenario(Base):
    __tablename__ = "repayment_scenarios"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repayment_analyses.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    scenario: Mapped[str] = mapped_column(String(24))  # BASE | REVENUE_DOWN | EXPENSE_UP | COMBINED_STRESS
    basis: Mapped[str] = mapped_column(String(16))  # HISTORICAL | FORECAST
    assumption: Mapped[str] = mapped_column(Text)
    business_inflow: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # monthly
    business_outflow: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    cash_available: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    existing_debt_service: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    proposed_debt_service: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    total_debt_service: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    dscr: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
    post_debt_service_cash_flow: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    status: Mapped[FactAvailability] = mapped_column(enum_column(FactAvailability))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
