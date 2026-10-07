from __future__ import annotations

import secrets
import shutil
import tempfile
import uuid
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_application_or_404, pipeline_view
from app.config import get_settings
from app.database import SessionLocal, get_db, utcnow
from app.ingestion.registry import register_upload
from app.models import (
    Application,
    AuditLog,
    Document,
    ExtractedField,
    ExtractedTableRow,
    ReconciliationResult,
    ValidationResult,
)
from app.models.enums import (
    CheckStatus,
    DocumentStatus,
    FieldValidationStatus,
    RowKind,
    RowStatus,
    Severity,
)
from app.pipeline.orchestrator import AlreadyProcessing, process_document, reconcile_application
from app.requirements.document_requirements import evaluate_completeness
from app.schemas.application import ApplicationCreate, ApplicationListItem, ApplicationOut
from app.schemas.document import DocumentOut, UploadResponse
from app.schemas.processing import AuditOut, JobOut, ReconciliationOut, ValidationResultOut
from app.utils.logging import audit, get_logger

router = APIRouter(prefix="/api/applications", tags=["applications"])
log = get_logger("api")


def _new_application_number() -> str:
    return f"MSME-{utcnow():%Y%m%d}-{secrets.token_hex(3).upper()}"


@router.post("", response_model=ApplicationOut, status_code=201)
def create_application(payload: ApplicationCreate, db: Session = Depends(get_db)):
    app = Application(application_number=_new_application_number(), **payload.model_dump())
    db.add(app)
    db.flush()
    audit(db, action="APPLICATION_CREATED", status="CREATED", application_id=app.id,
          details=payload.model_dump(mode="json"))
    db.commit()
    return app


@router.get("", response_model=list[ApplicationListItem])
def list_applications(db: Session = Depends(get_db)):
    apps = db.scalars(select(Application).order_by(Application.created_at.desc())).all()
    counts = db.execute(
        select(Document.application_id, Document.document_status, func.count())
        .where(Document.duplicate_of_id.is_(None))
        .group_by(Document.application_id, Document.document_status)
    ).all()
    agg: dict[uuid.UUID, Counter] = {}
    for app_id, status, n in counts:
        agg.setdefault(app_id, Counter())[status] += n
    out = []
    for a in apps:
        c = agg.get(a.id, Counter())
        item = ApplicationListItem.model_validate(a)
        item.document_count = sum(c.values())
        item.documents_needing_review = c.get(DocumentStatus.NEEDS_REVIEW, 0)
        item.documents_failed = c.get(DocumentStatus.FAILED, 0) + c.get(DocumentStatus.INVALID, 0)
        out.append(item)
    return out


@router.get("/{application_id}", response_model=ApplicationOut)
def get_application(application_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_application_or_404(db, application_id)


# --------------------------------------------------------------------------- documents
def _process_in_background(document_ids: list[uuid.UUID]) -> None:
    db = SessionLocal()
    try:
        for doc_id in document_ids:
            try:
                process_document(db, doc_id)
            except AlreadyProcessing:
                continue
            except Exception:  # recorded in stage records; keep going with the next document
                log.exception("background processing failed", extra={"ctx": {"document_id": str(doc_id)}})
                db.rollback()
    finally:
        db.close()


@router.post("/{application_id}/documents", response_model=UploadResponse, status_code=201)
def upload_documents(
    application_id: uuid.UUID,
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    auto_process: bool = Query(True, description="Run the processing pipeline after upload"),
    db: Session = Depends(get_db),
):
    application = get_application_or_404(db, application_id)
    tmp_dir = Path(get_settings().storage_dir) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    docs: list[Document] = []
    for upload in files:
        with tempfile.NamedTemporaryFile(dir=tmp_dir, delete=False) as tmp:
            shutil.copyfileobj(upload.file, tmp, length=1024 * 1024)
            tmp_path = Path(tmp.name)
        try:
            doc = register_upload(
                db, application, filename=upload.filename or "unnamed",
                declared_mime=upload.content_type, tmp_path=tmp_path,
            )
            docs.append(doc)
        finally:
            tmp_path.unlink(missing_ok=True)
    valid_ids = [d.id for d in docs if d.document_status == DocumentStatus.VALID]
    scheduled = bool(auto_process and valid_ids)
    if scheduled:
        background.add_task(_process_in_background, valid_ids)
    elif docs:
        # still refresh reconciliation/completeness so invalid uploads are reflected
        reconcile_application(db, application.id)
    return UploadResponse(
        documents=[DocumentOut.model_validate(d) for d in docs],
        accepted=len(valid_ids),
        rejected=len(docs) - len(valid_ids),
        processing_scheduled=scheduled,
    )


@router.get("/{application_id}/documents")
def list_documents(application_id: uuid.UUID, db: Session = Depends(get_db)):
    get_application_or_404(db, application_id)
    docs = db.scalars(select(Document).where(Document.application_id == application_id)
                      .order_by(Document.sequence_no)).all()
    out = []
    for d in docs:
        pipeline, _, _ = pipeline_view(db, d)
        out.append({**DocumentOut.model_validate(d).model_dump(mode="json"), "pipeline": pipeline})
    return out


@router.post("/{application_id}/process", response_model=list[JobOut])
def process_all(application_id: uuid.UUID, only_pending: bool = True, db: Session = Depends(get_db)):
    """Process every not-yet-processed (or failed) document synchronously."""
    from app.pipeline.orchestrator import process_application

    get_application_or_404(db, application_id)
    return process_application(db, application_id, only_pending=only_pending)


@router.post("/{application_id}/reconcile", response_model=JobOut)
def run_reconciliation(application_id: uuid.UUID, db: Session = Depends(get_db)):
    get_application_or_404(db, application_id)
    return reconcile_application(db, application_id)


# --------------------------------------------------------------------------- results
@router.get("/{application_id}/validation")
def get_validation(
    application_id: uuid.UUID,
    include_info: bool = False,
    db: Session = Depends(get_db),
):
    get_application_or_404(db, application_id)
    stmt = select(ValidationResult, Document).join(Document, Document.id == ValidationResult.document_id).where(
        ValidationResult.application_id == application_id)
    if not include_info:
        stmt = stmt.where(ValidationResult.severity != Severity.INFO)
    rows = db.execute(stmt.order_by(Document.sequence_no, ValidationResult.created_at)).all()
    results = []
    for vr, doc in rows:
        item = ValidationResultOut.model_validate(vr).model_dump(mode="json")
        item.update(document_code=doc.document_code, filename=doc.filename, document_type=doc.document_type.value)
        results.append(item)
    by_sev = Counter(r["severity"] for r in results)
    by_status = Counter(r["status"] for r in results)
    # include file-level problems (invalid / duplicate uploads) explicitly
    invalid_docs = db.scalars(select(Document).where(
        Document.application_id == application_id,
        Document.document_status.in_([DocumentStatus.INVALID, DocumentStatus.FAILED]))).all()
    return {
        "summary": {"total": len(results), "by_severity": by_sev, "by_status": by_status,
                    "invalid_or_failed_documents": len(invalid_docs)},
        "file_issues": [
            {"document_id": str(d.id), "document_code": d.document_code, "filename": d.filename,
             "status": d.document_status.value, "error": d.error_message, "reasons": d.status_reasons}
            for d in invalid_docs
        ],
        "results": results,
    }


@router.get("/{application_id}/reconciliation")
def get_reconciliation(application_id: uuid.UUID, db: Session = Depends(get_db)):
    get_application_or_404(db, application_id)
    rows = db.scalars(select(ReconciliationResult).where(ReconciliationResult.application_id == application_id)
                      .order_by(ReconciliationResult.check_group, ReconciliationResult.check_code)).all()
    results = [ReconciliationOut.model_validate(r) for r in rows]
    return {
        "summary": dict(Counter(r.status.value for r in rows)),
        "note": "INCONSISTENCY means the documents disagree and need officer review; it is not a fraud finding.",
        "results": results,
    }


@router.get("/{application_id}/completeness")
def get_completeness(application_id: uuid.UUID, db: Session = Depends(get_db)):
    app = get_application_or_404(db, application_id)
    docs = list(db.scalars(select(Document).where(Document.application_id == application_id)))
    return evaluate_completeness(app, docs)


@router.get("/{application_id}/audit", response_model=list[AuditOut])
def get_audit(application_id: uuid.UUID, limit: int = Query(500, le=5000), db: Session = Depends(get_db)):
    get_application_or_404(db, application_id)
    return db.scalars(select(AuditLog).where(AuditLog.application_id == application_id)
                      .order_by(AuditLog.timestamp.desc()).limit(limit)).all()


@router.get("/{application_id}/overview")
def get_overview(application_id: uuid.UUID, db: Session = Depends(get_db)):
    """Officer dashboard aggregate. Deliberately contains no approval score."""
    app = get_application_or_404(db, application_id)
    docs = list(db.scalars(select(Document).where(Document.application_id == application_id)
                           .order_by(Document.sequence_no)))
    originals = [d for d in docs if d.duplicate_of_id is None]
    doc_ids = [d.id for d in docs]
    fields = list(db.scalars(select(ExtractedField).where(ExtractedField.application_id == application_id)))
    rows = list(db.scalars(select(ExtractedTableRow).where(ExtractedTableRow.document_id.in_(doc_ids)))) if doc_ids else []
    vrs = list(db.scalars(select(ValidationResult).where(ValidationResult.application_id == application_id)))
    recon = list(db.scalars(select(ReconciliationResult).where(ReconciliationResult.application_id == application_id)))
    completeness = evaluate_completeness(app, docs)
    threshold = get_settings().field_confidence_threshold

    present = [f for f in fields if not f.is_missing]
    with_page = [f for f in present if f.source_page]
    with_bbox = [f for f in present if f.source_location and (f.source_location.get("bbox") or f.source_location.get("components"))]
    problem_vrs = [v for v in vrs if v.status in (CheckStatus.FAIL, CheckStatus.INCONSISTENCY, CheckStatus.NEEDS_REVIEW)
                   and v.severity != Severity.INFO]
    txn_rows = [r for r in rows if r.row_kind == RowKind.TRANSACTION]
    document_rows = []
    for d in docs:
        pipeline, job, _ = pipeline_view(db, d)
        document_rows.append({**DocumentOut.model_validate(d).model_dump(mode="json"), "pipeline": pipeline})

    return {
        "application": ApplicationOut.model_validate(app).model_dump(mode="json"),
        "documents_received": {
            "total_uploaded": len(docs),
            "unique": len(originals),
            "duplicates": len(docs) - len(originals),
            "by_status": dict(Counter(d.document_status.value for d in originals)),
            "by_type": dict(Counter(d.document_type.value for d in originals)),
        },
        "documents_missing": {
            "required_missing": completeness["summary"]["missing_document_types"],
            "officer_to_confirm": [i["document_type"] for i in completeness["items"] if i["state"] == "OFFICER_TO_CONFIRM"],
            "completeness_pct": completeness["summary"]["completeness_pct"],
        },
        "extraction_quality": {
            "average_document_confidence": round(sum(d.extraction_confidence or 0 for d in originals if d.extraction_confidence is not None)
                                                 / max(1, sum(1 for d in originals if d.extraction_confidence is not None)), 3),
            "fields_total": len(fields),
            "fields_extracted": len(present),
            "fields_missing": len(fields) - len(present),
            "required_fields_missing": sum(1 for f in fields if f.is_missing and f.is_required),
            "low_confidence_fields": sum(1 for f in present if f.confidence < threshold),
            "fields_by_status": dict(Counter(f.validation_status.value for f in fields)),
            "table_rows_total": len(rows),
            "transactions": len(txn_rows),
            "rows_failed": sum(1 for r in rows if r.status == RowStatus.FAILED),
            "rows_needs_review": sum(1 for r in rows if r.status == RowStatus.NEEDS_REVIEW),
        },
        "validation_issues": {
            "total": len(problem_vrs),
            "by_severity": dict(Counter(v.severity.value for v in problem_vrs)),
            "by_rule": dict(Counter(v.rule_code for v in problem_vrs)),
            "invalid_fields": sum(1 for f in fields if f.validation_status == FieldValidationStatus.INVALID),
        },
        "cross_document": {
            "checks": len(recon),
            "inconsistencies": sum(1 for r in recon if r.status == CheckStatus.INCONSISTENCY),
            "passed": sum(1 for r in recon if r.status == CheckStatus.PASS),
            "insufficient_data": sum(1 for r in recon if r.status == CheckStatus.INSUFFICIENT_DATA),
            "items": [ReconciliationOut.model_validate(r).model_dump(mode="json") for r in recon
                      if r.status == CheckStatus.INCONSISTENCY],
        },
        "processing_errors": [
            {"document_id": str(d.id), "document_code": d.document_code, "filename": d.filename,
             "status": d.document_status.value, "error": d.error_message, "reasons": d.status_reasons}
            for d in docs if d.document_status in (DocumentStatus.FAILED, DocumentStatus.INVALID)
        ],
        "evidence_coverage": {
            "fields_with_source_page_pct": round(100 * len(with_page) / len(present), 1) if present else None,
            "fields_with_location_pct": round(100 * len(with_bbox) / len(present), 1) if present else None,
            "reconciliation_checks_with_data_pct": round(
                100 * sum(1 for r in recon if r.status != CheckStatus.INSUFFICIENT_DATA) / len(recon), 1) if recon else None,
        },
        "completeness": completeness,
        "documents": document_rows,
    }
