from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, JSONType, utcnow


class AuditLog(Base):
    """Append-only audit trail. Never updated or deleted by the application."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True, nullable=True
    )
    # No FK: audit entries must survive even if a document row were ever removed.
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True, nullable=True)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(128), default="system")
    timestamp: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
