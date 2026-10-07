"""Transaction intelligence layer (projection over canonical bank transactions; rebuilt per run).

  TransactionClassification  one per canonical BankTransaction: category, business nature,
                             counterparty, evidence, links (reversal / transfer pairs), exclusion
  RecurringPattern           detected recurring counterparty / category patterns
  CashflowMonthly            monthly cash-flow buckets per statement and for all statements
  CashflowMetric             transaction-derived cash-flow metrics (no risk scoring)
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
from app.models.enums import (
    BusinessNature,
    ClassificationStatus,
    CounterpartyType,
    FactAvailability,
    RecurrenceFrequency,
    TxnClass,
    TxnGroup,
)

MONEY = Numeric(20, 2)


class RecurringPattern(Base):
    __tablename__ = "recurring_patterns"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    group_key: Mapped[str] = mapped_column(String(255))
    direction: Mapped[str] = mapped_column(String(8))
    counterparty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pattern_type: Mapped[str] = mapped_column(String(40))  # SALARY | RENT | EMI | SUBSCRIPTION | CUSTOMER_RECEIPTS ...
    frequency: Mapped[RecurrenceFrequency] = mapped_column(enum_column(RecurrenceFrequency))
    average_amount: Mapped[Decimal] = mapped_column(MONEY)
    min_amount: Mapped[Decimal] = mapped_column(MONEY)
    max_amount: Mapped[Decimal] = mapped_column(MONEY)
    amount_cv: Mapped[float] = mapped_column(Float)
    occurrences: Mapped[int] = mapped_column(Integer)
    distinct_months: Mapped[int] = mapped_column(Integer)
    first_seen: Mapped[date] = mapped_column(Date)
    last_seen: Mapped[date] = mapped_column(Date)
    median_interval_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    transaction_ids: Mapped[list[str]] = mapped_column(JSONType)
    document_ids: Mapped[list[str]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TransactionClassification(Base):
    __tablename__ = "transaction_classifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bank_transactions.id", ondelete="CASCADE"), unique=True, index=True)
    category: Mapped[TxnClass] = mapped_column(enum_column(TxnClass), index=True)
    category_group: Mapped[TxnGroup] = mapped_column(enum_column(TxnGroup))
    nature: Mapped[BusinessNature] = mapped_column(enum_column(BusinessNature))
    status: Mapped[ClassificationStatus] = mapped_column(enum_column(ClassificationStatus))
    confidence: Mapped[float] = mapped_column(Float)
    raw_counterparty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_counterparty: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    counterparty_type: Mapped[CounterpartyType] = mapped_column(enum_column(CounterpartyType))
    channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    references: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    vpa: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)
    candidates: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, nullable=True)
    linked_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bank_transactions.id", ondelete="SET NULL"), nullable=True)
    link_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    excluded_from_aggregates: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recurring_pattern_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recurring_patterns.id", ondelete="SET NULL"), nullable=True)
    rules_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class CashflowMonthly(Base):
    __tablename__ = "cashflow_monthly"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(  # NULL = all statements combined
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True)
    month: Mapped[str] = mapped_column(String(7))
    month_start: Mapped[date] = mapped_column(Date)
    month_end: Mapped[date] = mapped_column(Date)
    days_in_month: Mapped[int] = mapped_column(Integer)
    days_covered: Mapped[int | None] = mapped_column(Integer, nullable=True)

    business_inflow: Mapped[Decimal] = mapped_column(MONEY)
    business_revenue_inflow: Mapped[Decimal] = mapped_column(MONEY)
    business_outflow: Mapped[Decimal] = mapped_column(MONEY)
    financing_inflow: Mapped[Decimal] = mapped_column(MONEY)
    financing_outflow: Mapped[Decimal] = mapped_column(MONEY)
    transfer_inflow: Mapped[Decimal] = mapped_column(MONEY)
    transfer_outflow: Mapped[Decimal] = mapped_column(MONEY)
    cash_deposits: Mapped[Decimal] = mapped_column(MONEY)
    cash_withdrawals: Mapped[Decimal] = mapped_column(MONEY)
    personal_inflow: Mapped[Decimal] = mapped_column(MONEY)
    personal_outflow: Mapped[Decimal] = mapped_column(MONEY)
    other_inflow: Mapped[Decimal] = mapped_column(MONEY)
    other_outflow: Mapped[Decimal] = mapped_column(MONEY)
    unknown_inflow: Mapped[Decimal] = mapped_column(MONEY)
    unknown_outflow: Mapped[Decimal] = mapped_column(MONEY)
    excluded_inflow: Mapped[Decimal] = mapped_column(MONEY)
    excluded_outflow: Mapped[Decimal] = mapped_column(MONEY)
    total_inflow: Mapped[Decimal] = mapped_column(MONEY)
    total_outflow: Mapped[Decimal] = mapped_column(MONEY)
    net_operating_cash_flow: Mapped[Decimal] = mapped_column(MONEY)
    low_confidence_business_inflow: Mapped[Decimal] = mapped_column(MONEY)
    low_confidence_business_outflow: Mapped[Decimal] = mapped_column(MONEY)

    txn_count: Mapped[int] = mapped_column(Integer)
    business_txn_count: Mapped[int] = mapped_column(Integer)
    unknown_txn_count: Mapped[int] = mapped_column(Integer)
    low_confidence_txn_count: Mapped[int] = mapped_column(Integer)
    excluded_txn_count: Mapped[int] = mapped_column(Integer)
    classification_coverage_count: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_coverage_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    availability: Mapped[FactAvailability] = mapped_column(enum_column(FactAvailability))
    partial_reasons: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class CashflowMetric(Base):
    __tablename__ = "cashflow_metrics"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    metric: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)  # NULL = not available / series
    unit: Mapped[str] = mapped_column(String(8))  # INR | RATIO | COUNT
    availability: Mapped[FactAvailability] = mapped_column(enum_column(FactAvailability))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    months_used: Mapped[int] = mapped_column(Integer)
    months_total: Mapped[int] = mapped_column(Integer)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONType)  # monthly aggregate ids used
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
