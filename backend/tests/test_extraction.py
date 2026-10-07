"""Extraction-level tests (no database): fields, provenance, tables, row statuses."""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal

from app.document_ai.extractor import ExtractionContext
from app.document_ai.extractors import get_extractor
from app.document_ai.pdf_parser import parse_document
from app.document_ai.table_extractor import extract_tables
from app.models.enums import DocumentType, ExtractionMethod, RowKind, RowStatus, TableType
from scripts import synthetic_docs as S


def run(write, name, data, doc_type):
    path = write(name, data)
    parsed = parse_document(path, "application/pdf")
    tables, _ = extract_tables(path, parsed)
    result = get_extractor(doc_type).extract(ExtractionContext(parsed, tables, name))
    return result, {f.name: f for f in result.fields}


def assert_provenance(f):
    assert not f.is_missing
    assert f.page is not None and f.page >= 1
    loc = f.source_location()
    assert loc and (loc.get("bbox") or loc.get("components"))
    assert f.raw_value is not None and f.method != ExtractionMethod.NONE


def test_pan_extraction(write, business):
    res, f = run(write, "pan.pdf", S.pan_card_pdf(business.pan, business.legal_name), DocumentType.PAN)
    assert f["pan"].normalized_value == business.pan
    assert f["name"].normalized_value == business.legal_name
    assert f["entity_type"].normalized_value == "Company"
    assert f["entity_type"].method == ExtractionMethod.DERIVED
    for name in ("pan", "name"):
        assert_provenance(f[name])


def test_invalid_pan_on_card_is_not_normalised(write, business):
    res, f = run(write, "pan.pdf", S.pan_card_pdf("AAEC4821K", business.legal_name), DocumentType.PAN)
    # Malformed PAN never becomes a "valid" value; it is missing or kept raw with a format warning
    pan = f["pan"]
    assert pan.is_missing or pan.normalized_value is None
    assert "entity_type" not in f  # nothing is derived from an invalid PAN


def test_gst_certificate_extraction(write, business):
    res, f = run(write, "gst.pdf", S.gst_certificate_pdf(business), DocumentType.GST_CERTIFICATE)
    assert f["gstin"].normalized_value == business.gstin
    assert f["legal_name"].normalized_value == business.legal_name
    assert f["trade_name"].normalized_value == business.trade_name
    assert f["registration_date"].typed_value == date(2017, 7, 1)
    assert "Coimbatore" in f["principal_address"].normalized_value
    assert f["status"].is_missing  # not printed on the certificate => not invented
    assert_provenance(f["gstin"])
    assert res.structured["gstin"] == business.gstin
    assert res.structured["status"] is None


def test_gst_missing_required_field(write, business):
    res, f = run(write, "gst.pdf", S.gst_certificate_pdf(business, omit_legal_name=True), DocumentType.GST_CERTIFICATE)
    assert f["legal_name"].is_missing and f["legal_name"].required
    assert any("MISSING_REQUIRED_FIELDS" in w for w in res.warnings)


def test_udyam_and_itr(write, business):
    _, u = run(write, "u.pdf", S.udyam_pdf(business), DocumentType.UDYAM)
    assert u["udyam_number"].normalized_value == business.udyam
    assert u["enterprise_type"].normalized_value == "MICRO"
    assert u["nic_code"].normalized_value == "28221"
    _, i = run(write, "itr.pdf", S.itr_pdf(business), DocumentType.ITR)
    assert i["assessment_year"].normalized_value == "2025-26"
    assert i["turnover"].typed_value == Decimal("6820000")
    assert i["taxable_income"].typed_value == Decimal("905000")
    assert i["itr_form"].normalized_value == "ITR-6"


def test_profit_and_loss_table_extraction(write, business):
    res, f = run(write, "pl.pdf", S.profit_loss_pdf(business), DocumentType.PROFIT_LOSS)
    assert f["period"].normalized_value == "2024-25"
    assert f["revenue"].typed_value == Decimal("6820000")  # current year column, not prior year
    assert f["profit_before_tax"].typed_value == Decimal("905000")  # not confused with "Net profit"
    assert f["profit_after_tax"].typed_value == Decimal("677000")
    assert f["revenue"].method == ExtractionMethod.TABLE
    assert f["revenue"].table_index is not None and f["revenue"].row_index is not None
    assert_provenance(f["revenue"])
    fin = [t for t in res.tables if t.table_type == TableType.FINANCIAL_STATEMENT]
    assert fin and all(r.status != RowStatus.FAILED for r in fin[0].rows)


def test_balance_sheet_sums_borrowings_with_components(write, business):
    res, f = run(write, "bs.pdf", S.balance_sheet_pdf(business), DocumentType.BALANCE_SHEET)
    assert f["borrowings"].typed_value == Decimal("1800000")
    assert f["borrowings"].method == ExtractionMethod.DERIVED
    assert len(f["borrowings"].components) == 2
    assert f["total_assets"].typed_value == f["total_liabilities"].typed_value == Decimal("6100000")
    assert f["bank_balance"].typed_value == Decimal("543750")


def test_multi_page_bank_statement(write, business):
    spec = S.default_bank_spec(business)
    res, f = run(write, "stmt.pdf", S.bank_statement_pdf(spec), DocumentType.BANK_STATEMENT)
    txn_rows = [r for t in res.tables for r in t.rows if r.kind == RowKind.TRANSACTION]
    pages = {r.page_number for r in txn_rows}
    assert len(pages) >= 3, "transactions must be extracted from every page"
    assert len(txn_rows) == len(spec.txns)
    assert all(r.status == RowStatus.EXTRACTED for r in txn_rows)
    assert len(res.structured["transactions"]) == len(spec.txns)
    assert f["account_number"].normalized_value == business.account_number
    assert f["ifsc"].normalized_value == business.ifsc
    assert f["opening_balance"].typed_value == spec.opening
    assert f["closing_balance"].typed_value == spec.closing
    assert f["period_start"].typed_value == spec.start
    assert f["period_end"].typed_value == spec.end
    # the three page segments are reconstructed into ONE logical table spanning pages 1-3
    txn_tables = [t for t in res.tables if t.table_type == TableType.TRANSACTIONS]
    assert len(txn_tables) == 1
    assert txn_tables[0].raw.page_number == 1 and txn_tables[0].raw.page_end == 3
    assert [s["page"] for s in txn_tables[0].raw.meta["segments"]] == [1, 2, 3]


def test_unparseable_rows_are_kept_not_dropped(write, business):
    spec = S.default_bank_spec(business)
    spec.txns[3].balance_text = "1,2O,000.00"  # OCR-style letter O
    spec.txns[5].date_text = "3l/04/2024"  # unreadable date but amounts present
    spec.txns.insert(8, S.Txn(spec.txns[8].date, "", "", None, None, None, date_text="##/##/####"))
    spec.txns[10].extra_line = "INV 2024-118 PART PAYMENT"
    res, _ = run(write, "stmt.pdf", S.bank_statement_pdf(spec), DocumentType.BANK_STATEMENT)
    rows = [r for t in res.tables for r in t.rows]
    by = Counter((r.kind, r.status) for r in rows)
    assert by[(RowKind.UNPARSED, RowStatus.FAILED)] == 1
    review = [r for r in rows if r.status == RowStatus.NEEDS_REVIEW]
    assert any("UNPARSEABLE_BALANCE" in e for r in review for e in r.errors)
    assert any("UNPARSEABLE_DATE" in e for r in review for e in r.errors)
    cont = [r for r in rows if r.kind == RowKind.CONTINUATION]
    assert len(cont) == 1
    merged = next(r for r in rows if r.row_index == cont[0].parsed["continuation_of"])
    assert "PART PAYMENT" in merged.parsed["description"]


def test_scanned_page_without_ocr_is_flagged(write, business):
    data = S.scanned_pdf_from(S.gst_certificate_pdf(business))
    path = write("scan.pdf", data)
    parsed = parse_document(path, "application/pdf")
    page = parsed.pages[0]
    assert page.is_scanned and page.text_source.value == "NONE"
    assert any(w["code"] == "SCANNED_PAGE_NO_OCR" for w in page.warnings)


def test_scanned_page_with_ocr(write, business, fake_ocr):
    original = S.gst_certificate_pdf(business)
    fake_ocr(original, conf=85.0)
    res, f = run(write, "scan.pdf", S.scanned_pdf_from(original), DocumentType.GST_CERTIFICATE)
    assert f["gstin"].normalized_value == business.gstin
    # OCR-sourced values carry reduced confidence and an OCR_SOURCE warning
    assert f["gstin"].confidence < 0.95
    assert any("OCR_SOURCE" in w for w in f["gstin"].warnings)
