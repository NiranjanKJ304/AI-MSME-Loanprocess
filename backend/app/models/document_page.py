from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, JSONType
from app.models._types import enum_column
from app.models.enums import TextSource


class DocumentPage(Base):
    """Raw layer: per-page text exactly as extracted (text layer or OCR), with word boxes."""

    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number", name="uq_page_per_document"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)  # 1-based
    width: Mapped[float | None] = mapped_column(Float, nullable=True)
    height: Mapped[float | None] = mapped_column(Float, nullable=True)
    text_source: Mapped[TextSource] = mapped_column(enum_column(TextSource))
    raw_text: Mapped[str] = mapped_column(Text, default="")
    # [{"text": str, "bbox": [x0, y0, x1, y1], "conf": float|None}]
    words: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    is_scanned: Mapped[bool] = mapped_column(default=False)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    image_quality: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    # [{"text", "bbox", "type": "text"|"image"}] - layout blocks (native) or OCR lines
    blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, nullable=True)
    # why this page was read the way it was: native chars, garbage ratio, image coverage, decision
    text_quality: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    warnings: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, nullable=True)
    errors: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, nullable=True)
