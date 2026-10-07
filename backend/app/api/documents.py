from __future__ import annotations

import uuid
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_document_or_404
from app.database import get_db
from app.ingestion.storage import get_storage
from app.ingestion.file_validator import IMAGE_MIMES, PDF
from app.models import (
    DocumentPage,
    ExtractedField,
    ExtractedTable,
    ValidationResult,
)
from app.models.enums import ClassificationMethod, DocumentType, RowKind, RowStatus
from app.pipeline.orchestrator import AlreadyProcessing, process_document
from app.schemas.document import DocumentDetail, DocumentTypeOverride
from app.schemas.extraction import (
    ExtractionOut,
    FieldOut,
    PageOut,
    PageTextOut,
    ProvenanceOut,
    RowOut,
    TableOut,
)
from app.schemas.processing import ValidationResultOut
from app.utils.logging import audit

router = APIRouter(prefix="/api", tags=["documents"])


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def get_document(document_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_document_or_404(db, document_id)


@router.patch("/documents/{document_id}/type", response_model=DocumentDetail)
def override_type(document_id: uuid.UUID, payload: DocumentTypeOverride, db: Session = Depends(get_db)):
    """Officer sets the document type (e.g. after NEEDS_REVIEW). Recorded in the audit log."""
    doc = get_document_or_404(db, document_id)
    previous = doc.document_type
    doc.document_type = payload.document_type
    doc.type_overridden = True
    doc.classification_method = ClassificationMethod.MANUAL
    doc.classification_confidence = 1.0
    audit(db, action="DOCUMENT_TYPE_OVERRIDDEN", status="OK", application_id=doc.application_id,
          document_id=doc.id, actor="officer",
          details={"from": previous.value, "to": payload.document_type.value})
    db.commit()
    if payload.reprocess:
        try:
            process_document(db, doc.id)
        except AlreadyProcessing as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        db.refresh(doc)
    return doc


@router.get("/documents/{document_id}/file")
def download_original(document_id: uuid.UUID, db: Session = Depends(get_db)):
    doc = get_document_or_404(db, document_id)
    path = get_storage().local_path(doc.storage_key)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Original file missing from storage")
    return FileResponse(path, media_type=doc.detected_mime_type or "application/octet-stream",
                        filename=doc.filename)


@router.get("/documents/{document_id}/pages/{page_number}/image")
def page_image(document_id: uuid.UUID, page_number: int, zoom: float = Query(1.5, ge=0.5, le=4),
               db: Session = Depends(get_db)):
    """Rendered page image for the evidence viewer. Bounding boxes are in page coordinates:
    for PDFs multiply by `zoom`; for images coordinates are pixels at zoom 1."""
    doc = get_document_or_404(db, document_id)
    path = get_storage().local_path(doc.storage_key)
    mime = doc.detected_mime_type
    if mime == PDF:
        import pymupdf as fitz

        with fitz.open(path) as pdf:
            if pdf.needs_pass and not pdf.authenticate(""):
                raise HTTPException(status_code=422, detail="PDF is password-protected")
            if not 1 <= page_number <= pdf.page_count:
                raise HTTPException(status_code=404, detail="Page out of range")
            pix = pdf.load_page(page_number - 1).get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            return Response(pix.tobytes("png"), media_type="image/png")
    if mime in IMAGE_MIMES:
        import cv2

        ok, frames = cv2.imreadmulti(str(path))
        if not ok or not frames:
            from app.document_ai.image_quality import decode_image_file

            img = decode_image_file(str(path))
            frames = [img] if img is not None else []
        if not 1 <= page_number <= len(frames):
            raise HTTPException(status_code=404, detail="Page out of range")
        ok, buf = cv2.imencode(".png", frames[page_number - 1])
        return Response(buf.tobytes(), media_type="image/png")
    raise HTTPException(status_code=404, detail="No page image for this file type")


@router.get("/documents/{document_id}/extraction", response_model=ExtractionOut)
def get_extraction(document_id: uuid.UUID, db: Session = Depends(get_db)):
    doc = get_document_or_404(db, document_id)
    fields = db.scalars(select(ExtractedField).where(ExtractedField.document_id == doc.id)
                        .order_by(ExtractedField.is_missing, ExtractedField.field_name)).all()
    tables = db.scalars(select(ExtractedTable).where(ExtractedTable.document_id == doc.id)
                        .order_by(ExtractedTable.table_index)).all()
    pages = db.scalars(select(DocumentPage).where(DocumentPage.document_id == doc.id)
                       .order_by(DocumentPage.page_number)).all()
    rows = [r for t in tables for r in t.rows]
    present = [f for f in fields if not f.is_missing]

    structured: dict = {f.field_name: f.normalized_value for f in present}
    if doc.document_type == DocumentType.BANK_STATEMENT:
        structured["transactions"] = [
            {k: (r.parsed or {}).get(k) for k in ("date", "value_date", "description", "reference", "debit", "credit", "balance")}
            | {"row_id": str(r.id), "status": r.status.value, "page": r.page_number}
            for r in rows if r.row_kind == RowKind.TRANSACTION
        ]
    warnings = []
    for f in fields:
        for w in f.warnings or []:
            warnings.append(f"{f.field_name}: {w}")
    for t in tables:
        for w in t.warnings or []:
            warnings.append(f"table {t.table_index}: {w}")
    for p in pages:
        for w in p.warnings or []:
            warnings.append(f"page {p.page_number}: {w.get('code')}: {w.get('message')}")
    return ExtractionOut(
        document_id=doc.id,
        document_code=doc.document_code,
        document_type=doc.document_type,
        processing_run=doc.processing_run,
        confidence=doc.extraction_confidence,
        fields=[FieldOut.model_validate(f) for f in fields],
        tables=[TableOut.model_validate(t) for t in tables],
        pages=[PageOut.model_validate(p) for p in pages],
        warnings=warnings,
        summary={
            "fields_total": len(fields),
            "fields_extracted": len(present),
            "fields_missing": len(fields) - len(present),
            "required_missing": [f.field_name for f in fields if f.is_missing and f.is_required],
            "fields_by_status": dict(Counter(f.validation_status.value for f in fields)),
            "tables": len(tables),
            "rows_total": len(rows),
            "rows_by_status": dict(Counter(r.status.value for r in rows)),
            "rows_by_kind": dict(Counter(r.row_kind.value for r in rows)),
            "transactions": sum(1 for r in rows if r.row_kind == RowKind.TRANSACTION),
            "rows_failed": sum(1 for r in rows if r.status == RowStatus.FAILED),
        },
        structured=structured,
    )


@router.get("/documents/{document_id}/validation", response_model=list[ValidationResultOut])
def get_document_validation(document_id: uuid.UUID, db: Session = Depends(get_db)):
    doc = get_document_or_404(db, document_id)
    return db.scalars(select(ValidationResult).where(ValidationResult.document_id == doc.id)
                      .order_by(ValidationResult.severity, ValidationResult.rule_code)).all()


@router.get("/documents/{document_id}/pages", response_model=list[PageTextOut])
def get_pages(document_id: uuid.UUID, db: Session = Depends(get_db)):
    """Raw layer: verbatim per-page text as extracted (text layer or OCR)."""
    doc = get_document_or_404(db, document_id)
    return db.scalars(select(DocumentPage).where(DocumentPage.document_id == doc.id)
                      .order_by(DocumentPage.page_number)).all()


@router.get("/documents/{document_id}/raw")
def get_raw_extraction(document_id: uuid.UUID, db: Session = Depends(get_db)):
    """The raw extraction artefact (text, word boxes, raw table cells) stored beside the original."""
    import json

    doc = get_document_or_404(db, document_id)
    if not doc.raw_extraction_key:
        raise HTTPException(status_code=404, detail="Document has not been processed yet")
    with get_storage().open(doc.raw_extraction_key) as f:
        return json.loads(f.read().decode("utf-8"))


@router.get("/fields/{field_id}/provenance", response_model=ProvenanceOut)
def field_provenance(field_id: uuid.UUID, db: Session = Depends(get_db)):
    """Answers 'where did this value come from?'"""
    f = db.get(ExtractedField, field_id)
    if f is None:
        raise HTTPException(status_code=404, detail="Field not found")
    doc = get_document_or_404(db, f.document_id)
    page = None
    if f.source_page:
        p = db.scalars(select(DocumentPage).where(DocumentPage.document_id == doc.id,
                                                  DocumentPage.page_number == f.source_page)).first()
        if p:
            page = {"page_number": p.page_number, "width": p.width, "height": p.height,
                    "text_source": p.text_source.value, "ocr_confidence": p.ocr_confidence}
    table = row = None
    if f.source_table_id:
        t = db.get(ExtractedTable, f.source_table_id)
        if t:
            table = {"id": str(t.id), "table_index": t.table_index, "page_number": t.page_number,
                     "table_type": t.table_type.value, "title": t.title, "method": t.extraction_method}
            match = next((r for r in t.rows if r.row_index == f.source_row_index), None)
            if match:
                row = RowOut.model_validate(match).model_dump(mode="json")
    vrs = db.scalars(select(ValidationResult).where(ValidationResult.field_id == f.id)).all()

    if f.is_missing:
        explanation = f"{f.field_name}: not found in {doc.filename} ({doc.document_code}); no value recorded."
    else:
        where = [f"Source: {doc.filename} ({doc.document_code})"]
        if f.source_page:
            where.append(f"Page: {f.source_page}")
        if table:
            where.append(f"Table: {table['title'] or table['table_index']}" + (f", row {f.source_row_index}" if f.source_row_index is not None else ""))
        if f.label:
            where.append(f"Label: {f.label}")
        where.append(f"Method: {f.extraction_method.value}")
        where.append(f"Confidence: {f.confidence:.0%}")
        explanation = f"{f.field_name}: {f.raw_value} (normalised: {f.normalized_value})\n" + "\n".join(where)
    return ProvenanceOut(
        field=FieldOut.model_validate(f),
        document={"id": str(doc.id), "document_code": doc.document_code, "filename": doc.filename,
                  "document_type": doc.document_type.value, "sha256": doc.sha256,
                  "processing_run": doc.processing_run},
        page=page, table=table, row=row,
        validation_results=[ValidationResultOut.model_validate(v).model_dump(mode="json") for v in vrs],
        explanation=explanation,
        extracted_at=f.created_at,
    )
