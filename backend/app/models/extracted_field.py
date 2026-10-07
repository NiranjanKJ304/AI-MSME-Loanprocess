from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, JSONType, utcnow
from app.models._types import enum_column
from app.models.enums import ExtractionMethod, ExtractionStatus, FieldValidationStatus


class ExtractedField(Base):
    """Structured layer: one row per schema field per document - including MISSING fields.

    Raw value and normalised value are stored separately; provenance (page, bbox,
    snippet, table/row) is mandatory for every non-missing value.
    """

    __tablename__ = "extracted_fields"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    field_name: Mapped[str] = mapped_column(String(128), index=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    value_type: Mapped[str] = mapped_column(String(32), default="string")
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    is_missing: Mapped[bool] = mapped_column(default=False)
    is_required: Mapped[bool] = mapped_column(default=False)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # {"bbox": [x0,y0,x1,y1], "page_width": w, "page_height": h, "components": [...]}
    source_location: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    source_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_table_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("extracted_tables.id", ondelete="SET NULL"), nullable=True
    )
    source_row_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extraction_method: Mapped[ExtractionMethod] = mapped_column(enum_column(ExtractionMethod))
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        enum_column(ExtractionStatus), default=ExtractionStatus.EXTRACTED,
        server_default=ExtractionStatus.EXTRACTED.value,
    )
    validation_status: Mapped[FieldValidationStatus] = mapped_column(
        enum_column(FieldValidationStatus), default=FieldValidationStatus.PENDING
    )
    warnings: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    # Other candidate values seen for this field (kept, never silently dropped)
    alternatives: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, nullable=True)
    processing_run: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
