from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import (
    DocumentType,
    ExtractionMethod,
    ExtractionStatus,
    FieldValidationStatus,
    RowKind,
    RowStatus,
    TableType,
    TextSource,
)


class FieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    field_name: str
    label: str | None
    value_type: str
    raw_value: str | None
    normalized_value: str | None
    confidence: float
    is_missing: bool
    is_required: bool
    source_page: int | None
    source_location: dict[str, Any] | None
    source_snippet: str | None
    source_table_id: uuid.UUID | None
    source_row_index: int | None
    extraction_method: ExtractionMethod
    extraction_status: ExtractionStatus
    validation_status: FieldValidationStatus
    warnings: list[str] | None
    alternatives: list[dict[str, Any]] | None
    processing_run: int


class RowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    row_index: int
    page_number: int
    bbox: list[float] | None
    row_kind: RowKind
    raw_cells: list[str | None]
    parsed: dict[str, Any] | None
    status: RowStatus
    confidence: float | None
    errors: list[str] | None


class TableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    table_index: int
    page_number: int
    page_end: int | None
    table_type: TableType
    title: str | None
    extraction_method: str
    header: list[str | None] | None
    column_mapping: dict[str, Any] | None
    row_count: int
    rows_failed: int
    rows_needs_review: int
    confidence: float | None
    warnings: list[str] | None
    rows: list[RowOut] = []


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_number: int
    width: float | None
    height: float | None
    text_source: TextSource
    char_count: int
    is_scanned: bool
    ocr_confidence: float | None
    image_quality: dict[str, Any] | None
    text_quality: dict[str, Any] | None = None
    warnings: list[dict[str, Any]] | None
    errors: list[dict[str, Any]] | None = None


class PageTextOut(PageOut):
    raw_text: str
    blocks: list[dict[str, Any]] | None = None


class ExtractionOut(BaseModel):
    document_id: uuid.UUID
    document_code: str
    document_type: DocumentType
    processing_run: int
    confidence: float | None
    fields: list[FieldOut]
    tables: list[TableOut]
    pages: list[PageOut]
    warnings: list[str]
    summary: dict[str, Any]
    structured: dict[str, Any]


class ProvenanceOut(BaseModel):
    field: FieldOut
    document: dict[str, Any]
    page: dict[str, Any] | None
    table: dict[str, Any] | None
    row: dict[str, Any] | None
    validation_results: list[dict[str, Any]]
    explanation: str
    extracted_at: datetime
