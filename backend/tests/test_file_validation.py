from __future__ import annotations

from scripts import synthetic_docs as S
from app.ingestion.file_validator import validate_file
from tests.conftest import create_app, upload


def _codes(report, status):
    return {c.code for c in report.checks if c.status == status}


def test_valid_pdf(write, business):
    report = validate_file(write("gst.pdf", S.gst_certificate_pdf(business)), "gst.pdf", "application/pdf")
    assert report.is_valid
    assert report.page_count == 1
    assert report.detected_mime == "application/pdf"


def test_corrupted_pdf_is_invalid(write, business):
    report = validate_file(write("bad.pdf", S.corrupted_pdf(b"")), "bad.pdf")
    assert not report.is_valid
    assert "PDF_READABLE" in _codes(report, "FAIL")


def test_password_protected_pdf(write, business):
    data = S.password_protected_pdf(S.gst_certificate_pdf(business))
    report = validate_file(write("locked.pdf", data), "locked.pdf")
    assert not report.is_valid
    assert "PDF_PASSWORD" in _codes(report, "FAIL")
    assert report.is_encrypted


def test_empty_file(write):
    report = validate_file(write("empty.pdf", b""), "empty.pdf")
    assert not report.is_valid
    assert "FILE_SIZE" in _codes(report, "FAIL")


def test_disallowed_extension(write):
    report = validate_file(write("macro.docm", b"PK\x03\x04xxxx"), "macro.docm")
    assert not report.is_valid
    assert "EXTENSION" in _codes(report, "FAIL")


def test_content_extension_mismatch_is_flagged(write, business):
    png = S.page_png(S.gst_certificate_pdf(business))
    report = validate_file(write("gst.pdf", png), "gst.pdf", "application/pdf")
    assert report.detected_mime == "image/png"
    assert "MIME_EXTENSION_MATCH" in _codes(report, "WARNING")


def test_low_quality_image_warning(write, business):
    blurry = S.blurred_png(S.page_png(S.gst_certificate_pdf(business), dpi=100), k=15)
    report = validate_file(write("scan.png", blurry), "scan.png", "image/png")
    assert report.is_valid  # readable, but flagged
    assert "IMAGE_QUALITY" in _codes(report, "WARNING")
    assert "BLURRY" in report.image_quality["issues"]


def test_unreadable_image(write):
    report = validate_file(write("broken.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 200), "broken.png")
    assert not report.is_valid
    assert "IMAGE_READABLE" in _codes(report, "FAIL")


def test_scanned_pdf_flags_missing_text_layer(write, business):
    data = S.scanned_pdf_from(S.gst_certificate_pdf(business))
    report = validate_file(write("scan.pdf", data), "scan.pdf")
    assert report.is_valid
    assert report.pages_without_text == [1]
    assert "PDF_TEXT_LAYER" in _codes(report, "WARNING")


def test_duplicate_upload_creates_invalid_record(client, business):
    app = create_app(client)
    data = S.pan_card_pdf(business.pan, business.legal_name)
    first = upload(client, app["id"], {"pan.pdf": data}, auto_process=False)["documents"][0]
    second = upload(client, app["id"], {"pan_copy.pdf": data}, auto_process=False)["documents"][0]
    assert first["document_status"] == "VALID"
    assert second["document_status"] == "INVALID"
    assert second["is_duplicate"] and second["duplicate_of_id"] == first["id"]
    assert "DUPLICATE_DOCUMENT" in second["error_message"]
    docs = client.get(f"/api/applications/{app['id']}/documents").json()
    assert len(docs) == 2  # the duplicate is recorded, never silently dropped


def test_invalid_upload_is_recorded_with_reason(client):
    app = create_app(client)
    res = upload(client, app["id"], {"broken.pdf": S.corrupted_pdf(b"")}, auto_process=False)
    doc = res["documents"][0]
    assert doc["document_status"] == "INVALID"
    assert "PDF_READABLE" in doc["error_message"]
    status = client.get(f"/api/documents/{doc['id']}/status").json()
    fv = next(s for s in status["pipeline"] if s["stage"] == "FILE_VALIDATION")
    assert fv["status"] == "FAILED"
