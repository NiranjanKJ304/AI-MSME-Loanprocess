from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class ProfitLossSchema(BaseModel):
    period: str | None = None  # financial year, e.g. 2024-25
    revenue: Decimal | None = None
    other_income: Decimal | None = None
    cost_of_goods_sold: Decimal | None = None
    gross_profit: Decimal | None = None
    operating_expenses: Decimal | None = None
    operating_profit: Decimal | None = None
    ebitda: Decimal | None = None
    depreciation: Decimal | None = None
    interest: Decimal | None = None
    profit_before_tax: Decimal | None = None
    tax: Decimal | None = None
    profit_after_tax: Decimal | None = None
