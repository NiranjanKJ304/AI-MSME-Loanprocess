"""End-to-end tests through the HTTP API (upload -> pipeline -> results)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.models import (
    AuditLog,
    DocumentPage,
    ExtractedField,
    ExtractedTable,
    ExtractedTableRow,
    ProcessingJob,
    ValidationResult,
)
from app.models.enums import PIPELINE_STAGES
from scripts import synthetic_docs as S
from tests.conftest import create_app, sample_documents, upload


def docs_by_name(client, app_id):
    return {d["filename"]: d for d in client.get(f"/api/applications/{app_id}/documents").json()}


def recon_by_code(client, app_id):
    return {r["check_code"]: r for r in client.get(f"/api/applications/{app_id}/reconciliation").json()["results"]}


def test_create_application_validation(client):
    bad = client.post("/api/applications", json={"business_name": "X", "applicant_type": "NGO",
                                                 "loan_type": "TERM_LOAN", "requested_amount": -5})
    assert bad.status_code == 422
    app = create_app(client)
    assert app["status"] == "CREATED"
    assert app["application_number"].startswith("MSME-")
    assert client.get("/api/applications").json()[0]["id"] == app["id"]


def test_full_sample_application_end_to_end(client, business):
    app = create_app(client)
    res = upload(client, app["id"], sample_documents(business))
    assert res["accepted"] == 9 and res["processing_scheduled"]

    docs = docs_by_name(client, app["id"])
    types = {n: d["document_type"] for n, d in docs.items()}
    assert types["Bank_statement_FY2024-25.pdf"] == "BANK_STATEMENT"
    assert types["PL_FY2024-25.pdf"] == "PROFIT_LOSS"
    for d in docs.values():
        assert d["document_status"] in ("PROCESSED", "NEEDS_REVIEW"), (d["filename"], d["status_reasons"])
        stages = {s["stage"]: s["status"] for s in d["pipeline"]}
        assert list(stages) == [s.value for s in PIPELINE_STAGES]
        assert all(v in ("COMPLETED", "COMPLETED_WITH_WARNINGS") for v in stages.values()), stages

    # consistent documents => identity + turnover checks pass
    recon = recon_by_code(client, app["id"])
    assert recon["PAN_CONSISTENCY"]["status"] == "PASS"
    assert recon["GSTIN_CONSISTENCY"]["status"] == "PASS"
    assert recon["BUSINESS_NAME_CONSISTENCY"]["status"] == "PASS"
    assert recon["TURNOVER_ITR_VS_PL"]["status"] == "PASS"
    assert recon["TURNOVER_GST_VS_PL"]["status"] == "PASS"
    assert recon["FINANCIAL_PERIOD_ALIGNMENT"]["status"] == "PASS"
    assert recon["BANK_BALANCE_VS_BALANCE_SHEET"]["status"] == "PASS"
    srcs = {s["document_code"] for s in recon["PAN_CONSISTENCY"]["sources"]}
    assert {"PAN-001", "GST-002", "ITR-006"} <= srcs

    # bank statement: every transaction validated by the balance chain
    bank = docs["Bank_statement_FY2024-25.pdf"]
    ext = client.get(f"/api/documents/{bank['id']}/extraction").json()
    assert ext["summary"]["transactions"] == len(S.default_bank_spec(business).txns)
    assert ext["summary"]["rows_by_status"].get("NEEDS_REVIEW", 0) == 0
    assert ext["summary"]["rows_failed"] == 0
    txn_rows = [r for t in ext["tables"] for r in t["rows"] if r["row_kind"] == "TRANSACTION"]
    assert all(r["status"] == "VALIDATED" for r in txn_rows)

    # completeness: PRIVATE_LIMITED without KYC / MOA / AOA
    comp = client.get(f"/api/applications/{app['id']}/completeness").json()
    missing = set(comp["summary"]["missing_document_types"])
    assert {"KYC", "MOA", "AOA"} <= missing
    assert "PAN" not in missing and "BANK_STATEMENT" not in missing
    assert "PROTOTYPE" in comp["policy"]["name"]
    # missing required documents keep the application in review even though every document processed
    assert client.get(f"/api/applications/{app['id']}").json()["status"] == "NEEDS_REVIEW"

    overview = client.get(f"/api/applications/{app['id']}/overview").json()
    assert overview["evidence_coverage"]["fields_with_source_page_pct"] == 100.0
    assert overview["documents_received"]["unique"] == 9
    assert "approval" not in str(overview).lower()


def test_provenance_answers_where_value_came_from(client, business):
    app = create_app(client)
    upload(client, app["id"], {"PL.pdf": S.profit_loss_pdf(business)})
    doc = next(iter(docs_by_name(client, app["id"]).values()))
    fields = {f["field_name"]: f for f in client.get(f"/api/documents/{doc['id']}/extraction").json()["fields"]}
    revenue = fields["revenue"]
    assert revenue["normalized_value"] == "6820000.00"
    assert revenue["source_page"] == 1 and revenue["source_location"]["bbox"]
    prov = client.get(f"/api/fields/{revenue['id']}/provenance").json()
    assert "PL.pdf" in prov["explanation"] and "Page: 1" in prov["explanation"]
    assert prov["table"] is not None and prov["row"]["raw_cells"][0].startswith("Revenue from Operations")
    img = client.get(f"/api/documents/{doc['id']}/pages/1/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"


def test_balance_mismatch_is_inconsistency_not_fraud(client, business):
    spec = S.default_bank_spec(business)
    spec.txns[6].balance_text = S.inr(spec.txns[6].balance + Decimal("1000"))  # printed balance off by 1000
    app = create_app(client)
    upload(client, app["id"], {"stmt.pdf": S.bank_statement_pdf(spec)})
    doc = next(iter(docs_by_name(client, app["id"]).values()))
    assert doc["document_status"] == "NEEDS_REVIEW"
    val = client.get(f"/api/applications/{app['id']}/validation").json()
    mism = [r for r in val["results"] if r["rule_code"] == "BALANCE_CONTINUITY"]
    # the bad row and the row after it (whose previous balance is the misprinted one) both break
    assert len(mism) == 2 and all(r["status"] == "INCONSISTENCY" for r in mism)
    assert "fraud" not in str(val).lower()
    rows = [r for t in client.get(f"/api/documents/{doc['id']}/extraction").json()["tables"] for r in t["rows"]]
    flagged = [r for r in rows if r["status"] == "NEEDS_REVIEW"]
    assert len(flagged) == 2 and any("BALANCE_MISMATCH" in e for r in flagged for e in r["errors"])


def test_reprocessing_is_idempotent(client, db, business):
    app = create_app(client)
    upload(client, app["id"], {"stmt.pdf": S.bank_statement_pdf(S.default_bank_spec(business)),
                               "gst.pdf": S.gst_certificate_pdf(business)})
    docs = list(docs_by_name(client, app["id"]).values())

    def counts():
        return [db.scalar(select(func.count()).select_from(m)) for m in
                (ExtractedField, ExtractedTable, ExtractedTableRow, DocumentPage, ValidationResult)]

    before = counts()
    for d in docs:
        r = client.post(f"/api/documents/{d['id']}/process")
        assert r.status_code == 200 and r.json()["run_number"] == 2
    after = counts()
    assert before == after, "re-processing must replace, not duplicate, derived records"
    jobs = db.scalar(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.job_type == "DOCUMENT_PROCESSING"))
    assert jobs == 4  # job history is append-only (2 docs x 2 runs)
    recon_codes = [r["check_code"] for r in client.get(f"/api/applications/{app['id']}/reconciliation").json()["results"]]
    assert len(recon_codes) == len(set(recon_codes))


def test_cross_document_name_and_financial_inconsistency(client, business):
    app = create_app(client)
    files = sample_documents(business)
    files["Bank_statement_FY2024-25.pdf"] = S.bank_statement_pdf(S.default_bank_spec(business, holder="SUNRISE TEXTILES"))
    files["ITR_AY2025-26.pdf"] = S.itr_pdf(business, turnover=Decimal("4500000"))
    upload(client, app["id"], files)
    recon = recon_by_code(client, app["id"])
    name = recon["BUSINESS_NAME_CONSISTENCY"]
    assert name["status"] == "INCONSISTENCY"
    assert "SUNRISE TEXTILES" in name["message"]
    turnover = recon["TURNOVER_ITR_VS_PL"]
    assert turnover["status"] == "INCONSISTENCY"
    assert turnover["details"]["variance"] > 0.3
    docs = docs_by_name(client, app["id"])
    assert "CROSS_DOCUMENT:BUSINESS_NAME_CONSISTENCY" in docs["Bank_statement_FY2024-25.pdf"]["status_reasons"]
    # documents whose names agree are not blamed for the mismatch
    assert "CROSS_DOCUMENT:BUSINESS_NAME_CONSISTENCY" not in (docs["GST_REG06.pdf"]["status_reasons"] or [])


def test_pan_mismatch_between_pan_card_and_gstin(client, business):
    app = create_app(client)
    upload(client, app["id"], {"pan.pdf": S.pan_card_pdf("AAECS9999K", business.legal_name),
                               "gst.pdf": S.gst_certificate_pdf(business)})
    assert recon_by_code(client, app["id"])["PAN_CONSISTENCY"]["status"] == "INCONSISTENCY"


def test_scanned_pdf_without_ocr_needs_review(client, business):
    app = create_app(client)
    upload(client, app["id"], {"gst_certificate.pdf": S.scanned_pdf_from(S.gst_certificate_pdf(business))})
    doc = next(iter(docs_by_name(client, app["id"]).values()))
    assert doc["document_status"] == "NEEDS_REVIEW"
    reasons = doc["status_reasons"]
    assert "LOW_CLASSIFICATION_CONFIDENCE" in reasons
    assert "VALIDATION:PAGE_TEXT_UNAVAILABLE" in reasons


def test_scanned_pdf_with_ocr(client, business, fake_ocr):
    original = S.gst_certificate_pdf(business)
    fake_ocr(original)
    app = create_app(client)
    upload(client, app["id"], {"scan.pdf": S.scanned_pdf_from(original)})
    doc = next(iter(docs_by_name(client, app["id"]).values()))
    assert doc["document_type"] == "GST_CERTIFICATE"
    pages = client.get(f"/api/documents/{doc['id']}/pages").json()
    assert pages[0]["text_source"] == "OCR" and pages[0]["ocr_confidence"] == 88.0
    fields = {f["field_name"]: f for f in client.get(f"/api/documents/{doc['id']}/extraction").json()["fields"]}
    assert fields["gstin"]["normalized_value"] == business.gstin


def test_invalid_document_skips_later_stages(client, db):
    app = create_app(client)
    res = upload(client, app["id"], {"locked.pdf": S.password_protected_pdf(S.unknown_document_pdf())})
    doc = res["documents"][0]
    r = client.post(f"/api/documents/{doc['id']}/process")
    assert r.status_code == 200
    stages = {s["stage"]: s for s in r.json()["stages"]}
    assert stages["FILE_VALIDATION"]["status"] == "FAILED"
    assert "PDF_PASSWORD" in stages["FILE_VALIDATION"]["error"]
    assert stages["CLASSIFICATION"]["status"] == "SKIPPED"
    assert stages["CROSS_DOCUMENT_RECONCILIATION"]["status"] != "SKIPPED"  # app-level stages still run
    status = client.get(f"/api/documents/{doc['id']}/status").json()
    assert status["document_status"] == "INVALID"


def test_unexpected_failure_is_recorded_never_silent(client, business, monkeypatch):
    from app.document_ai.extractors.identity import GSTCertificateExtractor

    def boom(self, ctx, tables=None):
        raise RuntimeError("simulated extractor crash")

    monkeypatch.setattr(GSTCertificateExtractor, "extract", boom)
    app = create_app(client)
    upload(client, app["id"], {"gst.pdf": S.gst_certificate_pdf(business)})
    doc = next(iter(docs_by_name(client, app["id"]).values()))
    assert doc["document_status"] == "FAILED"
    assert "simulated extractor crash" in doc["error_message"]
    stages = {s["stage"]: s for s in doc["pipeline"]}
    assert stages["FIELD_EXTRACTION"]["status"] == "FAILED"
    assert stages["FIELD_VALIDATION"]["status"] == "SKIPPED"
    overview = client.get(f"/api/applications/{app['id']}/overview").json()
    assert overview["processing_errors"][0]["document_id"] == doc["id"]


def test_type_override_and_audit_trail(client, db, business):
    app = create_app(client)
    upload(client, app["id"], {"unknown.pdf": S.unknown_document_pdf()})
    doc = next(iter(docs_by_name(client, app["id"]).values()))
    assert doc["document_type"] == "UNKNOWN" and doc["document_status"] == "NEEDS_REVIEW"
    r = client.patch(f"/api/documents/{doc['id']}/type", json={"document_type": "BUSINESS_PLAN"})
    assert r.status_code == 200
    body = r.json()
    assert body["document_type"] == "BUSINESS_PLAN" and body["classification_method"] == "manual"
    actions = {a["action"] for a in client.get(f"/api/applications/{app['id']}/audit").json()}
    assert {"APPLICATION_CREATED", "DOCUMENT_UPLOADED", "DOCUMENT_TYPE_OVERRIDDEN", "DOCUMENT_CLASSIFIED",
            "PROCESSING_FINISHED"} <= actions
    assert db.scalar(select(func.count()).select_from(AuditLog)) > 10


def test_meta_endpoint_and_health(client):
    meta = client.get("/api/meta").json()
    assert "GST_CERTIFICATE" in meta["schemas"]
    assert meta["engines"]["llm"]["available"] is False
    assert client.get("/health").json()["database"] is True
