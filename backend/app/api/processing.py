from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_document_or_404, pipeline_view
from app.config import get_settings
from app.database import SessionLocal, get_db
from app.document_ai.extractors import schema_catalog
from app.llm import get_llm_provider
from app.document_ai.ocr import get_ocr_engine
from app.models import ProcessingJob
from app.models import enums as E
from app.pipeline.orchestrator import AlreadyProcessing, process_document
from app.requirements.document_requirements import get_policy
from app.schemas.processing import DocumentStatusOut, JobOut
from app.validation.reconciliation import variance_thresholds

router = APIRouter(prefix="/api", tags=["processing"])


def _bg_process(document_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        process_document(db, document_id)
    except AlreadyProcessing:
        pass
    finally:
        db.close()


@router.post("/documents/{document_id}/process")
def process(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    background: bool = Query(False, description="Return immediately and process in the background"),
    db: Session = Depends(get_db),
):
    """(Re-)run the full pipeline. Derived data is replaced, never duplicated."""
    doc = get_document_or_404(db, document_id)
    if background:
        background_tasks.add_task(_bg_process, doc.id)
        return {"scheduled": True, "document_id": str(doc.id)}
    try:
        job = process_document(db, doc.id)
    except AlreadyProcessing as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.refresh(job)
    return JobOut.model_validate(job)


@router.get("/documents/{document_id}/status", response_model=DocumentStatusOut)
def status(document_id: uuid.UUID, db: Session = Depends(get_db)):
    doc = get_document_or_404(db, document_id)
    pipeline, job, upload = pipeline_view(db, doc)
    return DocumentStatusOut(
        document_id=doc.id,
        document_code=doc.document_code,
        document_status=doc.document_status,
        processing_run=doc.processing_run,
        error_message=doc.error_message,
        status_reasons=doc.status_reasons,
        pipeline=pipeline,
        latest_job=job,
        upload_validation=upload,
    )


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/meta")
def meta():
    s = get_settings()
    policy = get_policy()
    return {
        "disclaimer": "Prototype decision-support system for document processing. It does not approve or "
                      "reject loans.",
        "enums": {
            "applicant_types": [e.value for e in E.ApplicantType],
            "loan_types": [e.value for e in E.LoanType],
            "document_types": [e.value for e in E.DocumentType],
            "document_statuses": [e.value for e in E.DocumentStatus],
            "pipeline_stages": [e.value for e in E.PIPELINE_STAGES],
        },
        "thresholds": {
            "classification_confidence": s.classification_confidence_threshold,
            "field_confidence": s.field_confidence_threshold,
            "balance_tolerance": s.balance_tolerance,
            "name_match": s.name_match_threshold,
            "variance": variance_thresholds(),
        },
        "engines": {
            "ocr": {"engine": get_ocr_engine().name, "available": get_ocr_engine().available()},
            "llm": {"provider": get_llm_provider().name, "available": get_llm_provider().available()},
        },
        "requirements_policy": policy.model_dump(mode="json"),
        "schemas": schema_catalog(),
    }
