"""Shared API helpers: lookups and read-model builders."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, Document, ProcessingJob
from app.models.enums import JobType, PIPELINE_STAGES, StageStatus
from app.schemas.processing import JobOut


def get_application_or_404(db: Session, application_id: uuid.UUID) -> Application:
    app = db.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application {application_id} not found")
    return app


def get_document_or_404(db: Session, document_id: uuid.UUID) -> Document:
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    return doc


def latest_job(db: Session, document_id: uuid.UUID, job_type: JobType) -> ProcessingJob | None:
    return db.scalars(
        select(ProcessingJob)
        .where(ProcessingJob.document_id == document_id, ProcessingJob.job_type == job_type)
        .order_by(ProcessingJob.started_at.desc(), ProcessingJob.run_number.desc())
        .limit(1)
    ).first()


def pipeline_view(db: Session, doc: Document) -> tuple[list[dict[str, Any]], JobOut | None, JobOut | None]:
    """One entry per pipeline stage, taken from the latest processing job (or upload validation)."""
    proc = latest_job(db, doc.id, JobType.DOCUMENT_PROCESSING)
    upload = latest_job(db, doc.id, JobType.UPLOAD_VALIDATION)
    source = proc or upload
    by_stage = {}
    if source is not None:
        for s in source.stages:
            by_stage[s.stage] = s
    pipeline = []
    for stage in PIPELINE_STAGES:
        rec = by_stage.get(stage)
        pipeline.append({
            "stage": stage.value,
            "status": rec.status.value if rec else StageStatus.PENDING.value,
            "records_processed": rec.records_processed if rec else 0,
            "records_failed": rec.records_failed if rec else 0,
            "warnings": rec.warnings_count if rec else 0,
            "error": rec.error if rec else None,
        })
    return (
        pipeline,
        JobOut.model_validate(proc) if proc else None,
        JobOut.model_validate(upload) if upload else None,
    )
