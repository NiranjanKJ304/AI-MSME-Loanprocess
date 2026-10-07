from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, JSONType
from app.models._types import enum_column
from app.models.enums import RowKind, RowStatus, TableType


class ExtractedTable(Base):
    """A table found in a document. raw rows are kept verbatim; parsing lives in the rows."""

    __tablename__ = "extracted_tables"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    table_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    table_type: Mapped[TableType] = mapped_column(enum_column(TableType))
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(64))
    bbox: Mapped[list[float] | None] = mapped_column(JSONType, nullable=True)
    header: Mapped[list[str | None] | None] = mapped_column(JSONType, nullable=True)
    column_mapping: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    raw_rows: Mapped[list[list[str | None]]] = mapped_column(JSONType)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    rows_failed: Mapped[int] = mapped_column(Integer, default=0)
    rows_needs_review: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    warnings: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)

    rows: Mapped[list[ExtractedTableRow]] = relationship(
        back_populates="table",
        order_by="ExtractedTableRow.row_index",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ExtractedTableRow(Base):
    """Every raw table row is persisted with a status - unparseable rows are kept as FAILED."""

    __tablename__ = "extracted_table_rows"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extracted_tables.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    row_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int] = mapped_column(Integer)
    bbox: Mapped[list[float] | None] = mapped_column(JSONType, nullable=True)
    row_kind: Mapped[RowKind] = mapped_column(enum_column(RowKind))
    raw_cells: Mapped[list[str | None]] = mapped_column(JSONType)
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[RowStatus] = mapped_column(enum_column(RowStatus))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    errors: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)

    table: Mapped[ExtractedTable] = relationship(back_populates="rows")
