from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, Field


class Transaction(BaseModel):
    date: dt.date | None = None
    value_date: dt.date | None = None
    description: str | None = None
    reference: str | None = None
    debit: Decimal | None = None
    credit: Decimal | None = None
    balance: Decimal | None = None


class BankStatementSchema(BaseModel):
    account_number: str | None = None
    account_holder: str | None = None
    bank_name: str | None = None
    ifsc: str | None = None
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    transactions: list[Transaction] = Field(default_factory=list)
