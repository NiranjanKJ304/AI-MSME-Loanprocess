"""Extractor registry: DocumentType -> extractor. Types without a structured schema still get
their raw text and tables persisted (GenericExtractor) and a NO_STRUCTURED_SCHEMA warning."""

from __future__ import annotations

from app.document_ai.extractor import BaseExtractor, ExtractionContext, ExtractionResult
from app.document_ai.extractors.bank_statement import BankStatementExtractor
from app.document_ai.extractors.financials import BalanceSheetExtractor, ProfitLossExtractor
from app.document_ai.extractors.identity import (
    BusinessRegistrationExtractor,
    GSTCertificateExtractor,
    GSTReturnExtractor,
    ITRExtractor,
    PANExtractor,
    UdyamExtractor,
)
from app.models.enums import DocumentType


class GenericExtractor(BaseExtractor):
    llm_fallback_enabled = False

    def __init__(self, document_type: DocumentType):
        self._doc_type = document_type

    def extract(self, ctx: ExtractionContext, tables=None) -> ExtractionResult:
        result = super().extract(ctx, tables)
        result.document_type = self._doc_type
        result.status = "UNSUPPORTED"
        result.warnings.append(
            f"UNSUPPORTED: no field parser for {self._doc_type.value} in this prototype; "
            "raw text and tables were preserved (NO_STRUCTURED_SCHEMA)"
        )
        return result


EXTRACTORS: dict[DocumentType, type[BaseExtractor]] = {
    DocumentType.PAN: PANExtractor,
    DocumentType.GST_CERTIFICATE: GSTCertificateExtractor,
    DocumentType.GST_RETURN: GSTReturnExtractor,
    DocumentType.UDYAM: UdyamExtractor,
    DocumentType.BANK_STATEMENT: BankStatementExtractor,
    DocumentType.ITR: ITRExtractor,
    DocumentType.PROFIT_LOSS: ProfitLossExtractor,
    DocumentType.BALANCE_SHEET: BalanceSheetExtractor,
    DocumentType.BUSINESS_REGISTRATION: BusinessRegistrationExtractor,
}


def get_extractor(document_type: DocumentType) -> BaseExtractor:
    cls = EXTRACTORS.get(document_type)
    return cls() if cls else GenericExtractor(document_type)


def schema_catalog() -> dict[str, dict]:
    """Field catalogue per document type (for the API / frontend)."""
    out: dict[str, dict] = {}
    for dt, cls in EXTRACTORS.items():
        out[dt.value] = {
            "fields": [
                {"name": s.name, "type": s.value_type, "required": s.required} for s in cls.field_specs
            ],
            "schema": cls.schema.model_json_schema() if cls.schema else None,
        }
    return out
