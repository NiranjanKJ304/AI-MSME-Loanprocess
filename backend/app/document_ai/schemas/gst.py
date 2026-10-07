from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class GSTCertificateSchema(BaseModel):
    gstin: str | None = None
    legal_name: str | None = None
    trade_name: str | None = None
    registration_date: date | None = None
    business_type: str | None = None  # "Constitution of Business"
    principal_address: str | None = None
    status: str | None = None


class GSTReturnSchema(BaseModel):
    gstin: str | None = None
    legal_name: str | None = None
    return_type: str | None = None  # GSTR-1 / GSTR-3B / GSTR-9
    tax_period: str | None = None  # e.g. "March 2025" or "2024-25"
    financial_year: str | None = None
    filing_date: date | None = None
    taxable_turnover: Decimal | None = None
    total_tax: Decimal | None = None
