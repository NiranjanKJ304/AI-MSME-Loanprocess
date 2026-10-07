from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, JSONType, utcnow
from app.models._types import enum_column
from app.models.enums import (
    DOCUMENT_TYPE_CODES,
    ClassificationMethod,
    DocumentStatus,
    DocumentType,
)

if TYPE_CHECKING:
    from app.models.application import Application


class Document(Base):
    """Registry entry for every uploaded file - created even for invalid/duplicate uploads."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detected_mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(1024))

    document_type: Mapped[DocumentType] = mapped_column(
        enum_column(DocumentType), default=DocumentType.UNKNOWN
    )
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_method: Mapped[ClassificationMethod | None] = mapped_column(
        enum_column(ClassificationMethod), nullable=True
    )
    classification_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    # When an officer sets the type manually, re-processing keeps it instead of re-classifying.
    type_overridden: Mapped[bool] = mapped_column(default=False)

    document_status: Mapped[DocumentStatus] = mapped_column(
        enum_column(DocumentStatus), default=DocumentStatus.UPLOADED
    )
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    file_validation: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    extraction_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    processing_run: Mapped[int] = mapped_column(Integer, default=0)
    raw_extraction_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status_reasons: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)

    uploaded_at: Mapped[datetime] = mapped_column(default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    application: Mapped[Application] = relationship(back_populates="documents")

    @property
    def document_code(self) -> str:
        return f"{DOCUMENT_TYPE_CODES[self.document_type]}-{self.sequence_no:03d}"

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of_id is not None
