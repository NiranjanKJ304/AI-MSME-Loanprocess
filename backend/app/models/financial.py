"""Canonical financial data layer.

A projection rebuilt from extraction output (extracted fields + parsed table rows) for one
application. Nothing here replaces or edits extraction data; every row links back to it.

  FinancialPeriod     canonical period (FY / quarter / month / custom range)
  FinancialFact       one value of one metric for one period from one source (never merged)
  FinancialConflict   two facts of the same metric+period that disagree (recorded, not resolved)
  BankTransaction     canonical transaction from a parsed bank-statement row
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, JSONType, utcnow
from app.models._types import enum_column
from app.models.enums import (
    ConflictStatus,
    DocumentType,
    ExtractionStatus,
    FactAvailability,
    FactSourceKind,
    MeasureType,
    PeriodType,
    RowStatus,
    TxnCategory,
    TxnDirection,
)

MONEY = Numeric(20, 2)


class FinancialPeriod(Base):
    __tablename__ = "financial_periods"
    __table_args__ = (UniqueConstraint("application_id", "period_key", name="uq_period_per_application"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    period_key: Mapped[str] = mapped_column(String(64))  # FY2024-25 | FY2024-25-Q1 | 2025-03 | 2024-04-01..2024-10-31
    period_type: Mapped[PeriodType] = mapped_column(enum_column(PeriodType))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    fiscal_year: Mapped[str | None] = mapped_column(String(16), nullable=True)  # FY the period ends in
    label: Mapped[str] = mapped_column(String(64))
    # raw period expressions that normalised to this period, with their sources
    source_expressions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, nullable=True)


class FinancialFact(Base):
    __tablename__ = "financial_facts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    metric: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(32))  # BUSINESS | PROFITABILITY | BALANCE_SHEET | BANKING | TAX
    measure_type: Mapped[MeasureType] = mapped_column(enum_column(MeasureType))
    value: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # NULL = not available (never 0)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)  # as printed in the source
    unit: Mapped[str] = mapped_column(String(16), default="INR")
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    scale_applied: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)  # e.g. 100000 for lakhs

    period_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("financial_periods.id", ondelete="SET NULL"), nullable=True, index=True)
    period_type: Mapped[PeriodType | None] = mapped_column(enum_column(PeriodType), nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # STOCK measures

    source_kind: Mapped[FactSourceKind] = mapped_column(enum_column(FactSourceKind))
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True)
    source_document_type: Mapped[DocumentType | None] = mapped_column(enum_column(DocumentType), nullable=True)
    source_field_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extracted_fields.id", ondelete="SET NULL"), nullable=True)
    source_field_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_status: Mapped[ExtractionStatus | None] = mapped_column(enum_column(ExtractionStatus), nullable=True)
    availability: Mapped[FactAvailability] = mapped_column(enum_column(FactAvailability))
    # canonical fact -> extracted field -> document -> page -> bbox (+ derivation components)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONType)
    derivation: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    notes: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class FinancialConflict(Base):
    __tablename__ = "financial_conflicts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    metric: Mapped[str] = mapped_column(String(64), index=True)
    period_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=True)
    fact_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("financial_facts.id", ondelete="CASCADE"))
    fact_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("financial_facts.id", ondelete="CASCADE"))
    value_a: Mapped[Decimal] = mapped_column(MONEY)
    value_b: Mapped[Decimal] = mapped_column(MONEY)
    source_a: Mapped[dict[str, Any]] = mapped_column(JSONType)
    source_b: Mapped[dict[str, Any]] = mapped_column(JSONType)
    difference: Mapped[Decimal] = mapped_column(MONEY)  # value_a - value_b
    difference_pct: Mapped[float] = mapped_column(Float)  # |a-b| / max(|a|,|b|)
    tolerance: Mapped[float] = mapped_column(Float)
    status: Mapped[ConflictStatus] = mapped_column(enum_column(ConflictStatus), default=ConflictStatus.CONFLICTING)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class BankTransaction(Base):
    __tablename__ = "bank_transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    source_row_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extracted_table_rows.id", ondelete="SET NULL"), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer)  # order within the statement
    account_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transaction_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    value_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    debit: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    credit: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # signed: credit +, debit -
    balance: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    direction: Mapped[TxnDirection] = mapped_column(enum_column(TxnDirection))
    category: Mapped[TxnCategory] = mapped_column(enum_column(TxnCategory))
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    row_status: Mapped[RowStatus | None] = mapped_column(enum_column(RowStatus), nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONType)
