"""Pydantic schemas for the structured layer, one per supported document type."""

from app.document_ai.schemas.balance_sheet import BalanceSheetSchema
from app.document_ai.schemas.bank_statement import BankStatementSchema, Transaction
from app.document_ai.schemas.business_registration import BusinessRegistrationSchema
from app.document_ai.schemas.gst import GSTCertificateSchema, GSTReturnSchema
from app.document_ai.schemas.itr import ITRSchema
from app.document_ai.schemas.pan import PANSchema
from app.document_ai.schemas.profit_loss import ProfitLossSchema
from app.document_ai.schemas.udyam import UdyamSchema

__all__ = [
    "BalanceSheetSchema",
    "BankStatementSchema",
    "BusinessRegistrationSchema",
    "GSTCertificateSchema",
    "GSTReturnSchema",
    "ITRSchema",
    "PANSchema",
    "ProfitLossSchema",
    "Transaction",
    "UdyamSchema",
]
