"""Field-level validation: required, format, numeric, date and confidence rules.

Each rule yields a RuleOutcome; the field's validation_status is derived from them:
  MISSING      field not present in the document
  INVALID      at least one FAIL (format / date / numeric / sign)
  NEEDS_REVIEW low confidence, conflicting candidates, LLM-sourced, OCR-sourced low quality
  VALID        otherwise
"""

from __future__ import annotations

from datetime import date

from app.config import get_settings
from app.document_ai.normalizers import parse_date
from app.models import ExtractedField
from app.models.enums import CheckStatus, ExtractionMethod, ExtractionStatus, FieldValidationStatus, Severity
from app.validation.rules import (
    CIN_FULL,
    IFSC_FULL,
    PAN_FULL,
    UDYAM_FULL,
    VALID_GST_STATE_CODES,
    GSTIN_FULL,
    RuleOutcome,
    dec,
    gstin_check_char,
)

ERR, WARN, INFO = Severity.ERROR, Severity.WARNING, Severity.INFO


def _format_rule(f: ExtractedField) -> list[RuleOutcome]:
    vt, v, raw = f.value_type, f.normalized_value, f.raw_value
    out: list[RuleOutcome] = []
    checks = {"pan": (PAN_FULL, "PAN", "AAAAA9999A"), "ifsc": (IFSC_FULL, "IFSC", "AAAA0XXXXXX"),
              "udyam": (UDYAM_FULL, "UDYAM", "UDYAM-XX-00-0000000"), "cin": (CIN_FULL, "CIN", "L/U + 20 chars")}
    if vt in checks:
        rx, name, shape = checks[vt]
        if not v or not rx.match(v):
            out.append(RuleOutcome(f"{name}_FORMAT", CheckStatus.FAIL, ERR,
                                   f"{f.field_name}: '{raw}' is not a valid {name} ({shape})",
                                   expected=shape, actual=raw))
        else:
            out.append(RuleOutcome(f"{name}_FORMAT", CheckStatus.PASS, INFO, f"{name} format valid"))
    if vt == "gstin":
        if not v or not GSTIN_FULL.match(v):
            out.append(RuleOutcome("GSTIN_FORMAT", CheckStatus.FAIL, ERR,
                                   f"{f.field_name}: '{raw}' is not a valid GSTIN format",
                                   expected="99AAAAA9999A9Z9", actual=raw))
        else:
            if v[:2] not in VALID_GST_STATE_CODES:
                out.append(RuleOutcome("GSTIN_STATE_CODE", CheckStatus.FAIL, ERR,
                                       f"GSTIN state code '{v[:2]}' is not a valid GST state code", actual=v[:2]))
            expected = gstin_check_char(v[:14])
            if expected != v[14]:
                out.append(RuleOutcome("GSTIN_CHECKSUM", CheckStatus.FAIL, ERR,
                                       f"GSTIN check digit mismatch: expected '{expected}', found '{v[14]}'",
                                       expected=expected, actual=v[14]))
            else:
                out.append(RuleOutcome("GSTIN_CHECKSUM", CheckStatus.PASS, INFO, "GSTIN checksum valid"))
            if not PAN_FULL.match(v[2:12]):
                out.append(RuleOutcome("GSTIN_EMBEDDED_PAN", CheckStatus.FAIL, ERR,
                                       f"Characters 3-12 of GSTIN ('{v[2:12]}') are not a valid PAN"))
    return out


def _type_rules(f: ExtractedField, non_negative: bool) -> list[RuleOutcome]:
    out: list[RuleOutcome] = []
    if f.value_type == "date":
        d = parse_date(f.normalized_value) if f.normalized_value else None
        if d is None:
            out.append(RuleOutcome("DATE_VALID", CheckStatus.FAIL, ERR,
                                   f"{f.field_name}: '{f.raw_value}' is not a valid date", actual=f.raw_value))
        else:
            if d > date.today():
                out.append(RuleOutcome("DATE_NOT_FUTURE", CheckStatus.FAIL, ERR,
                                       f"{f.field_name}: {d.isoformat()} is in the future", actual=d.isoformat()))
            elif d.year < 1900:
                out.append(RuleOutcome("DATE_PLAUSIBLE", CheckStatus.FAIL, ERR,
                                       f"{f.field_name}: {d.isoformat()} is implausibly old", actual=d.isoformat()))
    elif f.value_type == "amount":
        amount = dec(f.normalized_value)
        if amount is None:
            out.append(RuleOutcome("AMOUNT_NUMERIC", CheckStatus.FAIL, ERR,
                                   f"{f.field_name}: '{f.raw_value}' is not a valid amount", actual=f.raw_value))
        elif non_negative and amount < 0:
            out.append(RuleOutcome("AMOUNT_NON_NEGATIVE", CheckStatus.FAIL, ERR,
                                   f"{f.field_name} must be >= 0 (found {amount})", expected=">= 0",
                                   actual=str(amount)))
    elif f.value_type in ("fy", "ay"):
        if not f.normalized_value:
            out.append(RuleOutcome("PERIOD_FORMAT", CheckStatus.FAIL, ERR,
                                   f"{f.field_name}: '{f.raw_value}' is not a recognisable year (YYYY-YY)",
                                   actual=f.raw_value))
    elif f.value_type not in ("pan", "gstin", "ifsc", "udyam", "cin") and not f.normalized_value:
        out.append(RuleOutcome("VALUE_PRESENT", CheckStatus.FAIL, ERR,
                               f"{f.field_name}: label found but value is empty", actual=f.raw_value))
    return out


def validate_field(f: ExtractedField, non_negative: bool = False) -> tuple[FieldValidationStatus, list[RuleOutcome]]:
    threshold = get_settings().field_confidence_threshold
    ocr_required = f.extraction_status == ExtractionStatus.OCR_REQUIRED
    if f.is_missing:
        if f.extraction_status == ExtractionStatus.EXTRACTION_FAILED:
            return FieldValidationStatus.INVALID, [
                RuleOutcome("EXTRACTION_FAILED", CheckStatus.FAIL, ERR,
                            f"'{f.field_name}' could not be extracted: {'; '.join(f.warnings or [])}")
            ]
        if f.is_required:
            msg = (f"Required field '{f.field_name}' not found in the readable text; part of the document "
                   "has no text layer (OCR required)" if ocr_required
                   else f"Required field '{f.field_name}' was not found in the document")
            return FieldValidationStatus.MISSING, [RuleOutcome("REQUIRED_FIELD", CheckStatus.FAIL, ERR, msg)]
        return FieldValidationStatus.MISSING, [
            RuleOutcome("FIELD_NOT_FOUND", CheckStatus.INSUFFICIENT_DATA, INFO,
                        f"Optional field '{f.field_name}' not found in the document")
        ]

    outcomes = _format_rule(f) + _type_rules(f, non_negative)
    if f.confidence < threshold:
        outcomes.append(RuleOutcome("LOW_CONFIDENCE", CheckStatus.NEEDS_REVIEW, WARN,
                                    f"{f.field_name}: confidence {f.confidence:.2f} below threshold {threshold:.2f}",
                                    expected=f">= {threshold}", actual=f"{f.confidence:.2f}"))
    if f.extraction_status == ExtractionStatus.AMBIGUOUS:
        conflict = next((w for w in f.warnings or [] if w.startswith("CONFLICTING_VALUES")), "several candidates")
        outcomes.append(RuleOutcome("AMBIGUOUS_VALUE", CheckStatus.NEEDS_REVIEW, WARN, f"{f.field_name}: {conflict}"))
    else:
        for w in f.warnings or []:
            if w.startswith("CONFLICTING_VALUES"):
                outcomes.append(RuleOutcome("CONFLICTING_VALUES", CheckStatus.NEEDS_REVIEW, WARN, f"{f.field_name}: {w}"))
    for w in f.warnings or []:
        if w.startswith("OCR_CHARS_CORRECTED"):
            outcomes.append(RuleOutcome("OCR_CORRECTED_VALUE", CheckStatus.NEEDS_REVIEW, WARN, f"{f.field_name}: {w}"))
    if f.extraction_method == ExtractionMethod.LLM:
        outcomes.append(RuleOutcome("LLM_SOURCED", CheckStatus.NEEDS_REVIEW, WARN,
                                    f"{f.field_name}: value extracted by LLM fallback - verify against source"))
    if not f.source_page:
        outcomes.append(RuleOutcome("PROVENANCE_MISSING", CheckStatus.NEEDS_REVIEW, WARN,
                                    f"{f.field_name}: no source page recorded"))

    if any(o.status == CheckStatus.FAIL for o in outcomes):
        status = FieldValidationStatus.INVALID
    elif any(o.status == CheckStatus.NEEDS_REVIEW for o in outcomes):
        status = FieldValidationStatus.NEEDS_REVIEW
    else:
        status = FieldValidationStatus.VALID
    return status, outcomes
