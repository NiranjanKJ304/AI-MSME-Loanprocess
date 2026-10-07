"""Centralized, versioned risk-feature definitions.

Every feature is declared here - name, group, description, unit, value type, formula, source layer,
required inputs, period scope and whether it is a required feature. Calculations live in
calculators.py, keyed by the same name; the registry check below guarantees a one-to-one match.
The definitions hash (definitions + FEATURE_CONFIG) identifies exactly which feature logic produced a
snapshot, so a future model can tell which feature version produced its input.

Features describe evidence. None of them is a risk score, a probability of default or a decision.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from app.models.enums import FactAvailability

FEATURE_VERSION = "v1"

# Calculation settings that are part of the feature definition (changing them changes the hash).
FEATURE_CONFIG = {
    "bank_window_months": 12,  # most recent months of combined monthly cash flow
    "min_months_growth": 6,  # growth compares the mean of the newest half with the oldest half
    "min_months_volatility": 3,
    "min_months_trend": 6,
    "recurring_obligation_types": ["EMI", "LOAN_INTEREST", "RENT", "SALARY", "INSURANCE_PREMIUM", "UTILITY_BILL",
                                   "SUBSCRIPTION"],
    "core_financial_metrics": ["revenue", "pat", "gross_profit", "total_assets", "current_assets",
                               "current_liabilities", "borrowings", "net_worth", "cash_and_bank"],
}

GROUPS = ["BUSINESS_FINANCIALS", "TRANSACTION", "STABILITY", "FORECAST", "REPAYMENT", "DATA_QUALITY"]
UNITS = {"INR", "INR_PER_MONTH", "RATIO", "TIMES", "PERCENT", "COUNT", "MONTHS", "TEXT"}
VALUE_TYPES = {"NUMBER", "INTEGER", "TEXT"}
PERIOD_SCOPES = {"LATEST_FY", "BANK_WINDOW", "FORECAST_PERIOD", "LOAN_TERMS", "SNAPSHOT"}
SOURCE_LAYERS = {"FINANCIAL_FACTS", "FINANCIAL_HEALTH", "TRANSACTION_INTELLIGENCE", "FORECASTING",
                 "REPAYMENT_CAPACITY", "DOCUMENTS", "RISK_FEATURES"}
VALID_STATUSES = [s.value for s in FactAvailability]


@dataclass(frozen=True)
class FeatureDef:
    name: str
    group: str
    description: str
    unit: str
    value_type: str
    formula: str
    source_layer: str
    required_inputs: tuple[str, ...]
    period_scope: str
    required: bool = False  # counted by missing_required_data_count when NOT_AVAILABLE
    version: str = FEATURE_VERSION

    def as_dict(self) -> dict:
        d = asdict(self)
        d["required_inputs"] = list(self.required_inputs)
        d["valid_statuses"] = VALID_STATUSES
        return d


def _f(name, group, unit, formula, source, inputs, scope, description, value_type="NUMBER", required=False):
    return FeatureDef(name, group, description, unit, value_type, formula, source, tuple(inputs), scope, required)


BF, TX, ST, FC, RP, DQ = GROUPS
FH, FF, TI, FO, RC, DO, RF = ("FINANCIAL_HEALTH", "FINANCIAL_FACTS", "TRANSACTION_INTELLIGENCE", "FORECASTING",
                              "REPAYMENT_CAPACITY", "DOCUMENTS", "RISK_FEATURES")
W = "bank window: complete months among the most recent bank_window_months months of combined monthly cash flow"

DEFINITIONS: list[FeatureDef] = [
    # ---------------------------------------------------------------- A. business financials (latest FY)
    _f("revenue", BF, "INR", "health metric 'revenue' (P&L revenue, else declared ITR / GST turnover)", FH,
       ["financial_health_metrics.revenue"], "LATEST_FY", "Revenue of the latest financial year", required=True),
    _f("revenue_growth", BF, "RATIO", "health metric 'revenue_yoy_growth' = (rev_t - rev_t-1) / rev_t-1", FH,
       ["financial_health_metrics.revenue_yoy_growth"], "LATEST_FY", "Year-over-year revenue growth"),
    _f("gross_profit", BF, "INR", "canonical fact 'gross_profit'", FF, ["financial_facts.gross_profit"], "LATEST_FY",
       "Gross profit of the latest financial year"),
    _f("net_profit", BF, "INR", "canonical fact 'pat' (profit after tax)", FF, ["financial_facts.pat"], "LATEST_FY",
       "Profit after tax of the latest financial year", required=True),
    _f("gross_margin", BF, "RATIO", "health metric 'gross_margin' = gross_profit / revenue", FH,
       ["financial_health_metrics.gross_margin"], "LATEST_FY", "Gross margin"),
    _f("profit_margin", BF, "RATIO", "health metric 'net_margin' = PAT / revenue", FH,
       ["financial_health_metrics.net_margin"], "LATEST_FY", "Net profit margin"),
    _f("current_ratio", BF, "TIMES", "health metric 'current_ratio' = current_assets / current_liabilities", FH,
       ["financial_health_metrics.current_ratio"], "LATEST_FY", "Current ratio"),
    _f("debt_to_equity", BF, "TIMES", "health metric 'debt_to_net_worth' = borrowings / net_worth (equity = net worth)",
       FH, ["financial_health_metrics.debt_to_net_worth"], "LATEST_FY", "Debt to equity (net worth)"),
    _f("total_debt", BF, "INR", "health metric 'borrowings'", FH, ["financial_health_metrics.borrowings"],
       "LATEST_FY", "Borrowings (balance sheet)", required=True),
    _f("net_worth", BF, "INR", "canonical fact 'net_worth' (reported or capital + reserves)", FF,
       ["financial_facts.net_worth"], "LATEST_FY", "Net worth"),
    _f("cash_balance", BF, "INR", "health metric 'cash_and_bank' (balance sheet)", FH,
       ["financial_health_metrics.cash_and_bank"], "LATEST_FY", "Cash and bank balances"),
    _f("operating_cash_flow", BF, "INR", "health metric 'net_business_cash_flow' for the latest FY "
       "(business inflow - business outflow from classified bank transactions)", FH,
       ["financial_health_metrics.net_business_cash_flow"], "LATEST_FY", "Operating (business) cash flow for the FY"),
    # ---------------------------------------------------------------- B. transaction features (bank window)
    _f("average_monthly_business_inflow", TX, "INR", "mean(business_inflow) over complete months", TI,
       ["cashflow_monthly.business_inflow"], "BANK_WINDOW", W, required=True),
    _f("average_monthly_business_outflow", TX, "INR", "mean(business_outflow) over complete months", TI,
       ["cashflow_monthly.business_outflow"], "BANK_WINDOW", W, required=True),
    _f("average_monthly_business_cash_flow", TX, "INR", "mean(business_inflow - business_outflow) over complete months",
       TI, ["cashflow_monthly.business_inflow", "cashflow_monthly.business_outflow"], "BANK_WINDOW", W),
    _f("positive_cash_flow_month_ratio", TX, "RATIO", "complete months with net business cash flow > 0 / complete months",
       TI, ["cashflow_monthly.net_operating_cash_flow"], "BANK_WINDOW", W),
    _f("business_inflow_growth", TX, "RATIO", "(mean of newest half - mean of oldest half) / mean of oldest half of "
       "complete months' business inflow (odd middle month excluded)", TI, ["cashflow_monthly.business_inflow"],
       "BANK_WINDOW", W),
    _f("business_outflow_growth", TX, "RATIO", "same as business_inflow_growth for business outflow", TI,
       ["cashflow_monthly.business_outflow"], "BANK_WINDOW", W),
    _f("unknown_credit_ratio", TX, "RATIO", "sum(unknown_inflow) / sum(total_inflow) over complete months", TI,
       ["cashflow_monthly.unknown_inflow", "cashflow_monthly.total_inflow"], "BANK_WINDOW", W),
    _f("unknown_debit_ratio", TX, "RATIO", "sum(unknown_outflow) / sum(total_outflow) over complete months", TI,
       ["cashflow_monthly.unknown_outflow", "cashflow_monthly.total_outflow"], "BANK_WINDOW", W),
    _f("transfer_ratio", TX, "RATIO", "sum(transfer_inflow + transfer_outflow) / sum(total_inflow + total_outflow)", TI,
       ["cashflow_monthly.transfer_inflow", "cashflow_monthly.transfer_outflow"], "BANK_WINDOW", W),
    _f("financing_inflow_ratio", TX, "RATIO", "sum(financing_inflow) / sum(total_inflow)", TI,
       ["cashflow_monthly.financing_inflow", "cashflow_monthly.total_inflow"], "BANK_WINDOW", W),
    _f("business_transaction_coverage", TX, "RATIO", "sum(business_inflow + business_outflow) / "
       "sum(total_inflow + total_outflow)", TI, ["cashflow_monthly.business_inflow", "cashflow_monthly.total_inflow"],
       "BANK_WINDOW", W),
    _f("recurring_obligation_count", TX, "COUNT", "count of recurring DEBIT patterns of obligation types "
       "(EMI, loan interest, rent, salary, insurance, utility, subscription)", TI, ["recurring_patterns"],
       "SNAPSHOT", "Recurring obligations detected in the bank statements", value_type="INTEGER"),
    _f("reversal_count", TX, "COUNT", "count of reversal / refund pairs (classifications linked REVERSAL_OF / REFUND_OF)",
       TI, ["transaction_classifications.link_type"], "SNAPSHOT", "Reversal and refund pairs",
       value_type="INTEGER"),
    # ---------------------------------------------------------------- C. stability
    _f("revenue_trend", ST, "RATIO", "health metric 'revenue_trend' (compound annual change over consecutive FYs)", FH,
       ["financial_health_metrics.revenue_trend"], "LATEST_FY", "Revenue trend"),
    _f("profit_trend", ST, "RATIO", "health metric 'profit_trend' (compound annual change of PAT)", FH,
       ["financial_health_metrics.profit_trend"], "LATEST_FY", "Profit trend"),
    _f("cash_flow_trend", ST, "INR_PER_MONTH", "OLS slope of monthly net business cash flow against month index "
       "(complete months)", TI, ["cashflow_monthly.net_operating_cash_flow"], "BANK_WINDOW", W),
    _f("profitable_month_ratio", ST, "RATIO", "complete months with business_inflow - business_outflow - "
       "financing_outflow > 0 / complete months (cash proxy: positive after existing debt service)", TI,
       ["cashflow_monthly.business_inflow", "cashflow_monthly.financing_outflow"], "BANK_WINDOW", W),
    _f("negative_cash_flow_month_ratio", ST, "RATIO", "complete months with net business cash flow < 0 / complete "
       "months", TI, ["cashflow_monthly.net_operating_cash_flow"], "BANK_WINDOW", W),
    _f("inflow_volatility", ST, "RATIO", "population std dev / mean of monthly business inflow (coefficient of "
       "variation)", TI, ["cashflow_monthly.business_inflow"], "BANK_WINDOW", W),
    _f("outflow_volatility", ST, "RATIO", "population std dev / mean of monthly business outflow", TI,
       ["cashflow_monthly.business_outflow"], "BANK_WINDOW", W),
    _f("cash_flow_volatility", ST, "RATIO", "population std dev of monthly net business cash flow / mean monthly "
       "business inflow", TI, ["cashflow_monthly.net_operating_cash_flow", "cashflow_monthly.business_inflow"],
       "BANK_WINDOW", W),
    _f("partial_month_ratio", ST, "RATIO", "PARTIAL months / months with data in the bank window", TI,
       ["cashflow_monthly.availability"], "BANK_WINDOW", W),
    # ---------------------------------------------------------------- D. forecast
    _f("forecast_revenue", FC, "INR", "first forecast result of the 'revenue' forecast run", FO,
       ["forecast_results.revenue"], "FORECAST_PERIOD", "Next-FY revenue forecast (projection, not an actual)"),
    _f("forecast_business_inflow", FC, "INR", "first (next-month) result of the 'business_inflow' forecast run", FO,
       ["forecast_results.business_inflow"], "FORECAST_PERIOD", "Next-month business inflow forecast"),
    _f("forecast_business_outflow", FC, "INR", "first result of the 'business_outflow' forecast run", FO,
       ["forecast_results.business_outflow"], "FORECAST_PERIOD", "Next-month business outflow forecast"),
    _f("forecast_net_cash_flow", FC, "INR", "first result of the 'net_business_cash_flow' forecast run", FO,
       ["forecast_results.net_business_cash_flow"], "FORECAST_PERIOD", "Next-month net business cash flow forecast"),
    _f("forecast_error_mae", FC, "INR", "backtest MAE of the selected business_inflow forecast model", FO,
       ["forecast_runs.business_inflow.backtest"], "FORECAST_PERIOD", "Forecast backtest MAE (business inflow)"),
    _f("forecast_error_rmse", FC, "INR", "backtest RMSE of the selected business_inflow forecast model", FO,
       ["forecast_runs.business_inflow.backtest"], "FORECAST_PERIOD", "Forecast backtest RMSE (business inflow)"),
    _f("forecast_mape", FC, "RATIO", "backtest MAPE of the selected business_inflow model (valid only when every "
       "test actual > 0)", FO, ["forecast_runs.business_inflow.backtest"], "FORECAST_PERIOD",
       "Forecast backtest MAPE (business inflow)"),
    _f("forecast_interval_width", FC, "INR", "upper_bound - lower_bound of the first business_inflow forecast", FO,
       ["forecast_results.business_inflow"], "FORECAST_PERIOD", "Width of the approximate forecast interval"),
    _f("forecast_data_quality", FC, "TEXT", "worst status across the four forecast runs "
       "(AVAILABLE > LOW_CONFIDENCE > NOT_AVAILABLE)", FO, ["forecast_runs.status"], "SNAPSHOT",
       "Forecast data-quality status", value_type="TEXT"),
    _f("forecast_model", FC, "TEXT", "model selected for the business_inflow forecast", FO,
       ["forecast_runs.business_inflow.model"], "SNAPSHOT", "Forecast model (business inflow)", value_type="TEXT"),
    # ---------------------------------------------------------------- E. repayment
    _f("requested_loan_amount", RP, "INR", "loan terms: requested_amount (user-provided)", RC,
       ["repayment_loan_terms.requested_amount"], "LOAN_TERMS", "Requested loan amount"),
    _f("annual_interest_rate", RP, "PERCENT", "loan terms: annual_interest_rate (user-provided)", RC,
       ["repayment_loan_terms.annual_interest_rate"], "LOAN_TERMS", "Annual interest rate"),
    _f("tenure_months", RP, "MONTHS", "loan terms: tenure_months (user-provided)", RC,
       ["repayment_loan_terms.tenure_months"], "LOAN_TERMS", "Tenure in months", value_type="INTEGER"),
    _f("proposed_periodic_payment", RP, "INR", "repayment installment (EMI / quarterly installment)", RC,
       ["repayment_analyses.repayment.installment"], "LOAN_TERMS", "Proposed periodic payment"),
    _f("existing_debt_service", RP, "INR", "repayment metric HISTORICAL existing_debt_service (monthly)", RC,
       ["repayment_capacity_metrics.HISTORICAL.existing_debt_service"], "BANK_WINDOW",
       "Existing monthly debt service"),
    _f("proposed_debt_service", RP, "INR", "repayment metric HISTORICAL proposed_debt_service (monthly equivalent)", RC,
       ["repayment_capacity_metrics.HISTORICAL.proposed_debt_service"], "LOAN_TERMS",
       "Proposed monthly debt service"),
    _f("total_debt_service", RP, "INR", "repayment metric HISTORICAL total_debt_service (monthly)", RC,
       ["repayment_capacity_metrics.HISTORICAL.total_debt_service"], "BANK_WINDOW", "Total monthly debt service"),
    _f("historical_dscr", RP, "TIMES", "repayment metric HISTORICAL dscr", RC,
       ["repayment_capacity_metrics.HISTORICAL.dscr"], "BANK_WINDOW", "Historical DSCR", required=True),
    _f("forecast_dscr", RP, "TIMES", "repayment metric FORECAST dscr", RC, ["repayment_capacity_metrics.FORECAST.dscr"],
       "FORECAST_PERIOD", "Forecast DSCR"),
    _f("statement_dscr", RP, "TIMES", "repayment metric STATEMENT statement_dscr", RC,
       ["repayment_capacity_metrics.STATEMENT.statement_dscr"], "LATEST_FY", "Statement-based DSCR"),
    _f("post_debt_service_cash_flow", RP, "INR", "repayment metric HISTORICAL post_debt_service_cash_flow", RC,
       ["repayment_capacity_metrics.HISTORICAL.post_debt_service_cash_flow"], "BANK_WINDOW",
       "Monthly cash flow after total debt service"),
    _f("stress_dscr_revenue_down", RP, "TIMES", "repayment scenario HISTORICAL REVENUE_DOWN dscr", RC,
       ["repayment_scenarios.HISTORICAL.REVENUE_DOWN"], "BANK_WINDOW", "DSCR with business inflow reduced"),
    _f("stress_dscr_expense_up", RP, "TIMES", "repayment scenario HISTORICAL EXPENSE_UP dscr", RC,
       ["repayment_scenarios.HISTORICAL.EXPENSE_UP"], "BANK_WINDOW", "DSCR with business outflow increased"),
    _f("stress_dscr_combined", RP, "TIMES", "repayment scenario HISTORICAL COMBINED_STRESS dscr", RC,
       ["repayment_scenarios.HISTORICAL.COMBINED_STRESS"], "BANK_WINDOW", "DSCR under combined stress"),
    _f("repayment_data_quality", RP, "TEXT", "status of the repayment-capacity analysis", RC,
       ["repayment_analyses.status"], "SNAPSHOT", "Repayment analysis data-quality status", value_type="TEXT"),
    # ---------------------------------------------------------------- F. document / data quality (evidence quality)
    _f("document_completeness", DQ, "RATIO", "required documents received / required documents (requirement policy)",
       DO, ["documents", "requirement_policy"], "SNAPSHOT", "Required-document completeness"),
    _f("extraction_confidence", DQ, "RATIO", "mean confidence of extracted (non-missing) fields of unique documents",
       DO, ["extracted_fields.confidence"], "SNAPSHOT", "Average field extraction confidence"),
    _f("financial_fact_coverage", DQ, "RATIO", "core financial metrics with a usable (AVAILABLE / LOW_CONFIDENCE) value "
       "in the latest FY / number of core metrics", FF, ["financial_facts"], "LATEST_FY",
       "Coverage of core financial facts"),
    _f("conflicting_fact_count", DQ, "COUNT", "count of recorded financial fact conflicts", FF,
       ["financial_conflicts"], "SNAPSHOT", "Conflicting facts", value_type="INTEGER"),
    _f("low_confidence_fact_count", DQ, "COUNT", "count of financial facts with availability LOW_CONFIDENCE", FF,
       ["financial_facts.availability"], "SNAPSHOT", "Low-confidence facts", value_type="INTEGER"),
    _f("transaction_classification_coverage", DQ, "RATIO", "(sum(total) - sum(unknown)) / sum(total) over complete "
       "months, credits + debits", TI, ["cashflow_monthly.unknown_inflow", "cashflow_monthly.unknown_outflow"],
       "BANK_WINDOW", W),
    _f("unknown_transaction_ratio", DQ, "RATIO", "transactions with classification status UNKNOWN / classified "
       "transactions (by count)", TI, ["transaction_classifications.status"], "SNAPSHOT",
       "Share of transactions left UNKNOWN (count)"),
    _f("missing_required_data_count", DQ, "COUNT", "required features (required=True) whose status is NOT_AVAILABLE",
       RF, ["risk_features.required"], "SNAPSHOT", "Missing required features", value_type="INTEGER"),
    _f("partial_period_count", DQ, "COUNT", "PARTIAL months + missing months in the bank window", TI,
       ["cashflow_monthly.availability"], "BANK_WINDOW", "Partial / missing bank months", value_type="INTEGER"),
]


def _check_registry() -> None:
    names = [d.name for d in DEFINITIONS]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"duplicate feature definitions: {sorted(dupes)}")
    for d in DEFINITIONS:
        if d.group not in GROUPS or d.unit not in UNITS or d.value_type not in VALUE_TYPES or \
                d.period_scope not in PERIOD_SCOPES or d.source_layer not in SOURCE_LAYERS:
            raise ValueError(f"invalid feature definition: {d.name}")
        if (d.value_type == "TEXT") != (d.unit == "TEXT"):
            raise ValueError(f"{d.name}: TEXT value type and TEXT unit must go together")


_check_registry()
BY_NAME: dict[str, FeatureDef] = {d.name: d for d in DEFINITIONS}


def definitions_hash() -> str:
    payload = json.dumps({"version": FEATURE_VERSION, "config": FEATURE_CONFIG,
                          "definitions": [d.as_dict() for d in DEFINITIONS]}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()
