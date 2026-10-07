from __future__ import annotations

import pytest

from app.document_ai.classifier import DocumentClassifier
from app.document_ai.pdf_parser import parse_document
from app.document_ai.table_extractor import extract_tables
from app.llm import set_llm_provider
from app.llm.provider import LLMProvider
from app.models.enums import ClassificationMethod, DocumentType
from scripts import synthetic_docs as S
from tests.conftest import sample_documents

EXPECTED = {
    "PAN_card.pdf": DocumentType.PAN,
    "GST_REG06.pdf": DocumentType.GST_CERTIFICATE,
    "GSTR9_FY2024-25.pdf": DocumentType.GST_RETURN,
    "Udyam_certificate.pdf": DocumentType.UDYAM,
    "Certificate_of_Incorporation.pdf": DocumentType.BUSINESS_REGISTRATION,
    "ITR_AY2025-26.pdf": DocumentType.ITR,
    "PL_FY2024-25.pdf": DocumentType.PROFIT_LOSS,
    "Balance_Sheet_FY2024-25.pdf": DocumentType.BALANCE_SHEET,
    "Bank_statement_FY2024-25.pdf": DocumentType.BANK_STATEMENT,
}


def _classify(write, name, data):
    path = write(name, data)
    parsed = parse_document(path, "application/pdf")
    tables, _ = extract_tables(path, parsed)
    return DocumentClassifier().classify(name, parsed, tables)


@pytest.mark.parametrize("name", list(EXPECTED))
def test_classifies_sample_documents(write, business, name):
    result = _classify(write, name, sample_documents(business)[name])
    assert result.document_type == EXPECTED[name]
    assert result.confidence >= 0.6
    assert not result.needs_review


def test_content_beats_misleading_filename(write, business):
    # A GST certificate saved as "bank_statement.pdf" is still a GST certificate.
    result = _classify(write, "bank_statement.pdf", S.gst_certificate_pdf(business))
    assert result.document_type == DocumentType.GST_CERTIFICATE


def test_filename_alone_is_not_enough(write):
    result = _classify(write, "gst_certificate.pdf", S.unknown_document_pdf())
    assert result.confidence < 0.6
    assert result.needs_review
    assert any("FILENAME_ONLY" in w for w in result.warnings)


def test_unknown_document(write):
    result = _classify(write, "minutes.pdf", S.unknown_document_pdf())
    assert result.document_type == DocumentType.UNKNOWN
    assert result.needs_review


class _FakeLLM(LLMProvider):
    name = "fake"

    def __init__(self, payload: str):
        self.payload = payload

    def available(self) -> bool:
        return True

    def _complete_raw(self, system, user, schema):
        return self.payload


def test_llm_fallback_used_only_when_rules_are_weak(write):
    text_pdf = S.PdfWriter()
    text_pdf.text(50, "Deed of hypothecation of plant and machinery", 11)
    set_llm_provider(_FakeLLM(
        '{"document_type": "LOAN_STATEMENT", "confidence": 0.95, '
        '"evidence_quote": "Deed of hypothecation of plant and machinery"}'
    ))
    result = _classify(write, "doc.pdf", text_pdf.bytes())
    assert result.method == ClassificationMethod.LLM
    assert result.confidence <= 0.85  # LLM confidence is capped


def test_llm_hallucinated_quote_is_not_trusted(write):
    text_pdf = S.PdfWriter()
    text_pdf.text(50, "Deed of hypothecation of plant and machinery", 11)
    set_llm_provider(_FakeLLM(
        '{"document_type": "LOAN_STATEMENT", "confidence": 0.95, "evidence_quote": "Loan Account Statement"}'
    ))
    result = _classify(write, "doc.pdf", text_pdf.bytes())
    assert result.confidence <= 0.5
    assert result.needs_review


def test_llm_output_must_match_schema(write):
    set_llm_provider(_FakeLLM('{"document_type": "NOT_A_TYPE", "confidence": 2}'))
    result = _classify(write, "doc.pdf", S.unknown_document_pdf())
    assert any("LLM_CLASSIFIER_FAILED" in w for w in result.warnings)
    assert result.needs_review
