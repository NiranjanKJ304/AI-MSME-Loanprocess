"""Canonical metric catalogue and the mapping from extracted fields to metrics.

Bank activity is deliberately its own category: total bank credits are NEVER mapped to
revenue or turnover - only to the BANKING metric `total_credits`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import DocumentType, MeasureType

DT = DocumentType
FLOW, STOCK = MeasureType.FLOW, MeasureType.STOCK


@dataclass(frozen=True)
class Metric:
    code: str
    category: str
    measure: MeasureType
    description: str


METRICS: dict[str, Metric] = {m.code: m for m in [
    # business
    Metric("turnover", "BUSINESS", FLOW, "Turnover / gross receipts as declared (ITR)"),
    Metric("revenue", "BUSINESS", FLOW, "Revenue from operations (P&L)"),
    Metric("other_income", "BUSINESS", FLOW, "Other / non-operating income (P&L)"),
    # profitability
    Metric("gross_profit", "PROFITABILITY", FLOW, "Gross profit"),
    Metric("operating_profit", "PROFITABILITY", FLOW, "Operating profit / EBIT as printed"),
    Metric("ebitda", "PROFITABILITY", FLOW, "EBITDA as printed"),
    Metric("pbt", "PROFITABILITY", FLOW, "Profit before tax"),
    Metric("pat", "PROFITABILITY", FLOW, "Profit after tax"),
    Metric("depreciation", "PROFITABILITY", FLOW, "Depreciation and amortisation"),
    Metric("interest_expense", "PROFITABILITY", FLOW, "Finance costs / interest expense"),
    # balance sheet (positions at the end of the period)
    Metric("total_assets", "BALANCE_SHEET", STOCK, "Total assets"),
    Metric("fixed_assets", "BALANCE_SHEET", STOCK, "Fixed assets / PPE"),
    Metric("current_assets", "BALANCE_SHEET", STOCK, "Total current assets"),
    Metric("cash_and_bank", "BALANCE_SHEET", STOCK, "Cash and bank balances"),
    Metric("inventory", "BALANCE_SHEET", STOCK, "Inventories"),
    Metric("receivables", "BALANCE_SHEET", STOCK, "Trade receivables"),
    Metric("total_liabilities", "BALANCE_SHEET", STOCK, "Total outside liabilities (excluding net worth)"),
    Metric("current_liabilities", "BALANCE_SHEET", STOCK, "Total current liabilities"),
    Metric("borrowings", "BALANCE_SHEET", STOCK, "Borrowings (long + short term)"),
    Metric("net_worth", "BALANCE_SHEET", STOCK, "Net worth (capital + reserves)"),
    # banking (statement activity - not revenue)
    Metric("total_credits", "BANKING", FLOW, "Sum of all credits in the statement (not revenue)"),
    Metric("total_debits", "BANKING", FLOW, "Sum of all debits in the statement"),
    Metric("average_balance", "BANKING", STOCK, "Average end-of-day balance over the statement period"),
    Metric("minimum_balance", "BANKING", STOCK, "Lowest end-of-day balance"),
    Metric("maximum_balance", "BANKING", STOCK, "Highest end-of-day balance"),
    Metric("cash_deposits", "BANKING", FLOW, "Credits identified as cash deposits"),
    Metric("cash_withdrawals", "BANKING", FLOW, "Debits identified as cash withdrawals"),
    Metric("bank_charges", "BANKING", FLOW, "Debits identified as bank charges/fees"),
    Metric("debt_payments", "BANKING", FLOW, "Debits identified as loan EMI / repayments"),
    # tax
    Metric("gst_turnover", "TAX", FLOW, "Taxable turnover declared in GST returns"),
    Metric("gst_tax", "TAX", FLOW, "Total GST payable/paid in GST returns"),
    Metric("itr_income", "TAX", FLOW, "Total (taxable) income declared in the ITR"),
    Metric("tax_paid", "TAX", FLOW, "Total taxes paid as per ITR"),
]}

# (document type, extracted field name) -> metric
FIELD_METRICS: dict[tuple[DocumentType, str], str] = {
    (DT.ITR, "turnover"): "turnover",
    (DT.ITR, "taxable_income"): "itr_income",
    (DT.ITR, "tax_paid"): "tax_paid",
    (DT.PROFIT_LOSS, "revenue"): "revenue",
    (DT.PROFIT_LOSS, "other_income"): "other_income",
    (DT.PROFIT_LOSS, "gross_profit"): "gross_profit",
    (DT.PROFIT_LOSS, "operating_profit"): "operating_profit",
    (DT.PROFIT_LOSS, "ebitda"): "ebitda",
    (DT.PROFIT_LOSS, "profit_before_tax"): "pbt",
    (DT.PROFIT_LOSS, "profit_after_tax"): "pat",
    (DT.PROFIT_LOSS, "depreciation"): "depreciation",
    (DT.PROFIT_LOSS, "interest"): "interest_expense",
    (DT.BALANCE_SHEET, "total_assets"): "total_assets",
    (DT.BALANCE_SHEET, "fixed_assets"): "fixed_assets",
    (DT.BALANCE_SHEET, "current_assets"): "current_assets",
    (DT.BALANCE_SHEET, "inventory"): "inventory",
    (DT.BALANCE_SHEET, "receivables"): "receivables",
    (DT.BALANCE_SHEET, "current_liabilities"): "current_liabilities",
    (DT.BALANCE_SHEET, "borrowings"): "borrowings",
    (DT.BALANCE_SHEET, "net_worth"): "net_worth",
    (DT.GST_RETURN, "taxable_turnover"): "gst_turnover",
    (DT.GST_RETURN, "total_tax"): "gst_tax",
}

# Metrics compared for conflicts: facts of any metric in a group, same period, different sources.
# ITR turnover and GST turnover describe the same declared turnover -> one group.
# Banking metrics are never compared with anything (different accounts legitimately differ),
# and bank credits are never compared with revenue/turnover.
CONFLICT_GROUPS: dict[str, set[str]] = {
    "turnover": {"turnover", "gst_turnover"},
}
NON_CONFLICTING_CATEGORIES = {"BANKING"}


def conflict_group(metric: str) -> str:
    for group, members in CONFLICT_GROUPS.items():
        if metric in members:
            return group
    return metric
