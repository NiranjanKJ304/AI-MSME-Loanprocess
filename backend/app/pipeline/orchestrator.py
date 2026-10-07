"""Processing pipeline orchestrator.

Per document:
  FILE_VALIDATION -> CLASSIFICATION -> TEXT_EXTRACTION -> TABLE_EXTRACTION -> FIELD_EXTRACTION
  -> FIELD_VALIDATION -> DOCUMENT_VALIDATION
Per application (run after every document, and on demand):
  -> CROSS_DOCUMENT_RECONCILIATION -> FINANCIAL_NORMALIZATION -> TRANSACTION_INTELLIGENCE
  -> FINANCIAL_HEALTH -> FINANCIAL_FORECAST -> REPAYMENT_CAPACITY -> COMPLETENESS_CHECK
  -> FINAL_DOCUMENT_STATUS

Idempotency: every run replaces the document's derived data (pages, tables, rows, fields,
validation results) inside the run, so re-processing never duplicates records. Processing
jobs/stage records and the audit log are append-only history.

Note on ordering: CLASSIFICATION needs the document text, so it parses the file in memory;
TEXT_EXTRACTION/TABLE_EXTRACTION then persist that raw layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import utcnow
from app.document_ai.classifier import ClassificationResult, DocumentClassifier
from app.document_ai.extractor import BaseExtractor, ExtractionContext, ExtractionResult, TableResult
from app.document_ai.extractors import get_extractor
from app.document_ai.pdf_parser import parse_document
from app.document_ai.table_extractor import extract_tables
from app.document_ai.types import ParsedDocument, RawTable
from app.financials.builder import build_financial_layer
from app.financial_health.service import build_financial_health
from app.forecasting.service import build_forecasts
from app.repayment.service import build_repayment_capacity
from app.transaction_intel.service import build_transaction_intelligence
from app.ingestion.registry import apply_file_validation
from app.ingestion.storage import get_storage, raw_extraction_key
from app.models import (
    Application,
    Document,
    DocumentPage,
    ExtractedField,
    ExtractedTable,
    ExtractedTableRow,
    ProcessingJob,
    ReconciliationResult,
    ValidationResult,
)
from app.models.enums import (
    ApplicationStatus,
    CheckStatus,
    ClassificationMethod,
    DocumentStatus,
    FieldValidationStatus,
    JobStatus,
    JobType,
    ProcessingStage,
    RowStatus,
    Severity,
    TextSource,
    ValidationScope,
)
from app.pipeline.recorder import JobRecorder, StageFailed, StageOutcome
from app.requirements.document_requirements import evaluate_completeness
from app.utils.logging import audit
from app.validation.document_validation import validate_document
from app.validation.field_validation import validate_field
from app.validation.reconciliation import reconcile

DOC_STAGES = [
    ProcessingStage.FILE_VALIDATION,
    ProcessingStage.CLASSIFICATION,
    ProcessingStage.TEXT_EXTRACTION,
    ProcessingStage.TABLE_EXTRACTION,
    ProcessingStage.FIELD_EXTRACTION,
    ProcessingStage.FIELD_VALIDATION,
    ProcessingStage.DOCUMENT_VALIDATION,
]
APP_STAGES = [
    ProcessingStage.CROSS_DOCUMENT_RECONCILIATION,
    ProcessingStage.FINANCIAL_NORMALIZATION,
    ProcessingStage.TRANSACTION_INTELLIGENCE,
    ProcessingStage.FINANCIAL_HEALTH,
    ProcessingStage.FINANCIAL_FORECAST,
    ProcessingStage.REPAYMENT_CAPACITY,
    ProcessingStage.COMPLETENESS_CHECK,
    ProcessingStage.FINAL_DOCUMENT_STATUS,
]
RECONCILABLE = (DocumentStatus.PROCESSED, DocumentStatus.NEEDS_REVIEW)


class AlreadyProcessing(Exception):
    pass


@dataclass
class _Run:
    document: Document
    parsed: ParsedDocument | None = None
    raw_tables: list[RawTable] = field(default_factory=list)
    table_warnings: list[str] = field(default_factory=list)
    classification: ClassificationResult | None = None
    extractor: BaseExtractor | None = None
    ctx: ExtractionContext | None = None
    tables: list[TableResult] = field(default_factory=list)
    table_ids: dict[int, uuid.UUID] = field(default_factory=dict)
    result: ExtractionResult | None = None


# --------------------------------------------------------------------------- helpers
def clear_derived_data(db: Session, document_id: uuid.UUID) -> None:
    db.execute(delete(ValidationResult).where(ValidationResult.document_id == document_id))
    db.execute(delete(ExtractedField).where(ExtractedField.document_id == document_id))
    db.execute(delete(ExtractedTableRow).where(ExtractedTableRow.document_id == document_id))
    db.execute(delete(ExtractedTable).where(ExtractedTable.document_id == document_id))
    db.execute(delete(DocumentPage).where(DocumentPage.document_id == document_id))


def _check_not_running(db: Session, document: Document) -> None:
    stale_before = utcnow() - timedelta(minutes=get_settings().stale_job_minutes)
    running = db.scalars(
        select(ProcessingJob).where(
            ProcessingJob.document_id == document.id,
            ProcessingJob.job_type == JobType.DOCUMENT_PROCESSING,
            ProcessingJob.status == JobStatus.RUNNING,
        )
    ).all()
    for job in running:
        started = job.started_at if job.started_at.tzinfo else job.started_at.replace(tzinfo=stale_before.tzinfo)
        if started > stale_before:
            raise AlreadyProcessing(f"Document {document.id} is already being processed (job {job.id})")
        job.status = JobStatus.FAILED
        job.error = "STALE_JOB: abandoned run superseded by a new run"
        job.finished_at = utcnow()
    db.commit()


def _persist_validation(db: Session, document: Document, outcome, *, scope: ValidationScope,
                        field_obj: ExtractedField | None = None, row: ExtractedTableRow | None = None,
                        field_name: str | None = None, page: int | None = None) -> None:
    db.add(ValidationResult(
        application_id=document.application_id,
        document_id=document.id,
        field_id=field_obj.id if field_obj else None,
        row_id=row.id if row else None,
        scope=scope,
        rule_code=outcome.rule_code,
        field_name=field_name or (field_obj.field_name if field_obj else None),
        status=outcome.status,
        severity=outcome.severity,
        message=outcome.message,
        expected=outcome.expected,
        actual=outcome.actual,
        source_page=page if page is not None else (field_obj.source_page if field_obj else (row.page_number if row else None)),
        details=outcome.details or None,
    ))


# --------------------------------------------------------------------------- stages
def _stage_file_validation(db: Session, run: _Run, out: StageOutcome) -> None:
    apply_file_validation(db, run.document, out)
    db.flush()


def _stage_classification(db: Session, run: _Run, out: StageOutcome) -> None:
    doc = run.document
    path = get_storage().local_path(doc.storage_key)
    run.parsed = parse_document(path, doc.detected_mime_type or "")
    run.raw_tables, run.table_warnings = extract_tables(path, run.parsed)
    out.records_processed = 1
    if doc.type_overridden:
        result = ClassificationResult(
            doc.document_type, 1.0, ClassificationMethod.MANUAL,
            [{"signal": "manual", "weight": 1.0, "detail": "document type set by officer"}],
        )
    else:
        result = DocumentClassifier().classify(doc.filename, run.parsed, run.raw_tables)
    run.classification = result
    doc.document_type = result.document_type
    doc.classification_confidence = result.confidence
    doc.classification_method = result.method
    doc.classification_evidence = result.as_dict()
    for w in result.warnings:
        out.warn(w)
    out.details = {"document_type": result.document_type.value, "confidence": result.confidence,
                   "method": result.method.value, "candidates": result.candidates}
    audit(db, action="DOCUMENT_CLASSIFIED", status="NEEDS_REVIEW" if result.needs_review else "OK",
          application_id=doc.application_id, document_id=doc.id,
          stage=ProcessingStage.CLASSIFICATION.value,
          details={"document_type": result.document_type.value, "confidence": result.confidence,
                   "method": result.method.value})


def _stage_text(db: Session, run: _Run, out: StageOutcome) -> None:
    doc, parsed = run.document, run.parsed
    assert parsed is not None
    clear_derived_data(db, doc.id)
    no_text = 0
    methods: dict[str, int] = {}
    for p in parsed.pages:
        db.add(DocumentPage(
            document_id=doc.id, page_number=p.page_number, width=p.width, height=p.height,
            text_source=p.text_source, raw_text=p.raw_text, words=[w.as_dict() for w in p.words] or None,
            char_count=len(p.raw_text), is_scanned=p.is_scanned, ocr_confidence=p.ocr_confidence,
            image_quality=p.image_quality, warnings=p.warnings or None,
            blocks=p.blocks or None, text_quality=p.text_quality or None, errors=p.errors or None,
        ))
        methods[p.text_source.value] = methods.get(p.text_source.value, 0) + 1
        if p.text_source == TextSource.NONE:
            no_text += 1
        for w in p.warnings:
            out.warn(f"page {p.page_number}: {w['code']}: {w['message']}")
        for e in p.errors:
            out.warn(f"page {p.page_number}: ERROR {e['code']}: {e['message']}")
    doc.page_count = len(parsed.pages)
    # Raw extraction artefact (verbatim text, word boxes, raw table cells), stored beside the original.
    key = raw_extraction_key(doc.application_id, doc.id)
    get_storage().put_json(key, {
        "document_id": str(doc.id),
        "processing_run": doc.processing_run,
        "extraction_version": get_settings().extraction_version,
        "source_kind": parsed.source_kind,
        "pages": [{"page_number": p.page_number, "text_source": p.text_source.value, "raw_text": p.raw_text,
                   "width": p.width, "height": p.height, "words": [w.as_dict() for w in p.words],
                   "blocks": p.blocks, "text_quality": p.text_quality, "ocr_output": p.ocr_output,
                   "ocr_confidence": p.ocr_confidence, "image_quality": p.image_quality,
                   "warnings": p.warnings, "errors": p.errors} for p in parsed.pages],
        "tables": [{"index": t.index, "page_number": t.page_number, "page_end": t.page_end, "method": t.method,
                    "rows": t.rows, "row_pages": t.row_pages, "row_hints": t.row_hints,
                    "row_bboxes": [list(b) if b else None for b in t.row_bboxes], "meta": t.meta}
                   for t in run.raw_tables],
        "table_warnings": run.table_warnings,
        "classification": run.classification.as_dict() if run.classification else None,
    })
    doc.raw_extraction_key = key
    out.records_processed = len(parsed.pages)
    out.records_failed = no_text
    out.details = {"pages": len(parsed.pages), "pages_without_text": no_text, "pages_by_method": methods,
                   "raw_extraction_key": key}


def _stage_tables(db: Session, run: _Run, out: StageOutcome) -> None:
    doc = run.document
    run.extractor = get_extractor(doc.document_type)
    run.ctx = ExtractionContext(run.parsed, run.raw_tables, doc.filename)  # type: ignore[arg-type]
    run.tables = run.extractor.process_tables(run.ctx)
    for w in run.table_warnings:
        out.warn(w)
    total = failed = review = 0
    for t in run.tables:
        rows_failed = sum(1 for r in t.rows if r.status == RowStatus.FAILED)
        rows_review = sum(1 for r in t.rows if r.status == RowStatus.NEEDS_REVIEW)
        tbl = ExtractedTable(
            document_id=doc.id, table_index=t.raw.index, page_number=t.raw.page_number,
            page_end=max(t.raw.row_pages) if t.raw.row_pages else t.raw.page_number,
            table_type=t.table_type, title=t.raw.title, extraction_method=t.raw.method,
            bbox=list(t.raw.bbox) if t.raw.bbox else None,
            header=t.raw.rows[t.analysis.header_index] if t.analysis.header_index is not None else None,
            column_mapping=t.analysis.column_mapping or None, raw_rows=t.raw.rows, row_count=len(t.rows),
            rows_failed=rows_failed, rows_needs_review=rows_review, confidence=t.confidence,
            warnings=t.warnings or None,
        )
        db.add(tbl)
        db.flush()
        run.table_ids[t.raw.index] = tbl.id
        for r in t.rows:
            db.add(ExtractedTableRow(
                table_id=tbl.id, document_id=doc.id, row_index=r.row_index, page_number=r.page_number,
                bbox=list(r.bbox) if r.bbox else None, row_kind=r.kind, raw_cells=r.raw_cells,
                parsed=r.parsed, status=r.status, confidence=r.confidence, errors=r.errors or None,
            ))
        total += len(t.rows)
        failed += rows_failed
        review += rows_review
        for w in t.warnings:
            out.warn(f"table {t.raw.index} (page {t.raw.page_number}): {w}")
    if review:
        out.warn(f"{review} row(s) need review")
    out.records_processed = total
    out.records_failed = failed
    out.details = {"tables": len(run.tables), "rows": total, "rows_failed": failed, "rows_needs_review": review}


def _stage_fields(db: Session, run: _Run, out: StageOutcome) -> None:
    doc = run.document
    assert run.extractor is not None and run.ctx is not None
    result = run.extractor.extract(run.ctx, run.tables)
    run.result = result
    doc.extraction_confidence = result.confidence
    missing_required = 0
    for c in result.fields:
        db.add(ExtractedField(
            document_id=doc.id, application_id=doc.application_id, field_name=c.name, label=c.label,
            value_type=c.value_type, raw_value=c.raw_value, normalized_value=c.normalized_value,
            confidence=c.confidence, is_missing=c.is_missing, is_required=c.required,
            source_page=c.page, source_location=c.source_location(), source_snippet=c.snippet,
            source_table_id=run.table_ids.get(c.table_index) if c.table_index is not None else None,
            source_row_index=c.row_index, extraction_method=c.method, extraction_status=c.extraction_status,
            validation_status=FieldValidationStatus.PENDING, warnings=c.warnings or None,
            alternatives=c.alternatives or None, processing_run=doc.processing_run,
        ))
        if c.is_missing and c.required:
            missing_required += 1
    for w in result.warnings:
        out.warn(w)
    out.records_processed = sum(1 for c in result.fields if not c.is_missing)
    out.records_failed = missing_required
    by_status: dict[str, int] = {}
    for c in result.fields:
        by_status[c.extraction_status.value] = by_status.get(c.extraction_status.value, 0) + 1
    out.details = {"fields_found": out.records_processed,
                   "fields_missing": sum(1 for c in result.fields if c.is_missing),
                   "required_missing": missing_required, "confidence": result.confidence,
                   "extraction_status": result.status, "fields_by_extraction_status": by_status,
                   "structured": result.structured if len(str(result.structured)) < 20000 else "(truncated)"}


def _stage_field_validation(db: Session, run: _Run, out: StageOutcome) -> None:
    doc = run.document
    specs = {s.name: s for s in (run.extractor.field_specs if run.extractor else [])}
    fields = db.scalars(select(ExtractedField).where(ExtractedField.document_id == doc.id)).all()
    counts: dict[str, int] = {}
    for f in fields:
        spec = specs.get(f.field_name)
        status, outcomes = validate_field(f, non_negative=bool(spec and spec.non_negative))
        f.validation_status = status
        counts[status.value] = counts.get(status.value, 0) + 1
        for o in outcomes:
            if o.is_problem:
                _persist_validation(db, doc, o, scope=ValidationScope.FIELD, field_obj=f)
    out.records_processed = len(fields)
    out.records_failed = counts.get("INVALID", 0) + sum(1 for f in fields if f.is_missing and f.is_required)
    if counts.get("NEEDS_REVIEW"):
        out.warn(f"{counts['NEEDS_REVIEW']} field(s) need review (low confidence / conflicts / LLM-sourced)")
    out.details = {"by_status": counts}


def _stage_document_validation(db: Session, run: _Run, out: StageOutcome) -> None:
    doc = run.document
    fields = {f.field_name: f for f in db.scalars(select(ExtractedField).where(ExtractedField.document_id == doc.id))}
    tables = list(db.scalars(select(ExtractedTable).where(ExtractedTable.document_id == doc.id)))
    pages = list(db.scalars(select(DocumentPage).where(DocumentPage.document_id == doc.id)))
    report = validate_document(doc, fields, tables, pages)
    problems = 0
    for o in report.outcomes:
        if o.outcome.status == CheckStatus.SKIPPED:
            continue
        _persist_validation(db, doc, o.outcome,
                            scope=ValidationScope.ROW if o.row is not None else ValidationScope.DOCUMENT,
                            row=o.row, field_name=o.field_name, page=o.page)
        if o.outcome.is_problem:
            problems += 1
            if o.outcome.severity != Severity.INFO:
                out.warn(o.outcome.message)
    for t in tables:
        t.rows_failed = sum(1 for r in t.rows if r.status == RowStatus.FAILED)
        t.rows_needs_review = sum(1 for r in t.rows if r.status == RowStatus.NEEDS_REVIEW)
    out.records_processed = len(report.outcomes) + report.rows_checked
    out.records_failed = problems
    out.details = {"rules_evaluated": len(report.outcomes), "rows_balance_checked": report.rows_checked,
                   "rows_inconsistent": report.rows_inconsistent}


# --------------------------------------------------------------------------- final status
def compute_document_status(db: Session, doc: Document, recon: list[ReconciliationResult]) -> None:
    # Only documents that completed extraction get a (re)computed review status.
    if doc.document_status not in (DocumentStatus.PROCESSED, DocumentStatus.NEEDS_REVIEW):
        return
    reasons: list[str] = []
    if doc.document_type.value == "UNKNOWN":
        reasons.append("UNKNOWN_DOCUMENT_TYPE")
    if (doc.classification_confidence or 0) < get_settings().classification_confidence_threshold:
        reasons.append("LOW_CLASSIFICATION_CONFIDENCE")
    fields = db.scalars(select(ExtractedField).where(ExtractedField.document_id == doc.id)).all()
    if any(f.is_missing and f.is_required for f in fields):
        reasons.append("MISSING_REQUIRED_FIELDS")
    if any(f.validation_status == FieldValidationStatus.INVALID for f in fields):
        reasons.append("INVALID_FIELDS")
    if any(f.validation_status == FieldValidationStatus.NEEDS_REVIEW for f in fields):
        reasons.append("FIELDS_NEED_REVIEW")
    problem_results = db.scalars(select(ValidationResult).where(
        ValidationResult.document_id == doc.id,
        ValidationResult.scope.in_([ValidationScope.DOCUMENT, ValidationScope.ROW]),
        ValidationResult.status.in_([CheckStatus.FAIL, CheckStatus.INCONSISTENCY, CheckStatus.NEEDS_REVIEW]),
        ValidationResult.severity != Severity.INFO,
    )).all()
    for code in sorted({r.rule_code for r in problem_results}):
        reasons.append(f"VALIDATION:{code}")
    rows = db.scalars(select(ExtractedTableRow).where(ExtractedTableRow.document_id == doc.id)).all()
    if any(r.status in (RowStatus.FAILED, RowStatus.NEEDS_REVIEW) for r in rows):
        reasons.append("TABLE_ROWS_NEED_REVIEW")
    doc_id = str(doc.id)
    for rr in recon:
        if rr.status != CheckStatus.INCONSISTENCY:
            continue
        flagged = (rr.details or {}).get("flag_document_ids")
        involved = doc_id in flagged if flagged is not None else any(
            s.get("document_id") == doc_id for s in rr.sources)
        if involved:
            reasons.append(f"CROSS_DOCUMENT:{rr.check_code}")
    doc.status_reasons = reasons or None
    doc.document_status = DocumentStatus.NEEDS_REVIEW if reasons else DocumentStatus.PROCESSED


def refresh_application_status(db: Session, application: Application, *, required_missing: int = 0,
                               inconsistencies: int = 0) -> None:
    """PROCESSED only when every document is processed cleanly, no required document is
    missing and no cross-document inconsistency is open; otherwise NEEDS_REVIEW."""
    docs = db.scalars(select(Document).where(Document.application_id == application.id)).all()
    statuses = {d.document_status for d in docs if d.duplicate_of_id is None}
    if not docs:
        application.status = ApplicationStatus.CREATED
    elif DocumentStatus.PROCESSING in statuses:
        application.status = ApplicationStatus.PROCESSING
    elif statuses & {DocumentStatus.UPLOADED, DocumentStatus.VALID, DocumentStatus.VALIDATING}:
        application.status = ApplicationStatus.DOCUMENTS_RECEIVED
    elif (statuses & {DocumentStatus.NEEDS_REVIEW, DocumentStatus.FAILED, DocumentStatus.INVALID}
          or required_missing or inconsistencies):
        application.status = ApplicationStatus.NEEDS_REVIEW
    else:
        application.status = ApplicationStatus.PROCESSED


def _run_application_stages(db: Session, recorder: JobRecorder, application: Application,
                            current_doc: Document | None) -> None:
    holder: dict[str, Any] = {}

    def reconciliation(out: StageOutcome) -> None:
        docs = [d for d in db.scalars(select(Document).where(Document.application_id == application.id))
                if d.duplicate_of_id is None and d.document_status in RECONCILABLE]
        doc_ids = [d.id for d in docs]
        fields = list(db.scalars(select(ExtractedField).where(ExtractedField.document_id.in_(doc_ids)))) if doc_ids else []
        rows = list(db.scalars(select(ExtractedTableRow).where(ExtractedTableRow.document_id.in_(doc_ids)))) if doc_ids else []
        db.execute(delete(ReconciliationResult).where(ReconciliationResult.application_id == application.id))
        results = reconcile(application, docs, fields, rows)
        saved = []
        for r in results:
            rr = ReconciliationResult(
                application_id=application.id, check_code=r.check_code, check_group=r.check_group,
                status=r.status, severity=r.severity, confidence=r.confidence, message=r.message,
                sources=r.sources, details=r.details or None,
            )
            db.add(rr)
            saved.append(rr)
            if r.status == CheckStatus.INCONSISTENCY:
                out.warn(f"{r.check_code}: {r.message}")
                audit(db, action="CROSS_DOCUMENT_INCONSISTENCY", status="INCONSISTENCY",
                      application_id=application.id, stage=ProcessingStage.CROSS_DOCUMENT_RECONCILIATION.value,
                      details={"check": r.check_code, "message": r.message},
                      source={"documents": sorted({s.get("document_code") for s in r.sources if s.get("document_code")})})
        holder["recon"] = saved
        out.records_processed = len(results)
        out.records_failed = sum(1 for r in results if r.status == CheckStatus.INCONSISTENCY)
        out.details = {"documents_compared": len(docs),
                       "by_status": {s.value: sum(1 for r in results if r.status == s) for s in CheckStatus}}

    def financial_normalization(out: StageOutcome) -> None:
        """Rebuild the canonical financial layer (facts / periods / conflicts / bank transactions)."""
        report = build_financial_layer(db, application)
        out.records_processed = report.facts + report.transactions
        out.details = {k: v for k, v in report.__dict__.items() if k != "warnings"}
        for w in report.warnings:
            out.warn(w)
        if report.conflicts:
            out.warn(f"{report.conflicts} financial fact conflict(s) recorded (not resolved)")

    def transaction_intelligence(out: StageOutcome) -> None:
        """Classify canonical bank transactions, detect patterns, aggregate monthly cash flow."""
        report = build_transaction_intelligence(db, application)
        out.records_processed = report.transactions
        out.details = {k: v for k, v in report.__dict__.items() if k != "warnings"}
        for w in report.warnings[:20]:
            out.warn(w)
        if report.unknown:
            out.warn(f"{report.unknown} transaction(s) left UNKNOWN (insufficient evidence)")

    def financial_health(out: StageOutcome) -> None:
        """Descriptive financial health per period (no scoring, no decision)."""
        report = build_financial_health(db, application)
        out.records_processed = report.metrics
        out.details = {k: v for k, v in report.__dict__.items() if k != "warnings"}
        for w in report.warnings[:20]:
            out.warn(w)
        if report.conflicting:
            out.warn(f"{report.conflicting} health metric(s) not calculated because their sources conflict")

    def financial_forecast(out: StageOutcome) -> None:
        """Deterministic forecasts of revenue and monthly business cash flow (projections only)."""
        report = build_forecasts(db, application)
        out.records_processed = report.forecasts
        out.details = {k: v for k, v in report.__dict__.items() if k != "warnings"}
        for w in report.warnings:
            out.warn(w)

    def repayment_capacity(out: StageOutcome) -> None:
        """Descriptive repayment capacity; calculated only when proposed loan terms were provided."""
        report = build_repayment_capacity(db, application)
        out.records_processed = report.metrics
        out.details = {k: v for k, v in report.__dict__.items() if k != "warnings"}

    def completeness(out: StageOutcome) -> None:
        docs = list(db.scalars(select(Document).where(Document.application_id == application.id)))
        report = evaluate_completeness(application, docs)
        s = report["summary"]
        holder["required_missing"] = s["required_missing"]
        out.records_processed = len(report["items"])
        out.records_failed = s["required_missing"]
        if s["required_missing"]:
            out.warn("Missing required documents: " + ", ".join(s["missing_document_types"]))
        if s["officer_to_confirm"]:
            out.warn(f"{s['officer_to_confirm']} conditional requirement(s) need officer confirmation")
        out.details = {"summary": s}

    def final_status(out: StageOutcome) -> None:
        recon = holder.get("recon", [])
        docs = list(db.scalars(select(Document).where(Document.application_id == application.id)))
        for d in docs:
            compute_document_status(db, d, recon)
        refresh_application_status(
            db, application,
            required_missing=holder.get("required_missing", 0),
            inconsistencies=sum(1 for r in recon if r.status == CheckStatus.INCONSISTENCY),
        )
        if current_doc is not None:
            out.details = {"document_status": current_doc.document_status.value,
                           "reasons": current_doc.status_reasons or []}
            if current_doc.document_status in (DocumentStatus.NEEDS_REVIEW, DocumentStatus.FAILED,
                                               DocumentStatus.INVALID):
                out.warn(f"Document status {current_doc.document_status.value}: "
                         + ", ".join(current_doc.status_reasons or [current_doc.error_message or ""]))
        out.details = {**out.details, "application_status": application.status.value}
        out.records_processed = len(docs)
        audit(db, action="STATUS_FINALISED", status=application.status.value,
              application_id=application.id, document_id=current_doc.id if current_doc else None,
              stage=ProcessingStage.FINAL_DOCUMENT_STATUS.value,
              details={"document_status": current_doc.document_status.value if current_doc else None})

    for stage, fn in ((ProcessingStage.CROSS_DOCUMENT_RECONCILIATION, reconciliation),
                      (ProcessingStage.FINANCIAL_NORMALIZATION, financial_normalization),
                      (ProcessingStage.TRANSACTION_INTELLIGENCE, transaction_intelligence),
                      (ProcessingStage.FINANCIAL_HEALTH, financial_health),
                      (ProcessingStage.FINANCIAL_FORECAST, financial_forecast),
                      (ProcessingStage.REPAYMENT_CAPACITY, repayment_capacity),
                      (ProcessingStage.COMPLETENESS_CHECK, completeness),
                      (ProcessingStage.FINAL_DOCUMENT_STATUS, final_status)):
        try:
            recorder.run_stage(stage, fn)
        except StageFailed:
            continue  # recorded; later stages still run so status stays truthful


# --------------------------------------------------------------------------- entry points
def process_document(db: Session, document_id: uuid.UUID) -> ProcessingJob:
    doc = db.get(Document, document_id)
    if doc is None:
        raise LookupError(f"Document {document_id} not found")
    _check_not_running(db, doc)
    application = db.get(Application, doc.application_id)
    assert application is not None

    doc.processing_run = (doc.processing_run or 0) + 1
    doc.document_status = DocumentStatus.PROCESSING
    doc.error_message = None
    doc.status_reasons = None
    application.status = ApplicationStatus.PROCESSING
    audit(db, action="PROCESSING_STARTED", status="RUNNING", application_id=doc.application_id,
          document_id=doc.id, details={"processing_run": doc.processing_run})
    db.commit()

    recorder = JobRecorder(db, application_id=doc.application_id, document_id=doc.id,
                           job_type=JobType.DOCUMENT_PROCESSING, run_number=doc.processing_run)
    run = _Run(document=doc)
    stage_fns = {
        ProcessingStage.FILE_VALIDATION: _stage_file_validation,
        ProcessingStage.CLASSIFICATION: _stage_classification,
        ProcessingStage.TEXT_EXTRACTION: _stage_text,
        ProcessingStage.TABLE_EXTRACTION: _stage_tables,
        ProcessingStage.FIELD_EXTRACTION: _stage_fields,
        ProcessingStage.FIELD_VALIDATION: _stage_field_validation,
        ProcessingStage.DOCUMENT_VALIDATION: _stage_document_validation,
    }
    failure: str | None = None
    for i, stage in enumerate(DOC_STAGES):
        try:
            recorder.run_stage(stage, lambda out, s=stage: stage_fns[s](db, run, out))
            if stage == ProcessingStage.FILE_VALIDATION:
                doc.document_status = DocumentStatus.PROCESSING
                db.commit()
        except StageFailed as exc:
            db.refresh(doc)
            if doc.document_status != DocumentStatus.INVALID:
                doc.document_status = DocumentStatus.FAILED
                doc.error_message = f"{stage.value} failed: {exc}"
                doc.status_reasons = [f"STAGE_FAILED:{stage.value}"]
            failure = doc.error_message
            db.commit()
            for later in DOC_STAGES[i + 1:]:
                recorder.skip_stage(later, f"skipped because {stage.value} failed")
            break

    if failure is None:
        doc.document_status = DocumentStatus.NEEDS_REVIEW  # provisional until FINAL_DOCUMENT_STATUS
        doc.processed_at = utcnow()
        db.commit()
    _run_application_stages(db, recorder, application, doc)
    job = recorder.finish(error=failure)
    audit(db, action="PROCESSING_FINISHED", status=doc.document_status.value, application_id=doc.application_id,
          document_id=doc.id, error=failure, details={"job_id": str(job.id), "job_status": job.status.value})
    db.commit()
    return job


def reconcile_application(db: Session, application_id: uuid.UUID) -> ProcessingJob:
    application = db.get(Application, application_id)
    if application is None:
        raise LookupError(f"Application {application_id} not found")
    recorder = JobRecorder(db, application_id=application.id, document_id=None,
                           job_type=JobType.APPLICATION_RECONCILIATION, run_number=1)
    _run_application_stages(db, recorder, application, None)
    job = recorder.finish()
    db.commit()
    return job


def process_application(db: Session, application_id: uuid.UUID, only_pending: bool = True) -> list[ProcessingJob]:
    docs = db.scalars(select(Document).where(Document.application_id == application_id)
                      .order_by(Document.sequence_no)).all()
    jobs = []
    for d in docs:
        if d.duplicate_of_id is not None:
            continue
        if only_pending and d.document_status not in (DocumentStatus.UPLOADED, DocumentStatus.VALID,
                                                      DocumentStatus.FAILED):
            continue
        jobs.append(process_document(db, d.id))
    return jobs
