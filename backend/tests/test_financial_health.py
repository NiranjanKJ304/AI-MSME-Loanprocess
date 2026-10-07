"""Financial health analysis: formulas, dimensions, periods, missing / partial / conflicting data,
indicators, provenance, explanations and APIs. Expected values are computed by hand."""

from __future__ import annotations

import calendar
import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal

import pytest

from app import database
from app.financial_health.config import DEFAULT_THRESHOLDS_PATH, get_thresholds, load_thresholds, set_thresholds
from app.financial_health.formulas import Input, change, compound_change, consistency, ratio
from app.financial_health.service import build_financial_health
from app.financials.metrics import METRICS
from app.financials.periods import CanonicalPeriod, fiscal_year, from_range, parse_month
from app.models import Application, CashflowMonthly, FinancialConflict, FinancialFact, FinancialPeriod
from app.models.enums import DocumentType, FactAvailability, FactSourceKind, MeasureType
from scripts import synthetic_docs as S
from tests.conftest import create_app, sample_documents, upload

A = FactAvailability
DT = DocumentType
D = Decimal


# =========================================================================== helpers
class Seed:
    """Writes canonical facts / conflicts / monthly cash flow directly, then builds health."""

    def __init__(self, client):
        self.client = client
        self.app_id = uuid.UUID(create_app(client)["id"])
        self.db = database.SessionLocal()
        self.periods: dict[str, FinancialPeriod] = {}

    def period(self, cp: CanonicalPeriod) -> FinancialPeriod:
        if cp.key not in self.periods:
            p = FinancialPeriod(id=uuid.uuid4(), application_id=self.app_id, period_key=cp.key,
                                period_type=cp.period_type, start_date=cp.start, end_date=cp.end,
                                fiscal_year=cp.fiscal_year, label=cp.label, source_expressions=[])
            self.db.add(p)
            self.db.flush()  # no ORM relationship orders the inserts: periods must exist before facts
            self.periods[cp.key] = p
        return self.periods[cp.key]

    def fact(self, fy: int | CanonicalPeriod, metric: str, value, *, doc="PL-001", dtype=DT.PROFIT_LOSS,
             availability=A.AVAILABLE, page=1) -> FinancialFact:
        cp = fiscal_year(fy) if isinstance(fy, int) else fy
        p = self.period(cp)
        m = METRICS[metric]
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, doc))
        exprs = list(p.source_expressions or [])
        exprs.append({"expression": cp.key, "document_code": doc, "document_id": doc_id})
        p.source_expressions = exprs
        f = FinancialFact(
            id=uuid.uuid4(), application_id=self.app_id, metric=metric, category=m.category, measure_type=m.measure,
            value=None if value is None else D(str(value)), raw_value=None if value is None else str(value),
            period_id=p.id, period_type=cp.period_type, period_start=cp.start, period_end=cp.end,
            as_of_date=cp.end if m.measure == MeasureType.STOCK else None, source_kind=FactSourceKind.EXTRACTED_FIELD,
            source_document_type=dtype, source_field_name=metric, confidence=0.9, availability=availability,
            provenance={"document_id": doc_id, "document_code": doc, "document_type": dtype.value, "page": page,
                        "field_id": str(uuid.uuid4()), "source_location": {"bbox": [50.0, 100.0, 300.0, 112.0]}},
            notes=["PARTIAL: missing component(s) reserves; not computed"] if availability == A.PARTIAL else None)
        self.db.add(f)
        return f

    def conflict(self, a: FinancialFact, b: FinancialFact, metric: str = "turnover") -> None:
        self.db.flush()
        diff = a.value - b.value
        self.db.add(FinancialConflict(
            application_id=self.app_id, metric=metric, period_id=a.period_id, fact_a_id=a.id, fact_b_id=b.id,
            value_a=a.value, value_b=b.value, difference=diff,
            difference_pct=float(abs(diff) / max(abs(a.value), abs(b.value))), tolerance=0.005,
            source_a={"document_code": a.provenance["document_code"], "document_type": a.provenance["document_type"],
                      "document_id": a.provenance["document_id"], "page": 1, "value": str(a.value)},
            source_b={"document_code": b.provenance["document_code"], "document_type": b.provenance["document_type"],
                      "document_id": b.provenance["document_id"], "page": 1, "value": str(b.value)}))

    def month(self, ym: str, inflow=0, outflow=0, availability=A.AVAILABLE, **buckets) -> None:
        y, mth = map(int, ym.split("-"))
        cp = from_range(date(y, mth, 1), date(y, mth, calendar.monthrange(y, mth)[1]))
        vals = {c.name: D("0.00") for c in CashflowMonthly.__table__.columns if str(c.type).startswith("NUMERIC")}
        vals.update(business_inflow=D(str(inflow)), business_outflow=D(str(outflow)))
        vals.update({k: D(str(v)) for k, v in buckets.items()})
        vals["total_inflow"] = vals["total_inflow"] or vals["business_inflow"] + vals["unknown_inflow"]
        vals["total_outflow"] = vals["total_outflow"] or vals["business_outflow"] + vals["unknown_outflow"]
        vals["net_operating_cash_flow"] = vals["business_inflow"] - vals["business_outflow"]
        self.db.add(CashflowMonthly(
            application_id=self.app_id, document_id=None, month=cp.key, month_start=cp.start, month_end=cp.end,
            days_in_month=cp.end.day, days_covered=cp.end.day if availability != A.PARTIAL else 10, **vals,
            txn_count=4, business_txn_count=4, unknown_txn_count=0, low_confidence_txn_count=0, excluded_txn_count=0,
            classification_coverage_count=1.0, classification_coverage_amount=1.0, confidence=0.9,
            availability=availability,
            partial_reasons=["statement covers 10 of 30 days"] if availability == A.PARTIAL else None,
            provenance={"transaction_ids": {}, "document_ids": []}))

    def build(self) -> dict:
        self.db.commit()
        build_financial_health(self.db, self.db.get(Application, self.app_id))
        self.db.commit()
        self.db.close()
        r = self.client.get(f"/api/applications/{self.app_id}/financial-health")
        assert r.status_code == 200, r.text
        return r.json()


def metric(profile: dict, period_key: str, name: str, scope: str | None = None) -> dict:
    blocks = profile["periods"] + profile["monthly"]
    p = next(b for b in blocks if b["period_key"] == period_key)
    for dim in p["dimensions"].values():
        for m in dim["metrics"]:
            if m["metric"] == name and (scope is None or m["scope"] == scope):
                return m
    raise KeyError(f"{name} not in {period_key}")


def indicator(profile: dict, period_key: str, dimension: str) -> dict:
    p = next(b for b in profile["periods"] if b["period_key"] == period_key)
    return p["dimensions"][dimension]["indicator"]


def inp(name, value, status=A.AVAILABLE, period="FY2024-25", unit="INR"):
    return Input(name, name, None if value is None else D(str(value)), status, period, unit=unit)


@pytest.fixture(autouse=True)
def default_thresholds():
    set_thresholds(None)
    yield
    set_thresholds(None)


# =========================================================================== formulas
def test_ratio_formula_and_division_by_zero_protection():
    assert ratio(inp("pat", 677000), inp("revenue", 6820000)).value == D("0.099267")
    zero = ratio(inp("pat", 100), inp("revenue", 0))
    assert zero.value is None and zero.status == A.NOT_AVAILABLE and "division by zero" in zero.reason
    neg = ratio(inp("borrowings", 100), inp("net_worth", -50))
    assert neg.value is None and "negative" in neg.reason


def test_formula_status_rules():
    c = ratio(inp("pat", 1), inp("revenue", 10, A.CONFLICTING))
    assert c.value is None and c.status == A.CONFLICTING  # never picks a value
    n = ratio(inp("pat", None, A.NOT_AVAILABLE), inp("revenue", 10))
    assert n.value is None and n.status == A.NOT_AVAILABLE  # missing stays missing, never 0
    p_none = ratio(inp("pat", 1), inp("net_worth", None, A.PARTIAL))
    assert p_none.value is None and p_none.status == A.PARTIAL
    p_obs = ratio(inp("net", 30, A.PARTIAL), inp("inflow", 120, A.PARTIAL))
    assert p_obs.value == D("0.25") and p_obs.status == A.PARTIAL  # observed value, explicitly PARTIAL
    lc = ratio(inp("pat", 1, A.LOW_CONFIDENCE), inp("revenue", 4))
    assert lc.value == D("0.25") and lc.status == A.LOW_CONFIDENCE


def test_change_compound_and_consistency_formulas():
    absolute, pct = change(inp("rev", 5000000, period="FY2024-25"), inp("rev", 6000000, period="FY2025-26"))
    assert absolute.value == D("1000000.00") and pct.value == D("0.2") and pct.details["direction"] == "INCREASE"
    _, from_zero = change(inp("rev", 0), inp("rev", 100))
    assert from_zero.value is None and "division by zero" in from_zero.reason
    a, from_loss = change(inp("pat", -100000), inp("pat", 50000))
    assert a.value == D("150000.00") and from_loss.value is None and "negative" in from_loss.reason
    cagr = compound_change([inp("r", 4000000), inp("r", 5000000), inp("r", 6000000)])
    assert cagr.value == D("0.224745") and cagr.details["trend"] == "INCREASING"  # 1.5 ** 0.5 - 1
    cons = consistency([D(100), D(100), D(100)], 3)
    assert cons.value == D("1.000000")
    assert consistency([D(100), D(100)], 3).value is None


# =========================================================================== revenue
def test_revenue_growth_between_consecutive_years(client):
    s = Seed(client)
    s.fact(2024, "revenue", 5000000, doc="PL-2425")
    s.fact(2025, "revenue", 6000000, doc="PL-2526")
    prof = s.build()
    g = metric(prof, "FY2025-26", "revenue_yoy_growth")
    assert g["value"] == "0.200000" and g["status"] == "AVAILABLE" and g["compare_period_key"] == "FY2024-25"
    assert metric(prof, "FY2025-26", "revenue_yoy_change")["value"] == "1000000.00"
    assert g["details"]["direction"] == "INCREASE"
    assert g["formula"].startswith("revenue_yoy_growth = (revenue(FY 2025-26) - revenue(FY 2024-25))")
    assert "20.00%" in g["explanation"] and "compared with FY 2024-25" in g["explanation"]
    assert {e["document_code"] for e in g["evidence"]} == {"PL-2425", "PL-2526"}
    ind = indicator(prof, "FY2025-26", "REVENUE")
    assert ind["indicator"] == "STABLE"  # +20% is below the 25% STRONG threshold; growth is described, not judged
    assert ind["evidence"][1]["metric"] == "revenue_yoy_growth" and "not a credit assessment" in ind["explanation"]
    assert indicator(prof, "FY2024-25", "REVENUE")["indicator"] == "INSUFFICIENT_DATA"  # no FY2023-24


@pytest.mark.parametrize("cur, expected", [(5400000, "DECLINING"), (7500000, "STRONG")])
def test_revenue_indicator_thresholds(client, cur, expected):
    s = Seed(client)
    s.fact(2024, "revenue", 6000000, doc="PL-A")
    s.fact(2025, "revenue", cur, doc="PL-B")
    assert indicator(s.build(), "FY2025-26", "REVENUE")["indicator"] == expected


def test_revenue_trend_and_annual_consistency(client):
    s = Seed(client)
    for i, v in enumerate((4000000, 5000000, 6000000)):
        s.fact(2023 + i, "revenue", v, doc=f"PL-{i}")
    prof = s.build()
    t = metric(prof, "FY2025-26", "revenue_trend")
    assert t["value"] == "0.224745" and t["details"]["trend"] == "INCREASING"
    assert t["details"]["periods"] == ["FY2023-24", "FY2024-25", "FY2025-26"]
    c = metric(prof, "FY2025-26", "revenue_consistency")
    # mean 5,000,000; population std dev 816,496.58 -> 1 - 0.163299 = 0.836701
    assert c["value"] == "0.836701" and c["status"] == "AVAILABLE"
    assert metric(prof, "FY2024-25", "revenue_consistency")["value"] is None  # only 2 years up to FY2024-25


# =========================================================================== profitability
def test_profitability_margins(client):
    s = Seed(client)
    for metric_code, v in (("revenue", 10000000), ("gross_profit", 4000000), ("operating_profit", 1500000),
                           ("pat", 900000)):
        s.fact(2024, metric_code, v)
    prof = s.build()
    assert metric(prof, "FY2024-25", "gross_margin")["value"] == "0.400000"
    assert metric(prof, "FY2024-25", "operating_margin")["value"] == "0.150000"
    nm = metric(prof, "FY2024-25", "net_margin")
    assert nm["value"] == "0.090000" and nm["formula"] == "net_margin = PAT / revenue"
    assert [i["name"] for i in nm["inputs"]] == ["pat", "revenue"]
    assert [i["value"] for i in nm["inputs"]] == ["900000.00", "10000000.00"]
    assert indicator(prof, "FY2024-25", "PROFITABILITY")["indicator"] == "STABLE"  # 0% <= 9% < 10%


def test_profit_trend_and_declining_margin(client):
    s = Seed(client)
    s.fact(2023, "revenue", 10000000, doc="PL-A"); s.fact(2023, "pat", 1200000, doc="PL-A")
    s.fact(2024, "revenue", 10000000, doc="PL-B"); s.fact(2024, "pat", 900000, doc="PL-B")
    prof = s.build()
    assert metric(prof, "FY2024-25", "pat_yoy_change")["value"] == "-300000.00"
    assert metric(prof, "FY2024-25", "pat_yoy_growth")["value"] == "-0.250000"
    assert metric(prof, "FY2024-25", "net_margin_change")["value"] == "-0.030000"  # 9% - 12%
    assert indicator(prof, "FY2024-25", "PROFITABILITY")["indicator"] == "DECLINING"
    assert metric(prof, "FY2024-25", "profit_trend")["details"]["trend"] == "DECREASING"


def test_loss_making_year_is_weak_and_growth_from_loss_not_a_percentage(client):
    s = Seed(client)
    s.fact(2023, "revenue", 8000000, doc="PL-A"); s.fact(2023, "pat", -200000, doc="PL-A")
    s.fact(2024, "revenue", 9000000, doc="PL-B"); s.fact(2024, "pat", -100000, doc="PL-B")
    prof = s.build()
    assert indicator(prof, "FY2024-25", "PROFITABILITY")["indicator"] == "WEAK"
    assert metric(prof, "FY2024-25", "pat_yoy_change")["value"] == "100000.00"
    g = metric(prof, "FY2024-25", "pat_yoy_growth")
    assert g["value"] is None and "negative" in g["reason"]


# =========================================================================== liquidity
def test_liquidity_current_ratio_and_working_capital(client):
    s = Seed(client)
    s.fact(2024, "current_assets", 3000000, doc="BS-1", dtype=DT.BALANCE_SHEET, page=2)
    s.fact(2024, "current_liabilities", 2000000, doc="BS-1", dtype=DT.BALANCE_SHEET, page=2)
    s.fact(2024, "cash_and_bank", 400000, doc="BS-1", dtype=DT.BALANCE_SHEET, page=2)
    prof = s.build()
    cr = metric(prof, "FY2024-25", "current_ratio")
    assert cr["value"] == "1.500000" and cr["unit"] == "TIMES"
    assert metric(prof, "FY2024-25", "net_working_capital")["value"] == "1000000.00"
    assert metric(prof, "FY2024-25", "cash_and_bank")["value"] == "400000.00"
    assert indicator(prof, "FY2024-25", "LIQUIDITY")["indicator"] == "STRONG"
    assert cr["evidence"] == [{"document_id": cr["evidence"][0]["document_id"], "document_code": "BS-1",
                               "document_type": "BALANCE_SHEET", "pages": [2]}]


def test_current_ratio_zero_liabilities_is_not_divided(client):
    s = Seed(client)
    s.fact(2024, "current_assets", 3000000, dtype=DT.BALANCE_SHEET)
    s.fact(2024, "current_liabilities", 0, dtype=DT.BALANCE_SHEET)
    prof = s.build()
    cr = metric(prof, "FY2024-25", "current_ratio")
    assert cr["value"] is None and cr["status"] == "NOT_AVAILABLE" and "division by zero" in cr["reason"]
    assert indicator(prof, "FY2024-25", "LIQUIDITY")["indicator"] == "INSUFFICIENT_DATA"


# =========================================================================== leverage
def test_debt_metrics_and_trend(client):
    s = Seed(client)
    s.fact(2023, "borrowings", 2000000, doc="BS-A", dtype=DT.BALANCE_SHEET)
    s.fact(2023, "revenue", 5000000, doc="PL-A")
    s.fact(2024, "borrowings", 2500000, doc="BS-B", dtype=DT.BALANCE_SHEET)
    s.fact(2024, "net_worth", 5000000, doc="BS-B", dtype=DT.BALANCE_SHEET)
    s.fact(2024, "revenue", 10000000, doc="PL-B")
    prof = s.build()
    assert metric(prof, "FY2024-25", "debt_to_revenue")["value"] == "0.250000"
    assert metric(prof, "FY2024-25", "debt_to_net_worth")["value"] == "0.500000"
    assert metric(prof, "FY2024-25", "borrowings_yoy_change")["value"] == "500000.00"
    assert metric(prof, "FY2024-25", "borrowings_yoy_growth")["value"] == "0.250000"
    assert metric(prof, "FY2024-25", "debt_trend")["details"]["trend"] == "INCREASING"
    assert indicator(prof, "FY2024-25", "LEVERAGE")["indicator"] == "STRONG"  # 0.25 <= threshold (low leverage)
    assert metric(prof, "FY2023-24", "debt_to_revenue")["value"] == "0.400000"  # 2.0M / 5.0M
    assert indicator(prof, "FY2023-24", "LEVERAGE")["indicator"] == "STABLE"  # 0.25 < 0.4 <= 1.0


# =========================================================================== conflicts / missing / partial
def test_conflicting_turnover_is_never_used(client):
    s = Seed(client)
    itr = s.fact(2024, "turnover", 5000000, doc="ITR-1", dtype=DT.ITR)
    gst = s.fact(2024, "gst_turnover", 4700000, doc="GSTR-1", dtype=DT.GST_RETURN)
    s.fact(2024, "borrowings", 1000000, doc="BS-1", dtype=DT.BALANCE_SHEET)
    s.conflict(itr, gst)
    prof = s.build()
    rev = metric(prof, "FY2024-25", "revenue")
    assert rev["status"] == "CONFLICTING" and rev["value"] is None
    d = metric(prof, "FY2024-25", "debt_to_revenue")
    assert d["status"] == "CONFLICTING" and d["value"] is None  # no ratio from conflicting facts
    conflicts = next(i for i in d["inputs"] if i["name"] == "revenue")["conflicts"]
    assert {conflicts[0]["source_a"]["document_code"], conflicts[0]["source_b"]["document_code"]} == {"ITR-1", "GSTR-1"}
    assert d["provenance"]["conflict_ids"]
    assert {e["document_code"] for e in d["evidence"]} >= {"ITR-1", "GSTR-1"}
    for dim in ("REVENUE", "LEVERAGE", "TAX_COMPLIANCE"):
        assert indicator(prof, "FY2024-25", dim)["indicator"] == "CONFLICTING_DATA"
    assert metric(prof, "FY2024-25", "source_conflicts")["value"] == "1"


def test_missing_inputs_stay_missing_never_zero(client):
    s = Seed(client)
    s.fact(2024, "revenue", 6000000)
    prof = s.build()
    for name in ("gross_margin", "operating_margin", "net_margin", "current_ratio", "debt_to_revenue"):
        m = metric(prof, "FY2024-25", name)
        assert m["value"] is None and m["status"] == "NOT_AVAILABLE" and m["reason"].startswith("missing input"), m
        assert "Not calculated" in m["explanation"]
    assert indicator(prof, "FY2024-25", "PROFITABILITY")["indicator"] == "INSUFFICIENT_DATA"
    # no bank data -> no cash-flow metrics at all (nothing invented)
    p = next(b for b in prof["periods"] if b["period_key"] == "FY2024-25")
    assert "CASH_FLOW" not in p["dimensions"] and prof["monthly"] == []


def test_partial_fact_gives_partial_metric_without_value(client):
    s = Seed(client)
    s.fact(2024, "borrowings", 1000000, dtype=DT.BALANCE_SHEET)
    s.fact(2024, "net_worth", None, dtype=DT.BALANCE_SHEET, availability=A.PARTIAL)
    prof = s.build()
    m = metric(prof, "FY2024-25", "debt_to_net_worth")
    assert m["status"] == "PARTIAL" and m["value"] is None and "reserves" in m["reason"]


def test_low_confidence_input_is_carried_into_the_metric(client):
    s = Seed(client)
    s.fact(2024, "revenue", 6000000)
    s.fact(2024, "pat", 600000, availability=A.LOW_CONFIDENCE)
    prof = s.build()
    nm = metric(prof, "FY2024-25", "net_margin")
    assert nm["value"] == "0.100000" and nm["status"] == "LOW_CONFIDENCE"
    assert "Caution: based on LOW_CONFIDENCE data" in indicator(prof, "FY2024-25", "PROFITABILITY")["explanation"]


# =========================================================================== periods
def test_non_consecutive_years_are_not_compared(client):
    s = Seed(client)
    s.fact(2022, "revenue", 5000000, doc="PL-A")
    s.fact(2024, "revenue", 6000000, doc="PL-B")
    prof = s.build()
    g = metric(prof, "FY2024-25", "revenue_yoy_growth")
    assert g["value"] is None and g["compare_period_key"] == "FY2023-24" and "FY 2023-24" in g["reason"]
    assert metric(prof, "FY2024-25", "revenue_trend")["value"] is None


def test_different_revenue_bases_are_not_compared(client):
    s = Seed(client)
    s.fact(2023, "turnover", 5000000, doc="ITR-A", dtype=DT.ITR)  # declared turnover only
    s.fact(2024, "revenue", 6000000, doc="PL-B")  # P&L revenue
    prof = s.build()
    assert metric(prof, "FY2023-24", "revenue")["inputs"][0]["basis"] == "declared turnover (ITR / GST return)"
    g = metric(prof, "FY2024-25", "revenue_yoy_growth")
    assert g["value"] is None and "bases differ" in g["reason"]


def test_monthly_facts_are_labelled_and_not_compared_with_annual(client):
    s = Seed(client)
    s.fact(2024, "revenue", 6000000, doc="PL-B")
    s.fact(parse_month("May 2024"), "gst_turnover", 500000, doc="GSTR3B-05", dtype=DT.GST_RETURN)
    prof = s.build()
    may = metric(prof, "2024-05", "revenue")
    assert may["period_kind"] == "MONTHLY" and may["value"] == "500000.00"
    assert may["inputs"][0]["basis"] == "declared turnover (ITR / GST return)"
    annual = next(b for b in prof["periods"] if b["period_key"] == "FY2024-25")
    assert annual["period_kinds"] == ["ANNUAL"]
    assert metric(prof, "FY2024-25", "revenue")["value"] == "6000000.00"  # monthly GST never enters annual formulas
    cov = metric(prof, "FY2024-25", "gst_period_coverage")
    assert cov["value"] == "0.083333" and cov["details"]["months_covered"] == ["2024-05"]
    assert metric(prof, "FY2024-25", "missing_gst_months")["value"] == "11"


# =========================================================================== cash flow
FY_MONTHS = [f"2024-{m:02d}" for m in range(4, 13)] + [f"2025-{m:02d}" for m in range(1, 4)]


def test_cash_flow_full_year(client):
    s = Seed(client)
    for i, ym in enumerate(FY_MONTHS):  # 9 positive months (+20,000), 3 negative months (-10,000)
        s.month(ym, inflow=100000, outflow=80000 if i % 4 else 110000)
    prof = s.build()
    assert metric(prof, "FY2024-25", "business_inflow")["value"] == "1200000.00"
    assert metric(prof, "FY2024-25", "business_outflow")["value"] == "1050000.00"  # 9 x 80k + 3 x 110k
    net = metric(prof, "FY2024-25", "net_business_cash_flow")
    assert net["value"] == "150000.00" and net["period_kind"] == "ANNUAL" and net["status"] == "AVAILABLE"
    assert metric(prof, "FY2024-25", "cash_flow_margin")["value"] == "0.125000"  # 150k / 1.2M
    assert metric(prof, "FY2024-25", "positive_cash_flow_months")["value"] == "9"
    neg = metric(prof, "FY2024-25", "negative_cash_flow_months")
    assert neg["value"] == "3" and neg["details"]["negative_months"] == ["2024-04", "2024-08", "2024-12"]
    assert metric(prof, "FY2024-25", "cash_flow_consistency")["value"] == "0.750000"
    assert metric(prof, "FY2024-25", "business_inflow_consistency")["value"] == "1.000000"
    assert metric(prof, "FY2024-25", "classification_coverage")["value"] == "1.000000"
    assert indicator(prof, "FY2024-25", "CASH_FLOW")["indicator"] == "STABLE"  # 12.5% < 15% STRONG margin
    apr = metric(prof, "2024-04", "net_business_cash_flow")
    assert apr["period_kind"] == "MONTHLY" and apr["value"] == "-10000.00" and apr["details"]["direction"] == "NEGATIVE"


def test_partial_bank_year_is_marked_partial(client):
    s = Seed(client)
    for ym in FY_MONTHS[:6]:
        s.month(ym, inflow=100000, outflow=90000)
    s.month(FY_MONTHS[6], inflow=30000, outflow=10000, availability=A.PARTIAL)
    prof = s.build()
    net = metric(prof, "FY2024-25", "net_business_cash_flow")
    assert net["period_kind"] == "PARTIAL_PERIOD" and net["status"] == "PARTIAL"
    assert net["value"] == "80000.00"  # observed over the covered months, explicitly PARTIAL
    assert "cover 7 of 12 months" in net["reason"]
    assert net["period_start"] == "2024-04-01" and net["period_end"] == "2024-10-31"
    pos = metric(prof, "FY2024-25", "positive_cash_flow_months")
    assert pos["value"] == "6" and pos["details"]["partial_months_excluded"] == ["2024-10"]
    ind = indicator(prof, "FY2024-25", "CASH_FLOW")
    assert ind["period_kind"] == "PARTIAL_PERIOD" and "PARTIAL" in ind["explanation"]


def test_zero_business_inflow_and_low_coverage(client):
    s = Seed(client)
    for ym in FY_MONTHS[:3]:
        s.month(ym, inflow=0, outflow=50000, unknown_inflow=200000)
    prof = s.build()
    m = metric(prof, "FY2024-25", "cash_flow_margin")
    assert m["value"] is None and "division by zero" in m["reason"]
    assert metric(prof, "FY2024-25", "classification_coverage")["value"] == "0.200000"  # 150k of 750k classified
    assert metric(prof, "FY2024-25", "unknown_inflow_share")["value"] == "1.000000"
    assert indicator(prof, "FY2024-25", "CASH_FLOW")["indicator"] == "INSUFFICIENT_DATA"


def test_cash_flow_negative_margin_is_weak(client):
    s = Seed(client)
    for ym in FY_MONTHS:
        s.month(ym, inflow=100000, outflow=120000)
    prof = s.build()
    assert metric(prof, "FY2024-25", "cash_flow_margin")["value"] == "-0.200000"
    assert indicator(prof, "FY2024-25", "CASH_FLOW")["indicator"] == "WEAK"


# =========================================================================== end-to-end, provenance, API
def test_sample_application_health_with_full_provenance(client, business):
    app = create_app(client)
    upload(client, app["id"], sample_documents(business))
    prof = client.get(f"/api/applications/{app['id']}/financial-health").json()
    assert prof["built"] and prof["thresholds_version"] == get_thresholds().version

    # Net margin -> PAT + revenue facts -> extracted fields -> P&L document -> page/bbox
    nm = metric(prof, "FY2024-25", "net_margin")
    assert nm["value"] == "0.099267" and nm["status"] == "AVAILABLE"  # 6,77,000 / 68,20,000
    assert nm["provenance"]["chain"].startswith("health metric -> calculation -> canonical financial facts")
    assert [e["document_type"] for e in nm["evidence"]] == ["PROFIT_LOSS"] and nm["evidence"][0]["pages"]
    detail = client.get(f"/api/financial-health/metrics/{nm['id']}").json()
    assert len(detail["resolved"]["facts"]) == 2
    for f in detail["resolved"]["facts"]:
        assert f["extracted_field"]["page"] and f["extracted_field"]["bbox"]
        assert f["document"]["document_code"] == nm["evidence"][0]["document_code"]

    # Cash flow -> monthly aggregates -> classified transactions -> statement rows -> page/bbox
    inflow = metric(prof, "FY2024-25", "business_inflow")
    assert "statement rows" in inflow["provenance"]["chain"] and inflow["provenance"]["monthly_aggregate_ids"]
    d = client.get(f"/api/financial-health/metrics/{inflow['id']}").json()
    assert d["resolved"]["transactions"] and all(t["statement_row"]["bbox"] for t in d["resolved"]["transactions"])
    assert [e["document_type"] for e in inflow["evidence"]] == ["BANK_STATEMENT"]

    # bank business inflow == transactions classified INCOME / BUSINESS (never all credits)
    cls = client.get(f"/api/applications/{app['id']}/transactions/classified").json()
    biz = [t for t in cls if t["classification"]["category_group"] == "INCOME"
           and t["classification"]["nature"] == "BUSINESS" and not t["classification"]["excluded_from_aggregates"]]
    assert D(inflow["value"]) == sum(D(t["credit"]) for t in biz)
    by_cp = defaultdict(D)
    for t in biz:
        by_cp[t["classification"]["normalized_counterparty"]] += D(t["credit"])
    top = metric(prof, "FY2024-25", "top_counterparty_share")
    assert D(top["value"]) == (max(by_cp.values()) / sum(by_cp.values())).quantize(D("0.000001"))

    doc_id = nm["inputs"][0]["sources"][0]["document_id"]
    stages = {s["stage"]: s["status"] for s in client.get(f"/api/documents/{doc_id}/status").json()["pipeline"]}
    assert stages["FINANCIAL_HEALTH"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS")


def test_period_endpoint_rebuild_idempotent_and_404(client, business):
    app = create_app(client)
    upload(client, app["id"], sample_documents(business))
    one = client.get(f"/api/applications/{app['id']}/financial-health/FY2024-25").json()
    assert [p["period_key"] for p in one["periods"]] == ["FY2024-25"] and one["monthly"] == []
    dims = one["periods"][0]["dimensions"]
    assert set(dims) == {"REVENUE", "PROFITABILITY", "LIQUIDITY", "CASH_FLOW", "LEVERAGE", "BUSINESS_STABILITY",
                         "TAX_COMPLIANCE"}
    for dim in dims.values():
        assert dim["indicator"]["indicator"] in {"STRONG", "STABLE", "DECLINING", "WEAK", "INSUFFICIENT_DATA",
                                                 "CONFLICTING_DATA"}
        for m in dim["metrics"]:
            assert {"value", "status", "formula", "inputs", "evidence", "explanation"} <= set(m)
    month = client.get(f"/api/applications/{app['id']}/financial-health/2024-04").json()
    assert month["monthly"][0]["period_kinds"] == ["MONTHLY"]
    assert client.get(f"/api/applications/{app['id']}/financial-health/FY1999-00").status_code == 404

    def strip(p):
        return sorted(((b["period_key"], k, m["metric"], m["scope"], m["value"], m["status"])
                       for b in p["periods"] + p["monthly"] for k, d in b["dimensions"].items()
                       for m in d["metrics"]), key=str)

    before = client.get(f"/api/applications/{app['id']}/financial-health").json()
    r = client.post(f"/api/applications/{app['id']}/financial-health/rebuild")
    assert r.status_code == 200 and r.json()["metrics"] > 0
    after = client.get(f"/api/applications/{app['id']}/financial-health").json()
    assert strip(before) == strip(after)


def test_bank_statement_alone_has_no_revenue(client, business):
    app = create_app(client)
    upload(client, app["id"], {"stmt.pdf": S.bank_statement_pdf(S.default_bank_spec(business))})
    prof = client.get(f"/api/applications/{app['id']}/financial-health").json()
    fy = next(b for b in prof["periods"] if b["period_key"] == "FY2024-25")
    assert "REVENUE" not in fy["dimensions"] and "PROFITABILITY" not in fy["dimensions"]
    assert metric(prof, "FY2024-25", "business_inflow")["value"] is not None
    assert indicator(prof, "FY2024-25", "TAX_COMPLIANCE")["indicator"] == "INSUFFICIENT_DATA"


# =========================================================================== thresholds
def test_thresholds_change_indicators_not_values(client):
    th = load_thresholds(DEFAULT_THRESHOLDS_PATH)
    assert th.version and th.revenue.strong_growth_at_least == 0.25
    s = Seed(client)
    s.fact(2024, "revenue", 5000000, doc="PL-A")
    s.fact(2025, "revenue", 6000000, doc="PL-B")
    set_thresholds(th.model_copy(update={"revenue": th.revenue.model_copy(update={"strong_growth_at_least": 0.15}),
                                         "version": "test"}))
    prof = s.build()
    assert indicator(prof, "FY2025-26", "REVENUE")["indicator"] == "STRONG"
    assert indicator(prof, "FY2025-26", "REVENUE")["thresholds_version"] == "test"
    assert metric(prof, "FY2025-26", "revenue_yoy_growth")["value"] == "0.200000"
