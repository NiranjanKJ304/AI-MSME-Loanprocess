from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class BalanceSheetSchema(BaseModel):
    period: str | None = None  # financial year, e.g. 2024-25 (as at 31 March 2025)
    fixed_assets: Decimal | None = None
    inventory: Decimal | None = None
    receivables: Decimal | None = None
    cash: Decimal | None = None
    bank_balance: Decimal | None = None
    other_current_assets: Decimal | None = None
    current_assets: Decimal | None = None
    total_assets: Decimal | None = None
    capital: Decimal | None = None
    reserves: Decimal | None = None
    borrowings: Decimal | None = None
    trade_payables: Decimal | None = None
    current_liabilities: Decimal | None = None
    net_worth: Decimal | None = None
    other_liabilities: Decimal | None = None
    total_liabilities: Decimal | None = None
