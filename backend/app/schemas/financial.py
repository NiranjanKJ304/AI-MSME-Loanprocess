from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

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


class PeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    period_key: str
    period_type: PeriodType
    start_date: date
    end_date: date
    fiscal_year: str | None
    label: str
    source_expressions: list[dict[str, Any]] | None


class FactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    metric: str
    category: str
    measure_type: MeasureType
    value: Decimal | None
    raw_value: str | None
    unit: str
    currency: str
    scale_applied: Decimal | None
    period_id: uuid.UUID | None
    period_type: PeriodType | None
    period_start: date | None
    period_end: date | None
    as_of_date: date | None
    source_kind: FactSourceKind
    source_document_id: uuid.UUID | None
    source_document_type: DocumentType | None
    source_field_id: uuid.UUID | None
    source_field_name: str | None
    confidence: float | None
    extraction_status: ExtractionStatus | None
    availability: FactAvailability
    provenance: dict[str, Any]
    derivation: dict[str, Any] | None
    notes: list[str] | None
    created_at: datetime


class ConflictOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    metric: str
    period_id: uuid.UUID | None
    fact_a_id: uuid.UUID
    fact_b_id: uuid.UUID
    value_a: Decimal
    value_b: Decimal
    source_a: dict[str, Any]
    source_b: dict[str, Any]
    difference: Decimal
    difference_pct: float
    tolerance: float
    status: ConflictStatus
    created_at: datetime


class BankTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_document_id: uuid.UUID
    source_row_id: uuid.UUID | None
    sequence: int
    account_number: str | None
    transaction_date: date | None
    value_date: date | None
    description: str | None
    reference: str | None
    debit: Decimal | None
    credit: Decimal | None
    amount: Decimal | None
    balance: Decimal | None
    direction: TxnDirection
    category: TxnCategory
    source_page: int | None
    confidence: float | None
    row_status: RowStatus | None
    provenance: dict[str, Any]
