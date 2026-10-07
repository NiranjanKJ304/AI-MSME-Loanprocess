"""Declarative label/pattern extractors for registration & identity documents."""

from __future__ import annotations

import re

from app.document_ai.extractor import (
    BaseExtractor,
    ExtractionContext,
    FieldCandidate,
    FieldSpec,
    TableResult,
)
from app.document_ai.normalizers import CIN_RE
from app.document_ai.schemas import (
    BusinessRegistrationSchema,
    GSTCertificateSchema,
    GSTReturnSchema,
    ITRSchema,
    PANSchema,
    UdyamSchema,
)
from app.models.enums import DocumentType, ExtractionMethod

PAN_PAT = r"[A-Z]{5}[0-9]{4}[A-Z]"
GSTIN_PAT = r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]"
YEAR_PAT = r"20\d{2}\s*[-–]\s*\d{2,4}"
NAME_LABEL = r"name(?=\s*:|\s{2,}|\s*$)"  # bare "Name" only when followed by ':' / gap / EOL

PAN_ENTITY_TYPES = {
    "P": "Individual",
    "C": "Company",
    "H": "Hindu Undivided Family (HUF)",
    "F": "Firm / LLP",
    "A": "Association of Persons (AOP)",
    "T": "Trust",
    "B": "Body of Individuals (BOI)",
    "L": "Local Authority",
    "J": "Artificial Juridical Person",
    "G": "Government",
}


class PANExtractor(BaseExtractor):
    document_type = DocumentType.PAN
    schema = PANSchema
    field_specs = [
        FieldSpec(
            "pan", "pan",
            labels=[r"permanent\s+account\s+number(?:\s+card)?", r"pan(?:\s*(?:no\.?|number))?"],
            pattern=PAN_PAT, unlabeled=True, required=True,
        ),
        FieldSpec("name", "name", labels=[NAME_LABEL], required=True),
        FieldSpec("date_of_birth", "date", labels=[r"date\s+of\s+(?:birth|incorporation)(?:\s*/\s*\w+)?", r"dob"]),
    ]

    def post_process(self, ctx, found, tables, warnings):
        pan = found.get("pan")
        if pan and pan.normalized_value and len(pan.normalized_value) == 10:
            code = pan.normalized_value[3]
            label = PAN_ENTITY_TYPES.get(code)
            if label:
                found["entity_type"] = FieldCandidate(
                    name="entity_type", value_type="string", raw_value=code,
                    normalized_value=label, typed_value=label,
                    confidence=round(min(pan.confidence, 0.95), 3), page=pan.page, bbox=pan.bbox,
                    page_size=pan.page_size, snippet=pan.snippet, method=ExtractionMethod.DERIVED,
                    position="DERIVED", text_source=pan.text_source,
                    confidence_factors={"derived_from": "pan", "base": min(pan.confidence, 0.95)},
                    warnings=[f"DERIVED: 4th character of PAN '{code}' => {label}"],
                )


class GSTCertificateExtractor(BaseExtractor):
    document_type = DocumentType.GST_CERTIFICATE
    schema = GSTCertificateSchema
    field_specs = [
        FieldSpec(
            "gstin", "gstin", labels=[r"registration\s+number", r"gstin(?:\s*/\s*uin)?"],
            pattern=GSTIN_PAT, unlabeled=True, required=True,
        ),
        FieldSpec("legal_name", "name", labels=[r"legal\s+name(?:\s+of\s+business)?"], required=True),
        FieldSpec("trade_name", "name", labels=[r"trade\s+name(?:,?\s+if\s+any)?"]),
        FieldSpec(
            "registration_date", "date",
            labels=[r"date\s+of\s+liability", r"date\s+of\s+registration", r"registration\s+date",
                    r"(?:period\s+of\s+)?validity\s+from"],
        ),
        FieldSpec("business_type", "string", labels=[r"constitution\s+of\s+business"]),
        FieldSpec(
            "principal_address", "text",
            labels=[r"address\s+of\s+principal\s+place\s+of\s*(?:business)?"], multiline=True,
        ),
        FieldSpec("status", "enum", labels=[r"(?:registration\s+|gstin\s+)?status"]),
    ]


class GSTReturnExtractor(BaseExtractor):
    document_type = DocumentType.GST_RETURN
    schema = GSTReturnSchema
    field_specs = [
        FieldSpec("gstin", "gstin", labels=[r"gstin(?:\s*/\s*uin)?"], pattern=GSTIN_PAT, unlabeled=True, required=True),
        FieldSpec("legal_name", "name", labels=[r"legal\s+name(?:\s+of\s+(?:the\s+)?registered\s+person)?"]),
        FieldSpec("return_type", "code", pattern=r"GSTR[-\s]?(?:3B|9C|1|9|4)\b", unlabeled=True, required=True),
        FieldSpec("tax_period", "string", labels=[r"(?:tax|return)\s+period", r"period"]),
        FieldSpec("financial_year", "fy", labels=[r"financial\s+year", r"f\.?y\.?"], pattern=YEAR_PAT),
        FieldSpec("filing_date", "date", labels=[r"date\s+of\s+filing", r"filing\s+date", r"arn\s+date"]),
        FieldSpec(
            "taxable_turnover", "amount", non_negative=True,
            labels=[r"total\s+taxable\s+(?:value|turnover)", r"aggregate\s+turnover", r"taxable\s+turnover",
                    r"\(?a\)?\s*outward\s+taxable\s+supplies[^0-9]*"],
        ),
        FieldSpec("total_tax", "amount", non_negative=True, labels=[r"total\s+tax(?:\s+(?:payable|paid|liability))?"]),
    ]


class UdyamExtractor(BaseExtractor):
    document_type = DocumentType.UDYAM
    schema = UdyamSchema
    field_specs = [
        FieldSpec(
            "udyam_number", "udyam", labels=[r"udyam\s+registration\s+(?:number|no\.?)"],
            pattern=r"UDYAM-[A-Z]{2}-\d{2}-\d{7}", unlabeled=True, required=True,
        ),
        FieldSpec("enterprise_name", "name", labels=[r"name\s+of\s+(?:the\s+)?enterprise", r"enterprise\s+name"], required=True),
        FieldSpec(
            "registration_date", "date",
            labels=[r"date\s+of\s+udyam\s+registration", r"date\s+of\s+registration", r"registration\s+date"],
        ),
        FieldSpec("enterprise_type", "enum", labels=[r"type\s+of\s+enterprise", r"enterprise\s+type", r"classification"]),
        FieldSpec("major_activity", "enum", labels=[r"major\s+activity"]),
        FieldSpec("nic_code", "code", labels=[r"nic\s*(?:code)?(?:\s*\(\d\s*digit\))?", r"national\s+industr\w+\s+classification\s+code"], pattern=r"\b\d{2,5}\b"),
    ]

    def post_process(self, ctx, found, tables, warnings):
        et = found.get("enterprise_type")
        if et and et.normalized_value:
            m = re.search(r"\b(micro|small|medium)\b", et.normalized_value, re.IGNORECASE)
            if m:
                et.normalized_value = et.typed_value = m.group(1).upper()


class ITRExtractor(BaseExtractor):
    document_type = DocumentType.ITR
    schema = ITRSchema
    field_specs = [
        FieldSpec("pan", "pan", labels=[r"pan"], pattern=PAN_PAT, unlabeled=True, required=True),
        FieldSpec("name", "name", labels=[r"name\s+of\s+(?:the\s+)?assessee", r"assessee\s+name", NAME_LABEL]),
        FieldSpec("itr_form", "code", labels=[r"form\s*(?:no\.?|number)?"], pattern=r"ITR[-\s]?[1-7]\b", unlabeled=True),
        FieldSpec("assessment_year", "ay", labels=[r"assessment\s+year", r"a\.?\s?y\.?"], pattern=YEAR_PAT, required=True),
        FieldSpec("gross_total_income", "amount", labels=[r"gross\s+total\s+income"]),
        FieldSpec(
            "business_income", "amount",
            labels=[r"profits?\s+and\s+gains\s+(?:of|from)\s+business(?:\s+or\s+profession)?",
                    r"income\s+from\s+business(?:\s+or\s+profession)?", r"business\s+income"],
        ),
        FieldSpec("taxable_income", "amount", non_negative=True, labels=[r"total\s+taxable\s+income", r"taxable\s+income", r"total\s+income"]),
        FieldSpec("tax_paid", "amount", non_negative=True, labels=[r"total\s+taxes\s+paid", r"taxes\s+paid", r"tax\s+paid"]),
        FieldSpec(
            "turnover", "amount", non_negative=True,
            labels=[r"gross\s+turnover(?:\s+or\s+gross\s+receipts)?", r"turnover", r"gross\s+receipts"],
        ),
        FieldSpec("filing_date", "date", labels=[r"date\s+of\s+filing", r"(?:e-?)?filed\s+on", r"e-?filing\s+date"]),
        FieldSpec(
            "acknowledgement_number", "code",
            labels=[r"acknowledg(?:e)?ment\s+(?:no\.?|number)"], pattern=r"\d{12,15}",
        ),
    ]


class BusinessRegistrationExtractor(BaseExtractor):
    document_type = DocumentType.BUSINESS_REGISTRATION
    schema = BusinessRegistrationSchema
    field_specs = [
        FieldSpec(
            "registration_number", "code", required=True,
            labels=[r"corporate\s+identity\s+number", r"cin", r"llpin",
                    r"registration\s+(?:no\.?|number)", r"licen[cs]e\s+(?:no\.?|number)"],
        ),
        FieldSpec(
            "entity_name", "name", required=True,
            labels=[r"name\s+of\s+(?:the\s+)?(?:company|establishment|firm|llp|business|proprietor)",
                    r"entity\s+name", NAME_LABEL],
        ),
        FieldSpec(
            "registration_date", "date",
            labels=[r"date\s+of\s+(?:incorporation|registration|issue)", r"incorporated\s+on"],
        ),
        FieldSpec("registering_authority", "string", labels=[r"issued\s+by", r"registering\s+authority", r"authority"]),
        FieldSpec("address", "text", labels=[r"(?:registered\s+)?address(?:\s+of\s+(?:the\s+)?establishment)?"], multiline=True),
    ]

    _TYPES = [
        (r"certificate\s+of\s+incorporation", "Certificate of Incorporation"),
        (r"shops?\s+(?:and|&)\s+establishments?", "Shop & Establishment Registration"),
        (r"trade\s+licen[cs]e", "Trade Licence"),
    ]

    def post_process(self, ctx: ExtractionContext, found: dict[str, FieldCandidate], tables: list[TableResult], warnings: list[str]) -> None:
        for page, _, line in ctx.lines():
            for pat, label in self._TYPES:
                m = re.search(pat, line.text, re.IGNORECASE)
                if m:
                    c = ctx.candidate(
                        FieldSpec("certificate_type", "string"), m.group(0), page=page,
                        bbox=line.bbox_for_span(m.start(), m.end()) if line.words else None, snippet=line.text,
                        method=ExtractionMethod.REGEX, position="PATTERN", base_conf=0.9,
                        word_conf=line.ocr_conf_for_span(m.start(), m.end()) if line.words else None,
                    )
                    c.normalized_value = c.typed_value = label
                    found["certificate_type"] = c
                    break
            if "certificate_type" in found:
                break
        reg = found.get("registration_number")
        if reg and reg.raw_value:
            m = CIN_RE.search(reg.raw_value.replace(" ", ""))
            if m:
                reg.normalized_value = reg.typed_value = m.group(1)
