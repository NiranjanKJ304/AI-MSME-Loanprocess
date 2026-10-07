from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import ClassificationMethod, DocumentStatus, DocumentType


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    document_code: str
    sequence_no: int
    filename: str
    mime_type: str | None
    detected_mime_type: str | None
    file_size: int
    sha256: str
    document_type: DocumentType
    classification_confidence: float | None
    classification_method: ClassificationMethod | None
    type_overridden: bool
    document_status: DocumentStatus
    page_count: int | None
    duplicate_of_id: uuid.UUID | None
    is_duplicate: bool
    extraction_confidence: float | None
    processing_run: int
    status_reasons: list[str] | None
    uploaded_at: datetime
    processed_at: datetime | None
    error_message: str | None


class DocumentDetail(DocumentOut):
    file_validation: dict[str, Any] | None
    classification_evidence: dict[str, Any] | None
    raw_extraction_key: str | None


class UploadResponse(BaseModel):
    documents: list[DocumentOut]
    accepted: int
    rejected: int
    processing_scheduled: bool


class DocumentTypeOverride(BaseModel):
    document_type: DocumentType
    reprocess: bool = True
