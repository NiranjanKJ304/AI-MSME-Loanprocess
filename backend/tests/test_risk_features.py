"""Risk feature engineering: centralized definitions, versioning, every feature group, missing /
conflicting / low-confidence / partial inputs, validation, provenance, reproducible snapshots and APIs.
Expected values are hand-calculated (shown in comments). No model, score or decision is involved."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.financial_health.service import build_financial_health
from app.forecasting.service import build_forecasts
from app.models import Application
from app.models.enums import DocumentType, FactAvailability
from app.repayment.service import build_repayment_capacity
from app.risk_features import calculators as C
from app.risk_features.calculators import FeatureValue
from app.risk_features.definitions import DEFINITIONS, FEATURE_VERSION, GROUPS, definitions_hash
from app.risk_features.validation import validate_feature, validate_set
from tests.conftest import create_app, sample_documents, upload
from tests.test_financial_health import Seed

A = FactAvailability
DT = DocumentType
D = Decimal
FY_MONTHS = [f"2024-{m:02d}" for m in range(4, 13)] + [f"2025-{m:02d}" for m in range(1, 4)]
TERMS = {"requested_amount": "1200000", "annual_interest_rate": "0", "tenure_months": 12,
         "repayment_frequency": "MONTHLY", "provided_by": "officer-3"}


class RFSeed(Seed):
    def build(self, terms: dict | None = TERMS) -> dict:
        self.db.commit()
        app = self.db.get(Application, self.app_id)
        build_financial_health(self.db, app)
        build_forecasts(self.db, app)
        build_repayment_capacity(self.db, app)
        self.db.commit()
        self.db.close()
        if terms:
            r = self.client.post(f"/api/applications/{self.app_id}/repayment-capacity", json=terms)
            assert r.status_code == 200, r.text
        r = self.client.post(f"/api/applications/{self.app_id}/risk-features")
        assert r.status_code == 200, r.text
        return r.json()


def financials(s: RFSeed) -> None:
    s.fact(2023, "revenue", 8000000, doc="PL-23")
    s.fact(2023, "pat", 640000, doc="PL-23")
    for metric, v in (("revenue", 10000000), ("gross_profit", 4000000), ("pat", 900000)):
        s.fact(2024, metric, v, doc="PL-24", page=2)
    for metric, v in (("current_assets", 3000000), ("current_liabilities", 2000000), ("borrowings", 2500000),
                      ("net_worth", 5000000), ("cash_and_bank", 400000), ("total_assets", 9000000)):
        s.fact(2024, metric, v, doc="BS-24", dtype=DT.BALANCE_SHEET, page=3)


def bank(s: RFSeed, months=FY_MONTHS, partial_last=False) -> None:
    for i, ym in enumerate(months):
        inflow = 500000 if i < 6 else 600000
        s.month(ym, inflow=inflow, outflow=400000, financing_outflow=50000, transfer_inflow=20000,
                unknown_inflow=30000, total_inflow=inflow + 50000, total_outflow=450000,
                availability=A.PARTIAL if partial_last and i == len(months) - 1 else A.AVAILABLE)


def feat(v: dict, name: str) -> dict:
    return next(f for fs in v["groups"].values() for f in fs if f["feature_name"] == name)


def val(v: dict, name: str):
    return feat(v, name)["value"]


@pytest.fixture
def full(client) -> dict:
    s = RFSeed(client)
    financials(s)
    bank(s)
    return s.build()


# =========================================================================== definitions / versioning
def test_centralized_versioned_definitions():
    names = [d.name for d in DEFINITIONS]
    assert len(names) == len(set(names)) == 68 and set(C.CALCULATORS) == set(names)
    assert {d.group for d in DEFINITIONS} == set(GROUPS)
    for d in DEFINITIONS:
        assert d.version == FEATURE_VERSION == "v1" and d.formula and d.required_inputs and d.description
    with pytest.raises(ValueError, match="duplicate calculator"):
        C.calc("revenue")(lambda ctx, done: None)
    with pytest.raises(ValueError, match="without a definition"):
        C.calc("probability_of_default")(lambda ctx, done: None)


def test_snapshot_records_version_definitions_and_metadata(full):
    meta = full["calculation_metadata"]
    assert full["feature_version"] == "v1" and meta["definitions_hash"] == definitions_hash()
    assert len(meta["source_fingerprint"]) == 64 and meta["generated_at"]
    assert meta["config"]["bank_window_months"] == 12
    assert meta["upstream_versions"]["repayment_engine_version"] == "repayment-engine-1.0"
    assert len(full["definitions"]) == 68 and full["valid"] and full["validation"]["errors"] == []
    f = feat(full, "profit_margin")
    assert f["feature_version"] == "v1" and f["source_layer"] == "FINANCIAL_HEALTH"
    assert f["calculation"]["formula"].startswith("health metric 'net_margin'")
    assert full["status_summary"]["features"] == 68
    assert not any(k in str(full["groups"]).lower() for k in ("risk_score", "probability_of_default", "approve"))


# =========================================================================== A. financials
def test_financial_features(full):
    expect = {"revenue": "10000000.00", "revenue_growth": "0.250000",  # 10M / 8M - 1
              "gross_profit": "4000000.00", "net_profit": "900000.00", "gross_margin": "0.400000",
              "profit_margin": "0.090000", "current_ratio": "1.500000", "debt_to_equity": "0.500000",
              "total_debt": "2500000.00", "net_worth": "5000000.00", "cash_balance": "400000.00",
              "operating_cash_flow": "1800000.00"}  # 6 x 1,00,000 + 6 x 2,00,000
    for name, v in expect.items():
        f = feat(full, name)
        assert (f["value"], f["status"], f["period"]) == (v, "AVAILABLE", "FY2024-25"), f


# =========================================================================== B. transactions
def test_transaction_features(full):
    expect = {
        "average_monthly_business_inflow": "550000.00", "average_monthly_business_outflow": "400000.00",
        "average_monthly_business_cash_flow": "150000.00", "positive_cash_flow_month_ratio": "1.000000",
        "business_inflow_growth": "0.200000",  # (6,00,000 - 5,00,000) / 5,00,000
        "business_outflow_growth": "0.000000",
        "unknown_credit_ratio": "0.050000",  # 3,60,000 / 72,00,000
        "unknown_debit_ratio": "0.000000",
        "transfer_ratio": "0.019048",  # 2,40,000 / 1,26,00,000
        "financing_inflow_ratio": "0.000000",
        "business_transaction_coverage": "0.904762",  # 1,14,00,000 / 1,26,00,000
    }
    for name, v in expect.items():
        f = feat(full, name)
        assert (f["value"], f["status"], f["period"]) == (v, "AVAILABLE", "2024-04..2025-03"), f
    # counts need classified transactions; none were seeded -> NOT_AVAILABLE, never 0
    for name in ("recurring_obligation_count", "reversal_count", "unknown_transaction_ratio"):
        f = feat(full, name)
        assert f["value"] is None and f["status"] == "NOT_AVAILABLE" and "no classified" in f["reason"]


# =========================================================================== C. stability
def test_stability_features(full):
    expect = {"revenue_trend": "0.250000", "profit_trend": "0.406250",  # 9,00,000 / 6,40,000 - 1
              "cash_flow_trend": "12587.41",  # OLS slope: 18,00,000 / 143
              "profitable_month_ratio": "1.000000",  # 50,000 / 1,50,000 left after existing EMI
              "negative_cash_flow_month_ratio": "0.000000",
              "inflow_volatility": "0.090909",  # std 50,000 / mean 5,50,000
              "outflow_volatility": "0.000000",
              "cash_flow_volatility": "0.090909",  # std(net) 50,000 / mean inflow 5,50,000
              "partial_month_ratio": "0.000000"}
    for name, v in expect.items():
        assert val(full, name) == v, name
    assert feat(full, "cash_flow_trend")["unit"] == "INR_PER_MONTH"


# =========================================================================== D. forecast
def test_forecast_features(full):
    # inflow 5L x 6 then 6L x 6: naive misses only the step (1L) over 6 backtest points
    assert val(full, "forecast_model") == "naive"
    assert val(full, "forecast_business_inflow") == "600000.00" and feat(full, "forecast_business_inflow")["period"] == "2025-04"
    assert val(full, "forecast_error_mae") == "16666.67"  # 1,00,000 / 6
    assert val(full, "forecast_error_rmse") == "40824.83"  # sqrt(1,00,000^2 / 6)
    assert val(full, "forecast_mape") == "0.027778"  # (1,00,000 / 6,00,000) / 6
    width = D(val(full, "forecast_interval_width"))
    assert width == 2 * (D("1.2816") * D("40824.83")).quantize(D("0.01"))
    assert val(full, "forecast_business_outflow") == "400000.00"
    assert val(full, "forecast_net_cash_flow") == "200000.00"
    # two FYs of revenue: naive only, LOW_CONFIDENCE carried into the feature
    rev = feat(full, "forecast_revenue")
    assert (rev["value"], rev["status"], rev["period"]) == ("10000000.00", "LOW_CONFIDENCE", "FY2025-26")
    assert val(full, "forecast_data_quality") == "LOW_CONFIDENCE"


# =========================================================================== E. repayment
def test_repayment_features(full):
    expect = {"requested_loan_amount": "1200000.00", "tenure_months": 12, "proposed_periodic_payment": "100000.00",
              "existing_debt_service": "50000.00", "proposed_debt_service": "100000.00",
              "total_debt_service": "150000.00", "historical_dscr": "1.000000",  # 1,50,000 / 1,50,000
              "forecast_dscr": "1.333333",  # (6,00,000 - 4,00,000) / 1,50,000
              "post_debt_service_cash_flow": "0.00",
              "stress_dscr_revenue_down": "0.266667",  # (4,40,000 - 4,00,000) / 1,50,000
              "stress_dscr_expense_up": "0.733333",  # (5,50,000 - 4,40,000) / 1,50,000
              "stress_dscr_combined": "0.000000",  # 4,40,000 - 4,40,000
              "repayment_data_quality": "AVAILABLE"}
    for name, v in expect.items():
        assert val(full, name) == v, name
    assert feat(full, "historical_dscr")["period"] == "2024-04..2025-03"
    assert feat(full, "forecast_dscr")["period"] == "2025-04..2025-06"  # bases kept separate
    st = feat(full, "statement_dscr")  # depreciation / interest not provided -> not calculated
    assert st["value"] is None and st["status"] == "NOT_AVAILABLE"
    terms = feat(full, "requested_loan_amount")["provenance"]["user_provided_inputs"][0]
    assert terms["source"] == "USER_PROVIDED" and terms["provided_by"] == "officer-3"


# =========================================================================== F. data quality
def test_data_quality_features(full):
    assert val(full, "financial_fact_coverage") == "1.000000"  # all 9 core metrics present
    assert val(full, "conflicting_fact_count") == 0 and val(full, "low_confidence_fact_count") == 0
    assert val(full, "transaction_classification_coverage") == "0.971429"  # (1.26 Cr - 3.6 L) / 1.26 Cr
    assert val(full, "missing_required_data_count") == 0 and val(full, "partial_period_count") == 0
    assert val(full, "document_completeness") == "0.000000"  # documents seeded as facts only, none uploaded
    ext = feat(full, "extraction_confidence")
    assert ext["value"] is None and ext["status"] == "NOT_AVAILABLE"
    assert full["data_quality_summary"]["bank_window"] == "2024-04..2025-03"


# =========================================================================== missing / conflicting / low-confidence / partial
def test_missing_inputs_are_null_never_zero(client):
    v = RFSeed(client).build(terms=None)
    assert v["valid"]
    for fs in v["groups"].values():
        for f in fs:
            if f["status"] in ("NOT_AVAILABLE", "CONFLICTING"):
                assert f["value"] is None and f["reason"], f
    for name in ("profit_margin", "revenue", "average_monthly_business_inflow", "historical_dscr", "forecast_model"):
        assert feat(v, name)["status"] == "NOT_AVAILABLE" and val(v, name) is None
    assert val(v, "missing_required_data_count") == 6  # all six required features missing
    assert set(v["validation"]["missing_inputs"]) >= {"revenue", "historical_dscr"}


def test_conflicting_inputs_are_not_resolved(client):
    s = RFSeed(client)
    financials(s)
    other = s.fact(2024, "revenue", 9000000, doc="PL-24B")
    first = next(f for f in s.db.new if getattr(f, "metric", None) == "revenue" and f.value == D(10000000))
    s.conflict(first, other, metric="revenue")
    bank(s)
    v = s.build()
    for name in ("revenue", "profit_margin", "gross_margin"):
        f = feat(v, name)
        assert f["status"] == "CONFLICTING" and f["value"] is None, f
    assert feat(v, "revenue")["provenance"]["conflict_ids"]
    assert val(v, "conflicting_fact_count") == 1
    assert val(v, "financial_fact_coverage") == "0.888889"  # 8 of 9 core metrics (revenue conflicting)
    assert "revenue" in v["validation"]["conflicting_inputs"] and v["valid"]


def test_low_confidence_inputs_carried(client):
    s = RFSeed(client)
    financials(s)
    s.fact(2024, "inventory", 100000, doc="BS-24", dtype=DT.BALANCE_SHEET, availability=A.LOW_CONFIDENCE)
    bank(s)
    v = s.build()
    assert val(v, "low_confidence_fact_count") == 1
    assert feat(v, "forecast_revenue")["status"] == "LOW_CONFIDENCE"


def test_partial_period(client):
    s = RFSeed(client)
    financials(s)
    bank(s, partial_last=True)  # Mar 2025 PARTIAL
    v = s.build()
    f = feat(v, "average_monthly_business_inflow")
    # 6 x 5,00,000 + 5 x 6,00,000 over 11 complete months = 5,45,454.55
    assert (f["value"], f["status"]) == ("545454.55", "PARTIAL")
    assert f["calculation"]["details"]["partial_months_excluded"] == ["2025-03"]
    assert val(v, "partial_month_ratio") == "0.083333" and val(v, "partial_period_count") == 1  # 1 / 12


# =========================================================================== validation
def test_validation_rejects_bad_features():
    ok = FeatureValue(D("1.5"), A.AVAILABLE, "FY2024-25", provenance={"chain": "x"})
    assert validate_feature("current_ratio", ok) == []
    assert any("must have a null value" in e for e in validate_feature(
        "profit_margin", FeatureValue(D(0), A.NOT_AVAILABLE, reason="missing", provenance={"x": 1})))
    assert any("expected TEXT" in e for e in validate_feature(
        "forecast_model", FeatureValue(D(1), A.AVAILABLE, provenance={"x": 1})))
    assert any("expected an integer" in e for e in validate_feature(
        "reversal_count", FeatureValue(D("1.5"), A.AVAILABLE, provenance={"x": 1})))
    assert any("without provenance" in e for e in validate_feature("revenue", FeatureValue(D(1), A.AVAILABLE)))
    assert any("without a reason" in e for e in validate_feature("revenue", FeatureValue(None, A.NOT_AVAILABLE)))
    assert any("unsupported status" in e for e in validate_feature(
        "revenue", FeatureValue(D(1), "GOOD", provenance={"x": 1})))
    assert validate_feature("risk_score", ok) == ["risk_score: no feature definition"]
    dup = validate_set([("current_ratio", ok), ("current_ratio", ok)])
    assert not dup["valid"] and dup["errors"] == ["duplicate feature name: current_ratio"]


# =========================================================================== reproducibility
def test_snapshot_reuse_rebuild_and_staleness(client):
    s = RFSeed(client)
    financials(s)
    bank(s, months=FY_MONTHS[:11])
    first = s.build()
    url = f"/api/applications/{s.app_id}/risk-features"
    again = client.post(url).json()
    assert again["reused"] and again["feature_set_id"] == first["feature_set_id"]
    rebuilt = client.post(f"{url}/rebuild").json()
    assert rebuilt["verified_identical"] is True and rebuilt["differences"] == []
    assert rebuilt["feature_set_id"] == first["feature_set_id"] and not rebuilt["stale"]

    # new source data -> stale, then a new snapshot; the old one is kept
    s2 = RFSeed.__new__(RFSeed)
    s2.client, s2.app_id, s2.periods = client, s.app_id, {}
    from app import database
    s2.db = database.SessionLocal()
    s2.month(FY_MONTHS[11], inflow=600000, outflow=400000, financing_outflow=50000, total_inflow=650000,
             total_outflow=450000)
    s2.db.commit()
    s2.db.close()
    assert client.get(url).json()["stale"] is True
    newer = client.post(url).json()
    assert newer["feature_set_id"] != first["feature_set_id"] and not newer["reused"]
    assert newer["calculation_metadata"]["source_fingerprint"] != first["calculation_metadata"]["source_fingerprint"]
    db = database.SessionLocal()
    try:
        from app.models import RiskFeatureSet
        sets = db.query(RiskFeatureSet).filter(RiskFeatureSet.application_id == s.app_id).all()
        assert len(sets) == 2 and sum(x.is_latest for x in sets) == 1
    finally:
        db.close()


def test_same_source_data_gives_identical_features(client):
    snaps = []
    for _ in range(2):
        s = RFSeed(client)
        financials(s)
        bank(s)
        snaps.append(s.build())
    sig = lambda v: [(f["feature_name"], f["value"], f["status"], f["period"])  # noqa: E731
                     for fs in v["groups"].values() for f in fs]
    assert sig(snaps[0]) == sig(snaps[1])  # deterministic values for identical data


# =========================================================================== end-to-end provenance
def test_provenance_end_to_end(client, business):
    app = create_app(client)
    upload(client, app["id"], sample_documents(business))
    client.post(f"/api/applications/{app['id']}/repayment-capacity", json=TERMS)
    v = client.post(f"/api/applications/{app['id']}/risk-features").json()
    assert v["valid"]

    # risk feature -> health metric -> financial fact -> extracted field -> document page/bbox
    rev = feat(v, "revenue")
    assert rev["provenance"]["chain"].startswith("risk_feature -> financial health metric")
    fact = client.get(f"/api/financial-facts/{rev['provenance']['fact_ids'][0]}").json()
    assert fact["extracted_field"]["source_page"] and fact["extracted_field"]["source_location"]["bbox"]

    # risk feature -> repayment metric -> monthly cash flow -> classified transaction -> statement row
    dscr = feat(v, "historical_dscr")
    assert dscr["provenance"]["chain"].startswith("risk_feature -> repayment metric")
    tid = dscr["provenance"]["transaction_ids"][0]
    t = client.get(f"/api/transactions/{tid}/classification").json()
    assert t["provenance"]["statement_row"]["bbox"] and t["provenance"]["statement_row"]["page_number"]

    # transaction feature -> monthly aggregates -> transactions
    inflow = feat(v, "average_monthly_business_inflow")
    assert inflow["provenance"]["monthly_aggregate_ids"] and inflow["provenance"]["transaction_ids"]
    assert val(v, "extraction_confidence") is not None and val(v, "document_completeness") is not None
    assert val(v, "recurring_obligation_count") is not None  # real classified transactions here
    for fs in v["groups"].values():
        for f in fs:
            if f["value"] is not None:
                assert f["provenance"], f["feature_name"]
