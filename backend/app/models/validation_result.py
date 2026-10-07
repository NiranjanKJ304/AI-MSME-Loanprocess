from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Float, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, JSONType, utcnow
from app.models._types import enum_column
from app.models.enums import CheckStatus, Severity, ValidationScope


class ValidationResult(Base):
    """Every rule evaluation that did not trivially pass is recorded here (passes too, for coverage)."""

    __tablename__ = "validation_results"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    field_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extracted_fields.id", ondelete="CASCADE"), nullable=True, index=True
    )
    row_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extracted_table_rows.id", ondelete="CASCADE"), nullable=True, index=True
    )
    scope: Mapped[ValidationScope] = mapped_column(enum_column(ValidationScope))
    rule_code: Mapped[str] = mapped_column(String(64), index=True)
    field_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[CheckStatus] = mapped_column(enum_column(CheckStatus))
    severity: Mapped[Severity] = mapped_column(enum_column(Severity))
    message: Mapped[str] = mapped_column(Text)
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_page: Mapped[int | None] = mapped_column(nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ReconciliationResult(Base):
    """Cross-document check outcome. Mismatches are INCONSISTENCY, never 'fraud'."""

    __tablename__ = "reconciliation_results"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    check_code: Mapped[str] = mapped_column(String(64), index=True)
    check_group: Mapped[str] = mapped_column(String(64))
    status: Mapped[CheckStatus] = mapped_column(enum_column(CheckStatus))
    severity: Mapped[Severity] = mapped_column(enum_column(Severity))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    # [{"document_id", "document_code", "filename", "document_type", "field", "value", "page", "confidence"}]
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
