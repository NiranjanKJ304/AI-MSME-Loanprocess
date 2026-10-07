from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.document_ai import normalizers as N
from app.models import ExtractedField
from app.models.enums import ExtractionMethod, FieldValidationStatus
from app.validation.field_validation import validate_field
from app.validation.reconciliation import name_similarity
from app.validation.rules import gstin_checksum_ok
from scripts.synthetic_docs import make_gstin


def field(name, value_type, raw, normalized=None, confidence=0.95, missing=False, required=False,
          method=ExtractionMethod.LABEL, page=1):
    return ExtractedField(
        field_name=name, value_type=value_type, raw_value=raw,
        normalized_value=normalized if normalized is not None else raw, confidence=confidence,
        is_missing=missing, is_required=required, extraction_method=method, source_page=None if missing else page,
        warnings=None,
    )


# ---- PAN
def test_valid_pan():
    status, outcomes = validate_field(field("pan", "pan", "AAECS4821K"))
    assert status == FieldValidationStatus.VALID


@pytest.mark.parametrize("bad", ["AAECS4821", "1AECS4821K", "AAECS48211"])
def test_invalid_pan(bad):
    status, outcomes = validate_field(field("pan", "pan", bad))
    assert status == FieldValidationStatus.INVALID
    assert any(o.rule_code == "PAN_FORMAT" for o in outcomes if o.is_problem)


# ---- GSTIN (format + checksum + state code)
def test_valid_gstin_checksum():
    g = make_gstin("AAECS4821K", "33")
    assert gstin_checksum_ok(g)
    status, _ = validate_field(field("gstin", "gstin", g))
    assert status == FieldValidationStatus.VALID


def test_invalid_gstin_checksum():
    g = make_gstin("AAECS4821K", "33")
    wrong = g[:-1] + ("A" if g[-1] != "A" else "B")
    status, outcomes = validate_field(field("gstin", "gstin", wrong))
    assert status == FieldValidationStatus.INVALID
    assert any(o.rule_code == "GSTIN_CHECKSUM" and o.is_problem for o in outcomes)


def test_invalid_gstin_state_code():
    g = make_gstin("AAECS4821K", "77")
    status, outcomes = validate_field(field("gstin", "gstin", g))
    assert any(o.rule_code == "GSTIN_STATE_CODE" for o in outcomes)
    assert status == FieldValidationStatus.INVALID


def test_ifsc_format():
    assert validate_field(field("ifsc", "ifsc", "KCBL0000212"))[0] == FieldValidationStatus.VALID
    assert validate_field(field("ifsc", "ifsc", "KCBL1000212"))[0] == FieldValidationStatus.INVALID


# ---- dates / amounts / confidence / missing
def test_future_date_is_invalid():
    future = (date.today() + timedelta(days=30)).isoformat()
    status, outcomes = validate_field(field("filing_date", "date", future))
    assert status == FieldValidationStatus.INVALID
    assert outcomes[0].rule_code == "DATE_NOT_FUTURE"


def test_negative_amount_flagged_when_non_negative():
    status, outcomes = validate_field(field("revenue", "amount", "-100.00"), non_negative=True)
    assert status == FieldValidationStatus.INVALID
    assert outcomes[0].rule_code == "AMOUNT_NON_NEGATIVE"


def test_low_confidence_needs_review():
    status, outcomes = validate_field(field("legal_name", "name", "ABC", confidence=0.4))
    assert status == FieldValidationStatus.NEEDS_REVIEW
    assert outcomes[-1].rule_code == "LOW_CONFIDENCE"


def test_missing_required_field():
    status, outcomes = validate_field(field("gstin", "gstin", None, missing=True, required=True))
    assert status == FieldValidationStatus.MISSING
    assert outcomes[0].rule_code == "REQUIRED_FIELD"


def test_llm_sourced_value_needs_review():
    status, outcomes = validate_field(field("legal_name", "name", "ABC", confidence=0.6, method=ExtractionMethod.LLM))
    assert status == FieldValidationStatus.NEEDS_REVIEW
    assert {"LLM_SOURCED", "LOW_CONFIDENCE"} <= {o.rule_code for o in outcomes}


# ---- normalisers
@pytest.mark.parametrize("raw,expected", [
    ("68,20,000.00", Decimal("6820000.00")),
    ("Rs. 1,250.50", Decimal("1250.50")),
    ("(12,000)", Decimal("-12000")),
    ("1,200.00 Dr", Decimal("1200.00")),
    ("-", None),
    ("abc", None),
])
def test_parse_amount(raw, expected):
    assert N.parse_amount(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("05/04/2024", date(2024, 4, 5)),
    ("05-04-24", date(2024, 4, 5)),
    ("2024-04-05", date(2024, 4, 5)),
    ("5th April 2024", date(2024, 4, 5)),
    ("05 Apr 2024", date(2024, 4, 5)),
    ("31/02/2024", None),
    ("XX/XX/XXXX", None),
])
def test_parse_date(raw, expected):
    assert N.parse_date(raw) == expected


def test_amount_regex_ignores_date_fragments():
    assert N.find_amounts("Particulars 31.03.2025 31.03.2024") == []
    assert [a[0] for a in N.find_amounts("Revenue 68,20,000.00  59,10,000.00")] == ["68,20,000.00", "59,10,000.00"]


def test_fiscal_year_helpers():
    assert N.parse_fy("FY 2024-25") == "2024-25"
    assert N.assessment_year_to_fy("2025-26") == "2024-25"
    assert N.fy_label(date(2025, 3, 31)) == "2024-25"


def test_name_similarity_handles_legal_suffixes():
    assert name_similarity("Sri Lakshmi Precision Components Pvt. Ltd.",
                           "SRI LAKSHMI PRECISION COMPONENTS PRIVATE LIMITED") == 1.0
    assert name_similarity("Sunrise Textiles", "SRI LAKSHMI PRECISION COMPONENTS PRIVATE LIMITED") < 0.6
