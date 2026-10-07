"""Document registry: every upload becomes a Document row, valid or not.

Flow per file: hash -> store original (write-once) -> create record -> duplicate check
-> file validation. Invalid and duplicate uploads stay visible with an explicit reason.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.file_validator import FileValidationReport, validate_file
from app.ingestion.storage import get_storage, original_key
from app.models import Application, Document
from app.models.enums import (
    ApplicationStatus,
    DocumentStatus,
    JobType,
    ProcessingStage,
)
from app.pipeline.recorder import JobRecorder, StageFailed, StageOutcome
from app.utils.hashing import sha256_file
from app.utils.logging import audit

_SAFE_NAME = re.compile(r"[^\w.\- ()&]+")


def safe_filename(name: str) -> str:
    name = Path(name or "unnamed").name  # strip any client-supplied directories
    name = _SAFE_NAME.sub("_", name).strip() or "unnamed"
    return name[:255]


def find_duplicate(db: Session, document: Document) -> Document | None:
    stmt = (
        select(Document)
        .where(
            Document.application_id == document.application_id,
            Document.sha256 == document.sha256,
            Document.id != document.id,
            Document.duplicate_of_id.is_(None),
            Document.sequence_no < document.sequence_no,
        )
        .order_by(Document.sequence_no)
        .limit(1)
    )
    return db.scalars(stmt).first()


def apply_file_validation(db: Session, document: Document, outcome: StageOutcome) -> FileValidationReport | None:
    """Duplicate check + file validation. Sets VALID/INVALID; never raises for bad files."""
    document.document_status = DocumentStatus.VALIDATING
    db.flush()

    duplicate = find_duplicate(db, document)
    if duplicate is not None:
        document.duplicate_of_id = duplicate.id
        document.document_status = DocumentStatus.INVALID
        document.error_message = (
            f"DUPLICATE_DOCUMENT: identical content (SHA-256) to {duplicate.document_code} "
            f"'{duplicate.filename}' uploaded {duplicate.uploaded_at:%Y-%m-%d %H:%M}"
        )
        document.status_reasons = ["DUPLICATE_DOCUMENT"]
        document.file_validation = {
            "is_valid": False,
            "checks": [
                {"code": "DUPLICATE", "status": "FAIL", "message": document.error_message},
            ],
        }
        audit(
            db,
            action="DUPLICATE_DETECTED",
            status="INVALID",
            application_id=document.application_id,
            document_id=document.id,
            stage=ProcessingStage.FILE_VALIDATION.value,
            error=document.error_message,
            source={"duplicate_of": str(duplicate.id), "sha256": document.sha256},
        )
        outcome.records_processed = 1
        outcome.records_failed = 1
        outcome.failed = True
        outcome.error = document.error_message
        outcome.details = {"duplicate_of": str(duplicate.id)}
        return None

    path = get_storage().local_path(document.storage_key)
    report = validate_file(path, document.filename, document.mime_type)
    document.file_validation = report.as_dict()
    document.detected_mime_type = report.detected_mime
    document.page_count = report.page_count
    outcome.records_processed = 1
    outcome.details = {"checks": [c.as_dict() for c in report.checks]}
    for w in report.warnings:
        outcome.warn(f"{w.code}: {w.message}")
    if report.is_valid:
        document.document_status = DocumentStatus.VALID
        document.error_message = None
        document.status_reasons = None
    else:
        document.document_status = DocumentStatus.INVALID
        document.error_message = report.error_message()
        document.status_reasons = [c.code for c in report.errors]
        outcome.records_failed = 1
        outcome.failed = True
        outcome.error = document.error_message
    audit(
        db,
        action="FILE_VALIDATED",
        status=document.document_status.value,
        application_id=document.application_id,
        document_id=document.id,
        stage=ProcessingStage.FILE_VALIDATION.value,
        error=document.error_message,
        details={"warnings": [w.code for w in report.warnings]},
    )
    return report


def register_upload(
    db: Session,
    application: Application,
    *,
    filename: str,
    declared_mime: str | None,
    tmp_path: Path,
) -> Document:
    storage = get_storage()
    filename = safe_filename(filename)
    digest = sha256_file(tmp_path)
    size = tmp_path.stat().st_size
    ext = Path(filename).suffix.lower()
    key = storage.put_file(original_key(application.id, digest, ext), tmp_path)

    # Lock the application row while allocating the sequence number (PostgreSQL).
    app_row = db.execute(
        select(Application).where(Application.id == application.id).with_for_update()
    ).scalar_one()
    app_row.document_sequence = (app_row.document_sequence or 0) + 1
    if app_row.status == ApplicationStatus.CREATED:
        app_row.status = ApplicationStatus.DOCUMENTS_RECEIVED

    document = Document(
        application_id=application.id,
        sequence_no=app_row.document_sequence,
        filename=filename,
        mime_type=declared_mime,
        file_size=size,
        sha256=digest,
        storage_key=key,
        document_status=DocumentStatus.UPLOADED,
    )
    db.add(document)
    db.flush()
    audit(
        db,
        action="DOCUMENT_UPLOADED",
        status=DocumentStatus.UPLOADED.value,
        application_id=application.id,
        document_id=document.id,
        source={"filename": filename, "sha256": digest, "size": size, "storage_key": key},
    )
    db.commit()

    recorder = JobRecorder(
        db,
        application_id=application.id,
        document_id=document.id,
        job_type=JobType.UPLOAD_VALIDATION,
        run_number=0,
    )
    try:
        recorder.run_stage(
            ProcessingStage.FILE_VALIDATION,
            lambda outcome: apply_file_validation(db, document, outcome),
        )
    except StageFailed as exc:
        db.refresh(document)
        if document.document_status != DocumentStatus.INVALID:
            # Unexpected exception (not a bad file): surface it as a processing failure.
            document.document_status = DocumentStatus.FAILED
            document.error_message = f"FILE_VALIDATION_ERROR: {exc}"
            db.commit()
    recorder.finish()
    db.refresh(document)
    return document
