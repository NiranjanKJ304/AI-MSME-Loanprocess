"""Structured logging + persistent audit trail helpers."""

from __future__ import annotations

import json
import logging
import sys
import uuid
from typing import Any

from sqlalchemy.orm import Session

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "ctx", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger("msme")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"msme.{name}")


_audit_logger = get_logger("audit")


def audit(
    db: Session,
    *,
    action: str,
    status: str,
    application_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    stage: str | None = None,
    error: str | None = None,
    source: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    actor: str = "system",
) -> None:
    """Append an immutable audit-log entry (flushed with the caller's transaction)."""
    from app.models.audit_log import AuditLog

    entry = AuditLog(
        application_id=application_id,
        document_id=document_id,
        stage=stage,
        action=action,
        status=status,
        error=error,
        source=source,
        details=details,
        actor=actor,
    )
    db.add(entry)
    _audit_logger.info(
        action,
        extra={
            "ctx": {
                "application_id": str(application_id) if application_id else None,
                "document_id": str(document_id) if document_id else None,
                "stage": stage,
                "status": status,
                "error": error,
            }
        },
    )
