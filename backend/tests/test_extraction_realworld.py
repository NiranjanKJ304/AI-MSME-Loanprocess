"""Extraction on documents that do NOT follow the default synthetic templates.

Every test checks actual values, tables, provenance and normalisation - not just that a
pipeline ran.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import cv2
import numpy as np
import pytest

from app.document_ai import normalizers as N
from app.document_ai.extractor import ExtractionContext, normalize
from app.document_ai.extractors import get_extractor
from app.document_ai.image_quality import map_box_to_original
from app.document_ai.ocr import OCREngine, OCRResult, OCRWord, TesseractOCREngine, set_ocr_engine, words_from_tesseract_data
from app.document_ai.pdf_parser import decide_page_method, parse_document, text_quality
from app.document_ai.table_extractor import classify_header_phrase, extract_tables
from app.document_ai.types import ParsedDocument, RawTable
from app.models.enums import DocumentType, ExtractionStatus, RowKind, RowStatus, TableType, TextSource
from scripts import synthetic_docs as S
from tests.conftest import create_app, upload
from tests.fixtures import layouts as L

DT = DocumentType


def run_pdf(write, name: str, data: bytes, doc_type: DocumentType):
    path = write(name, data)
    parsed = parse_document(path, "application/pdf")
    tables, warnings = extract_tables(path, parsed)
    result = get_extractor(doc_type).extract(ExtractionContext(parsed, tables, name))
    return parsed, result, {f.name: f for f in result.fields}


def run_doc(doc: ParsedDocument, doc_type: DocumentType, tables: list[RawTable] | None = None):
    result = get_extractor(doc_type).extract(ExtractionContext(doc, tables or [], "synthetic"))
    return result, {f.name: f for f in result.fields}


def assert_traceable(f, parsed: ParsedDocument):
    """The officer can trace the value back: page, box inside the page, source text, method, factors."""
    assert not f.is_missing
    assert f.page is not None
    page = parsed.page(f.page)
    assert page is not None
    loc = f.source_location()
    assert loc and loc.get("text_source") == page.text_source.value
    assert loc.get("confidence_factors", {}).get("final") == f.confidence
    if page.width:  # geometry available (PDF / image)
        x0, y0, x1, y1 = loc["bbox"]
        assert 0 <= x0 < x1 <= page.width + 1 and 0 <= y0 < y1 <= page.height + 1
    assert f.snippet and f.raw_value
    raw_core = f.raw_value.replace(" ", "")[:8]
    assert raw_core in f.snippet.replace(" ", "")
    assert 0 < f.confidence < 1.0  # never certain


# =========================================================================== normal / scanned / mixed PDF
def test_normal_pdf_fields_have_full_provenance(write, business):
    parsed, res, f = run_pdf(write, "gst.pdf", S.gst_certificate_pdf(business), DT.GST_CERTIFICATE)
    assert parsed.pages[0].text_source == TextSource.TEXT_LAYER
    assert parsed.pages[0].text_quality["decision"] == "TEXT_LAYER"
    assert parsed.pages[0].blocks, "layout blocks are preserved"
    for name in ("gstin", "legal_name", "trade_name", "registration_date", "business_type", "principal_address"):
        assert f[name].extraction_status == ExtractionStatus.EXTRACTED, name
        assert_traceable(f[name], parsed)
    assert f["gstin"].normalized_value == business.gstin
    assert f["principal_address"].position == "BELOW"  # value printed under its label
    assert f["status"].extraction_status == ExtractionStatus.NOT_FOUND  # not printed => not invented
    assert res.status == "OK"


def test_scanned_pdf_with_ocr_maps_boxes_to_page_coordinates(write, business, fake_ocr):
    original = S.gst_certificate_pdf(business)
    _, _, native = run_pdf(write, "native.pdf", original, DT.GST_CERTIFICATE)
    fake_ocr(original, conf=88.0)
    parsed, res, f = run_pdf(write, "scan.pdf", S.scanned_pdf_from(original), DT.GST_CERTIFICATE)
    page = parsed.pages[0]
    assert page.text_source == TextSource.OCR and page.text_quality["reason"] == "NO_TEXT_LAYER"
    assert page.ocr_output["engine"] == "fake" and page.ocr_output["words"], "raw OCR output preserved"
    assert f["gstin"].normalized_value == business.gstin
    assert_traceable(f["gstin"], parsed)
    # OCR boxes land where the text is on the page (same place as in the native PDF)
    nb, ob = native["gstin"].bbox, f["gstin"].bbox
    assert all(abs(a - b) < 2.0 for a, b in zip(nb, ob))
    # OCR text is less certain than native text, and the factor says why
    assert f["gstin"].confidence < native["gstin"].confidence
    assert f["gstin"].confidence_factors["text_source"].startswith("OCR")


def test_scanned_pdf_without_ocr_is_ocr_required_not_empty(write, business):
    parsed, res, f = run_pdf(write, "scan.pdf", S.scanned_pdf_from(S.gst_certificate_pdf(business)), DT.GST_CERTIFICATE)
    page = parsed.pages[0]
    assert page.text_source == TextSource.NONE
    assert {w["code"] for w in page.warnings} >= {"SCANNED_PAGE_NO_OCR"}
    assert res.status == "OCR_REQUIRED"
    assert all(x.extraction_status == ExtractionStatus.OCR_REQUIRED for x in res.fields)


def test_mixed_pdf_preserves_method_per_page(write, business, fake_ocr):
    gst = S.gst_certificate_pdf(business)
    data = L.mixed_pdf(S.pan_card_pdf(business.pan, business.legal_name), gst)
    fake_ocr(gst)
    parsed, res, f = run_pdf(write, "mixed.pdf", data, DT.PAN)
    assert [p.text_source for p in parsed.pages] == [TextSource.TEXT_LAYER, TextSource.OCR]
    assert parsed.pages[1].ocr_output and parsed.pages[1].lines
    assert f["pan"].page == 1 and f["pan"].text_source == "TEXT_LAYER"
    assert f["pan"].normalized_value == business.pan
    assert res.status == "OK"


def test_mixed_pdf_without_ocr_is_partial(write, business):
    data = L.mixed_pdf(S.pan_card_pdf(business.pan, business.legal_name), S.gst_certificate_pdf(business))
    parsed, res, f = run_pdf(write, "mixed.pdf", data, DT.PAN)
    assert [p.text_source for p in parsed.pages] == [TextSource.TEXT_LAYER, TextSource.NONE]
    assert res.status == "PARTIAL"
    assert f["pan"].extraction_status == ExtractionStatus.EXTRACTED


def test_camscanner_image_page_is_hybrid(write, business, fake_ocr):
    gst = S.gst_certificate_pdf(business)
    fake_ocr(gst)
    parsed, res, f = run_pdf(write, "cam.pdf", L.camscanner_pdf(gst), DT.GST_CERTIFICATE)
    page = parsed.pages[0]
    assert page.text_quality["decision"] == "HYBRID"
    assert page.text_source == TextSource.HYBRID
    assert any(w.text == "CamScanner" and w.conf is None for w in page.words), "native watermark kept"
    assert page.text_quality["ocr_words_added"] > 20
    assert f["gstin"].normalized_value == business.gstin and f["legal_name"].normalized_value == business.legal_name


def test_garbled_text_layer_triggers_ocr():
    garbage = "(cid:3)(cid:17)(cid:22)(cid:9) (cid:4)(cid:11)(cid:5) �� " * 6
    q = text_quality(garbage)
    assert q["garbage_ratio"] > 0.2
    assert decide_page_method(q, n_words=30, image_coverage=0.0) == ("OCR", "UNREADABLE_TEXT_LAYER")
    good = text_quality("Statement of Account  Account Number 50200031457788  IFSC KCBL0000212")
    assert decide_page_method(good, n_words=200, image_coverage=0.9) == ("TEXT_LAYER", "NATIVE_TEXT_OK")
    assert decide_page_method(good, n_words=6, image_coverage=0.95)[0] == "HYBRID"


# =========================================================================== OCR behaviour
class _CrashingOCR(OCREngine):
    name = "crashy"

    def available(self):
        return True

    def recognize(self, image):
        raise RuntimeError("tesseract segfault")


class _WordsOCR(OCREngine):
    """Returns fixed words (pixel coords at 300 dpi) with a given confidence."""

    name = "words"

    def __init__(self, words: list[tuple[str, tuple[float, float, float, float]]], conf: float):
        self.words, self.conf = words, conf

    def available(self):
        return True

    def recognize(self, image):
        ws = [OCRWord(t, b, self.conf) for t, b in self.words]
        return OCRResult(" ".join(t for t, _ in self.words), ws, self.conf, self.name)


def test_ocr_failure_is_recorded_and_never_silent(write, business):
    set_ocr_engine(_CrashingOCR())
    parsed, res, _ = run_pdf(write, "scan.pdf", S.scanned_pdf_from(S.pan_card_pdf(business.pan, business.legal_name)),
                             DT.PAN)
    page = parsed.pages[0]
    assert page.text_source == TextSource.NONE
    assert page.errors and page.errors[0]["code"] == "OCR_FAILED" and "segfault" in page.errors[0]["message"]
    assert page.text_quality["ocr_status"] == "FAILED"
    assert res.status == "OCR_REQUIRED"
    assert {x.extraction_status for x in res.fields} == {ExtractionStatus.OCR_REQUIRED}


def test_poor_ocr_gives_low_confidence(write, business, fake_ocr):
    original = S.pan_card_pdf(business.pan, business.legal_name)
    fake_ocr(original, conf=45.0)
    parsed, res, f = run_pdf(write, "blurry.pdf", S.scanned_pdf_from(original, blur=True), DT.PAN)
    assert any(w["code"] == "LOW_OCR_CONFIDENCE" for w in parsed.pages[0].warnings)
    assert f["pan"].normalized_value == business.pan  # still read ...
    assert f["pan"].extraction_status == ExtractionStatus.LOW_CONFIDENCE  # ... but flagged
    assert f["pan"].confidence_factors["source"] < 0.8


def test_ocr_character_confusion_is_repaired_only_for_ocr_text():
    rows = [(60, [(30, "Permanent Account Number Card")]), (80, [(30, "AAECS482IK")]),  # 'I' for '1'
            (110, [(30, "Name")]), (124, [(30, "SRI LAKSHMI PRECISION COMPONENTS PRIVATE LIMITED")])]
    ocr_page = L.page_from_rows(rows, source=TextSource.OCR, conf=82.0)
    _, f = run_doc(L.doc_from_pages(ocr_page), DT.PAN)
    assert f["pan"].normalized_value == "AAECS4821K"
    assert f["pan"].raw_value == "AAECS482IK"  # raw kept exactly as read
    assert any(w.startswith("OCR_CHARS_CORRECTED") for w in f["pan"].warnings)
    assert f["pan"].confidence_factors["ocr_repair"] == 0.85
    native_page = L.page_from_rows(rows)
    _, g = run_doc(L.doc_from_pages(native_page), DT.PAN)
    # in a native PDF a malformed PAN is a real defect: reported, never "fixed"
    assert g["pan"].normalized_value is None
    assert g["pan"].extraction_status == ExtractionStatus.EXTRACTION_FAILED
    assert g["pan"].raw_value == "AAECS482IK"


def test_tesseract_boxes_are_mapped_back_through_preprocessing_transform():
    # processed image = original scaled x2 then rotated 5 degrees
    rot = cv2.getRotationMatrix2D((800.0, 1100.0), 5.0, 1.0)
    scale = np.array([[2.0, 0, 0], [0, 2.0, 0], [0, 0, 1]])
    m = rot @ scale
    orig = (100.0, 200.0, 260.0, 230.0)
    corners = np.array([[orig[0], orig[1]], [orig[2], orig[1]], [orig[0], orig[3]], [orig[2], orig[3]]])
    fwd = corners @ m[:, :2].T + m[:, 2]
    x0, y0 = fwd[:, 0].min(), fwd[:, 1].min()
    data = {"text": ["AAECS4821K", ""], "conf": ["91", "-1"], "left": [x0, 0], "top": [y0, 0],
            "width": [fwd[:, 0].max() - x0, 0], "height": [fwd[:, 1].max() - y0, 0],
            "block_num": [1, 1], "par_num": [1, 1], "line_num": [1, 1]}
    words, lines = words_from_tesseract_data(data, m)
    assert len(words) == 1 and lines[0]["text"] == "AAECS4821K"
    bx = words[0].bbox
    cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
    assert abs(cx - 180) < 1.5 and abs(cy - 215) < 1.5  # centre of the original box
    assert map_box_to_original((10, 10, 20, 20), np.array([[1.0, 0, 0], [0, 1.0, 0]])) == (10, 10, 20, 20)


def test_tesseract_adapter_single_pass_with_line_structure():
    class FakePT:
        class Output:
            DICT = "dict"

        def image_to_data(self, img, lang, config, output_type):
            assert "--psm 3" in config
            return {"text": ["Statement", "of", "Account", "IFSC", "KCBL0000212"],
                    "conf": ["95", "93", "96", "90", "88"], "left": [10, 120, 150, 10, 80],
                    "top": [10, 10, 10, 40, 40], "width": [100, 25, 80, 50, 120], "height": [20] * 5,
                    "block_num": [1] * 5, "par_num": [1] * 5, "line_num": [1, 1, 1, 2, 2]}

    eng = TesseractOCREngine()
    eng._pt, eng._ok = FakePT(), True  # image_to_string is deliberately absent: one OCR pass only
    res = eng.recognize(np.full((600, 2000, 3), 255, dtype=np.uint8))
    assert res.text.splitlines() == ["Statement of Account", "IFSC KCBL0000212"]
    assert round(res.mean_confidence, 1) == 92.4 and len(res.lines) == 2


# =========================================================================== bank statements
def test_bank_variant_two_line_header_repeated_on_every_page(write):
    data, exp = L.bank_variant_b()
    parsed, res, f = run_pdf(write, "hdfc_style.pdf", data, DT.BANK_STATEMENT)
    assert exp["pages"] == 3
    txn_tables = [t for t in res.tables if t.table_type == TableType.TRANSACTIONS]
    assert len(txn_tables) == 1, "page segments reconstructed into one table"
    t = txn_tables[0]
    assert (t.raw.page_number, t.raw.page_end) == (1, 3)
    assert t.analysis.column_mapping["date"] == 0 and t.analysis.column_mapping["value_date"] == 1
    headers = [r for r in t.rows if r.kind == RowKind.HEADER]
    assert {r.page_number for r in headers} == {1, 2, 3}, "repeated headers recognised on every page"
    txns = [r for r in t.rows if r.kind == RowKind.TRANSACTION]
    assert len(txns) == len(exp["txns"])
    for r, e in zip(txns, exp["txns"]):
        assert r.parsed["date"] == e["date"].isoformat()
        assert r.parsed["debit"] == (N.decimal_str(e["debit"]) if e["debit"] is not None else None)
        assert r.parsed["credit"] == (N.decimal_str(e["credit"]) if e["credit"] is not None else None)
        assert r.parsed["balance"] == N.decimal_str(e["balance"])
        assert r.status == RowStatus.EXTRACTED
        if e.get("wrap"):
            assert r.parsed["description"] == f"{e['desc']} {e['wrap']}"
    assert not any("Withdrawal" in (r.parsed or {}).get("description") or "" for r in txns)
    assert f["opening_balance"].typed_value == exp["opening"]
    assert f["account_number"].normalized_value == exp["account"]
    assert f["ifsc"].normalized_value == exp["ifsc"]
    assert f["bank_name"].normalized_value == "HDFC Bank" and f["bank_name"].method.value == "DERIVED"
    assert f["account_holder"].normalized_value == "M/S SUNRISE TEXTILES"
    assert (f["period_start"].normalized_value, f["period_end"].normalized_value) == ("2024-04-01", "2025-03-31")
    # no closing balance is printed in this layout: reported, not computed
    assert f["closing_balance"].extraction_status == ExtractionStatus.NOT_FOUND


def test_bank_variant_ruled_grid_amount_with_dr_cr(write):
    data, exp = L.bank_variant_c()
    parsed, res, f = run_pdf(write, "ruled.pdf", data, DT.BANK_STATEMENT)
    t = next(t for t in res.tables if t.table_type == TableType.TRANSACTIONS)
    assert t.raw.method.startswith("merged:pdfplumber")
    assert (t.raw.page_number, t.raw.page_end) == (1, 2)
    kinds = Counter(r.kind for r in t.rows)
    assert kinds[RowKind.HEADER] == 2 and kinds[RowKind.TRANSACTION] == len(exp["rows"])
    txns = [r for r in t.rows if r.kind == RowKind.TRANSACTION]
    for r, e in zip(txns, exp["rows"]):
        assert r.parsed["debit"] == (N.decimal_str(e["debit"]) if e["debit"] else None)
        assert r.parsed["credit"] == (N.decimal_str(e["credit"]) if e["credit"] else None)
        assert r.parsed["balance"] == N.decimal_str(e["balance"])  # "Cr" balance stays positive
        assert r.parsed["description"] == e["narr"]  # text wrapped inside the cell is joined
    assert "\n" in txns[1].raw_cells[2], "raw cell keeps its original line break"
    assert f["opening_balance"].typed_value == exp["opening"]
    assert f["ifsc"].position == "INLINE" and f["ifsc"].normalized_value == "KCBL0000212"
    assert f["account_holder"].extraction_status == ExtractionStatus.NOT_FOUND


def test_narration_printed_above_the_date_line_attaches_downwards():
    hdr = [(40, "Date"), (110, "Particulars"), (330, "Debit"), (410, "Credit"), (500, "Balance")]
    rows = [
        (100, hdr),
        (118, [(40, "01/04/2024"), (110, "Opening Balance"), (500, "1,00,000.00")]),
        (140, [(110, "NEFT CR SHAKTHI PUMPS")]),  # narration line above its date line
        (150, [(40, "02/04/2024"), (110, "UTR 4471"), (410, "25,000.00"), (500, "1,25,000.00")]),
        (180, [(40, "03/04/2024"), (110, "CHQ 0012 RENT"), (330, "5,000.00"), (500, "1,20,000.00")]),
        (190, [(110, "APRIL 2024")]),  # wrapped continuation below
    ]
    doc = L.doc_from_pages(L.page_from_rows(rows, size=9))
    doc.source_kind = "image"  # synthetic geometry: skip the pdfplumber pass
    tables, _ = extract_tables(None, doc)
    res, _ = run_doc(doc, DT.BANK_STATEMENT, tables)
    txns = [r for t in res.tables for r in t.rows if r.kind == RowKind.TRANSACTION]
    assert txns[0].parsed["description"] == "NEFT CR SHAKTHI PUMPS UTR 4471"
    assert txns[1].parsed["description"] == "CHQ 0012 RENT APRIL 2024"
    cont = [r for t in res.tables for r in t.rows if r.kind == RowKind.CONTINUATION]
    assert [c.parsed["attached"] for c in cont] == ["below", "above"]


@pytest.mark.parametrize("phrase,canon", [
    ("Date", "date"), ("Txn Date", "date"), ("Transaction Date", "date"), ("Tran. Dt", "date"),
    ("Value Dt", "value_date"), ("Narration", "description"), ("Particulars", "description"),
    ("Transaction Details", "description"), ("Chq./Ref.No.", "reference"), ("Cheque No", "reference"),
    ("Withdrawal Amt.", "debit"), ("Withdrawals (Dr)", "debit"), ("Debit", "debit"), ("Dr", "debit"),
    ("Deposit Amt.", "credit"), ("Credit", "credit"), ("Cr", "credit"), ("Closing Balance", "balance"),
    ("Balance (INR)", "balance"), ("Dr/Cr", "drcr"), ("Amount", "amount"), ("S.No", None), ("Branch", None),
])
def test_bank_header_vocabulary(phrase, canon):
    assert classify_header_phrase(phrase) == canon


# =========================================================================== label / value layouts
def test_value_below_label_in_two_column_form():
    g = L.gstin_for()
    rows = [(100, [(50, "Legal Name"), (320, "GSTIN")]),
            (114, [(50, "SUNRISE TEXTILES PRIVATE LIMITED"), (320, g)])]
    _, f = run_doc(L.doc_from_pages(L.page_from_rows(rows)), DT.GST_CERTIFICATE)
    assert f["legal_name"].normalized_value == "SUNRISE TEXTILES PRIVATE LIMITED"
    assert f["legal_name"].position == "BELOW"
    assert f["gstin"].normalized_value == g and f["gstin"].position == "BELOW"
    assert f["legal_name"].bbox[2] < 320, "value box stays inside its own column"


def test_two_label_value_pairs_on_one_line():
    g = L.gstin_for()
    rows = [(100, [(50, "Name of the Company: SUNRISE TEXTILES PVT LTD"), (330, f"GSTIN: {g}")])]
    _, f = run_doc(L.doc_from_pages(L.page_from_rows(rows)), DT.BUSINESS_REGISTRATION)
    assert f["entity_name"].normalized_value == "SUNRISE TEXTILES PVT LTD"  # does not swallow "GSTIN: ..."
    _, h = run_doc(L.doc_from_pages(L.page_from_rows(rows)), DT.GST_CERTIFICATE)
    assert h["gstin"].normalized_value == g and h["gstin"].position == "INLINE"


def test_value_above_caption_and_bilingual_label():
    rows = [(100, [(60, "SUNRISE TEXTILES")]), (114, [(60, "Name of Enterprise")]),
            (160, [(60, "उद्यम पंजीकरण संख्या / Udyam Registration Number")]), (174, [(60, "UDYAM-MH-12-0001234")])]
    _, f = run_doc(L.doc_from_pages(L.page_from_rows(rows)), DT.UDYAM)
    assert f["enterprise_name"].normalized_value == "SUNRISE TEXTILES" and f["enterprise_name"].position == "ABOVE"
    assert f["enterprise_name"].confidence < 0.8  # weakest position
    assert f["udyam_number"].normalized_value == "UDYAM-MH-12-0001234"
    assert f["udyam_number"].label == "Udyam Registration Number" and f["udyam_number"].position == "BELOW"


def test_value_inside_table_cells():
    g = L.gstin_for()
    form = RawTable(page_number=1, index=0, method="pdfplumber_lines",
                    rows=[["1.", "GSTIN", g], ["2.", "Legal Name", "SUNRISE TEXTILES PRIVATE LIMITED"]],
                    row_bboxes=[(40, 100, 500, 114), (40, 114, 500, 128)])
    page = L.page_from_rows([(100, [(40, "1."), (70, "GSTIN"), (250, g)]),
                             (114, [(40, "2."), (70, "Legal Name"), (250, "SUNRISE TEXTILES PRIVATE LIMITED")])])
    _, f = run_doc(L.doc_from_pages(page), DT.GST_CERTIFICATE, [form])
    assert f["gstin"].normalized_value == g
    header_style = RawTable(page_number=1, index=1, method="pdfplumber_lines",
                            rows=[["Udyam Registration Number", "Date of Udyam Registration"],
                                  ["UDYAM-MH-12-0001234", "15/09/2020"]],
                            row_bboxes=[(40, 200, 500, 214), (40, 214, 500, 228)])
    blank = L.page_from_rows([(300, [(40, "UDYAM REGISTRATION CERTIFICATE")])])
    _, u = run_doc(L.doc_from_pages(blank), DT.UDYAM, [header_style])
    assert u["registration_date"].normalized_value == "2020-09-15"
    assert u["registration_date"].position == "TABLE_BELOW" and u["registration_date"].bbox == (40, 214, 500, 228)


def test_multiline_address_follows_its_column():
    rows = [(100, [(50, "Address of Principal Place of Business"), (330, "Constitution of Business")]),
            (114, [(50, "Plot 14, SIDCO Industrial Estate"), (330, "Proprietorship")]),
            (126, [(50, "Kurichi, Coimbatore 641021")]),
            (150, [(50, "Date of Liability"), (330, "01/07/2017")])]
    _, f = run_doc(L.doc_from_pages(L.page_from_rows(rows)), DT.GST_CERTIFICATE)
    assert f["principal_address"].normalized_value == "Plot 14, SIDCO Industrial Estate, Kurichi, Coimbatore 641021"
    assert f["business_type"].normalized_value == "Proprietorship"
    assert f["registration_date"].normalized_value == "2017-07-01"


# =========================================================================== normalisation
@pytest.mark.parametrize("raw,expected,matched", [
    ("₹1,25,000", "125000.00", "₹1,25,000"),
    ("1,25,000.00", "125000.00", "1,25,000.00"),
    ("12,500 Dr", "-12500.00", "12,500 Dr"),
    ("12,500 Cr", "12500.00", "12,500 Cr"),
    ("12,500.00CR", "12500.00", "12,500.00CR"),
    ("Rs.1,25,000/-", "125000.00", "Rs.1,25,000/-"),
    ("(1,25,000)", "-125000.00", "(1,25,000)"),
    ("INR 1,25,000.00", "125000.00", "INR 1,25,000.00"),
    ("Gross Total Income 1 905000", "905000.00", "905000"),
])
def test_indian_amount_formats(raw, expected, matched):
    r = normalize("amount", raw)
    assert r.normalized == expected and r.matched == matched


def test_dates_percent_and_identifiers_normalise_without_losing_raw():
    assert normalize("date", "Date of filing: 05-Apr-2024").normalized == "2024-04-05"
    assert normalize("date", "2024/04/05").normalized == "2024-04-05"
    assert normalize("date", "31/02/2024").error  # invalid date is an error, not a guess
    assert normalize("percent", "18 %").normalized == "18"
    assert normalize("gstin", "GSTIN : 33aaecs4821k1zd").normalized == "33AAECS4821K1ZD"
    assert N.detect_amount_unit("(Rs. in Lakhs)")[0] == Decimal(100000)


# =========================================================================== financial statements
def test_tformat_proprietor_pl(write):
    parsed, res, f = run_pdf(write, "trading_pl.pdf", L.tformat_pl(), DT.PROFIT_LOSS)
    assert f["revenue"].typed_value == Decimal("6820000")  # "By Sales" on the Cr side
    assert f["cost_of_goods_sold"].typed_value == Decimal("4092000")  # "To Purchases" on the Dr side
    assert f["gross_profit"].typed_value == Decimal("2788000")
    assert f["depreciation"].typed_value == Decimal("253000")
    assert f["interest"].typed_value == Decimal("185000")
    assert f["profit_after_tax"].typed_value == Decimal("1335000")
    assert f["period"].normalized_value == "2024-25"
    assert f["profit_before_tax"].extraction_status == ExtractionStatus.NOT_FOUND
    # the two sides of one printed line are separate rows with their own boxes
    sales_row_box, purchases_box = f["revenue"].bbox, f["cost_of_goods_sold"].bbox
    assert sales_row_box[0] > 300 and purchases_box[2] < 300
    assert_traceable(f["revenue"], parsed)


def test_amounts_in_lakhs_are_scaled_raw_kept(write):
    parsed, res, f = run_pdf(write, "pl_lakhs.pdf", L.pl_in_lakhs(), DT.PROFIT_LOSS)
    assert f["revenue"].raw_value == "682.40"
    assert f["revenue"].typed_value == Decimal("68240000.00")
    assert f["profit_after_tax"].typed_value == Decimal("6770000.00")  # "Profit for the year"
    assert any(w.startswith("UNIT_SCALED") for w in f["revenue"].warnings)
    assert any(w.startswith("AMOUNT_UNIT") for w in res.warnings)


def test_itr_v_row_numbers_and_unformatted_amounts(write):
    parsed, res, f = run_pdf(write, "itrv.pdf", L.itr_v_unformatted(), DT.ITR)
    assert f["gross_total_income"].typed_value == Decimal("905000")
    assert f["gross_total_income"].raw_value == "905000"  # not the row number "1"
    assert f["taxable_income"].typed_value == Decimal("905000")
    assert f["tax_paid"].typed_value == Decimal("228000")
    assert f["itr_form"].normalized_value == "ITR-6"  # second pair on the PAN line
    assert any(w.startswith("UNFORMATTED_NUMBER") for w in f["tax_paid"].warnings)
    assert f["gross_total_income"].confidence_factors["format"] == 0.92


# =========================================================================== missing / ambiguous
def test_ambiguous_conflicting_values_are_flagged():
    g1, g2 = L.gstin_for("AAACS1234A"), L.gstin_for("AAACS9999A")
    rows = [(100, [(50, "GSTIN:"), (200, g1)]), (300, [(50, "GSTIN:"), (200, g2)])]
    _, f = run_doc(L.doc_from_pages(L.page_from_rows(rows)), DT.GST_CERTIFICATE)
    gst = f["gstin"]
    assert gst.extraction_status == ExtractionStatus.AMBIGUOUS
    assert {a["normalized_value"] for a in gst.alternatives} == {g2}
    assert gst.confidence_factors["ambiguity"] == 0.7
    assert f["legal_name"].extraction_status == ExtractionStatus.NOT_FOUND


# =========================================================================== persistence through the API
def test_page_artefacts_and_field_provenance_persisted(client, business, fake_ocr):
    pan_pdf = S.pan_card_pdf(business.pan, business.legal_name)
    fake_ocr(pan_pdf)
    app = create_app(client)
    data = L.mixed_pdf(pan_pdf, pan_pdf)  # e-PAN page + a scanned copy of the card
    doc = upload(client, app["id"], {"pan_with_scan.pdf": data})["documents"][0]
    assert client.get(f"/api/documents/{doc['id']}").json()["document_type"] == "PAN"
    pages = client.get(f"/api/documents/{doc['id']}/pages").json()
    assert [p["text_source"] for p in pages] == ["TEXT_LAYER", "OCR"]
    assert pages[0]["text_quality"]["decision"] == "TEXT_LAYER" and pages[0]["blocks"]
    assert pages[1]["text_quality"]["reason"] == "NO_TEXT_LAYER"
    raw = client.get(f"/api/documents/{doc['id']}/raw").json()
    assert raw["pages"][1]["ocr_output"]["words"], "raw OCR output stored in the extraction artefact"
    fields = {x["field_name"]: x for x in client.get(f"/api/documents/{doc['id']}/extraction").json()["fields"]}
    pan = fields["pan"]
    assert pan["document_id"] == doc["id"]
    assert pan["extraction_status"] == "EXTRACTED" and pan["normalized_value"] == business.pan
    assert pan["source_page"] == 1 and pan["source_location"]["bbox"]
    assert pan["source_location"]["text_source"] == "TEXT_LAYER"
    assert pan["source_location"]["confidence_factors"]["final"] == pan["confidence"]
    assert business.pan in pan["source_snippet"]
