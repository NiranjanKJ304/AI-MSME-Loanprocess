from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class ITRSchema(BaseModel):
    """Flexible ITR schema: any of these may be absent depending on the ITR form."""

    pan: str | None = None
    name: str | None = None
    itr_form: str | None = None
    assessment_year: str | None = Field(default=None, description="e.g. 2025-26")
    gross_total_income: Decimal | None = None
    business_income: Decimal | None = None
    taxable_income: Decimal | None = None
    tax_paid: Decimal | None = None
    turnover: Decimal | None = None
    filing_date: date | None = None
    acknowledgement_number: str | None = None
