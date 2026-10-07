from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import (
    CheckStatus,
    DocumentStatus,
    JobStatus,
    JobType,
    ProcessingStage,
    Severity,
    StageStatus,
    ValidationScope,
)


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stage: ProcessingStage
    status: StageStatus
    records_processed: int
    records_failed: int
    warnings_count: int
    warnings: list[str] | None
    error: str | None
    details: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    document_id: uuid.UUID | None
    job_type: JobType
    status: JobStatus
    run_number: int
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    stages: list[StageOut] = []


class DocumentStatusOut(BaseModel):
    document_id: uuid.UUID
    document_code: str
    document_status: DocumentStatus
    processing_run: int
    error_message: str | None
    status_reasons: list[str] | None
    pipeline: list[dict[str, Any]]
    latest_job: JobOut | None
    upload_validation: JobOut | None


class ValidationResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    field_id: uuid.UUID | None
    row_id: uuid.UUID | None
    scope: ValidationScope
    rule_code: str
    field_name: str | None
    status: CheckStatus
    severity: Severity
    message: str
    expected: str | None
    actual: str | None
    source_page: int | None
    details: dict[str, Any] | None
    created_at: datetime


class ReconciliationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    check_code: str
    check_group: str
    status: CheckStatus
    severity: Severity
    confidence: float | None
    message: str
    sources: list[dict[str, Any]]
    details: dict[str, Any] | None
    created_at: datetime


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID | None
    document_id: uuid.UUID | None
    stage: str | None
    action: str
    status: str
    actor: str
    timestamp: datetime
    error: str | None
    source: dict[str, Any] | None
    details: dict[str, Any] | None
