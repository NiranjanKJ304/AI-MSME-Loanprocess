"""Deterministic forecasting: models, backtesting, model selection, data quality (missing / partial /
conflicting / outliers), seasonality, uncertainty, provenance and APIs. Expected values are
hand-calculated (shown in comments)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app import database
from app.forecasting.config import DEFAULT_CONFIG_PATH, SeriesConfig, load_config, set_config
from app.forecasting.models import backtest, candidate_models, error_metrics, linear_fit, select_model
from app.forecasting.service import build_forecasts
from app.models import Application
from app.models.enums import DocumentType, FactAvailability
from scripts import synthetic_docs as S
from tests.conftest import create_app, sample_documents, upload
from tests.test_financial_health import Seed

A = FactAvailability
DT = DocumentType
D = Decimal
FY_MONTHS = [f"2024-{m:02d}" for m in range(4, 13)] + [f"2025-{m:02d}" for m in range(1, 4)]


class FSeed(Seed):
    def build(self) -> dict:
        self.db.commit()
        build_forecasts(self.db, self.db.get(Application, self.app_id))
        self.db.commit()
        self.db.close()
        r = self.client.get(f"/api/applications/{self.app_id}/forecasts")
        assert r.status_code == 200, r.text
        return r.json()

    def detail(self, metric: str) -> dict:
        r = self.client.get(f"/api/applications/{self.app_id}/forecasts/{metric}")
        assert r.status_code == 200, r.text
        return r.json()


def values(f: dict) -> list[str | None]:
    return [x["predicted_value"] for x in f["forecast"]]


def check(f: dict, name: str) -> dict:
    return next(c for c in f["data_quality"] if c["check"] == name)


@pytest.fixture(autouse=True)
def default_config():
    set_config(None)
    yield
    set_config(None)


# =========================================================================== pure models
def test_baseline_moving_average_and_linear_models():
    naive, ma, lin = candidate_models(3, 6, seasonal=False)
    y = [D(100), D(110), D(120), D(130)]
    assert naive.predict(y, 1) == D(130) and naive.predict(y, 3) == D(130)  # latest value, flat
    assert ma.predict([D(100), D(120), D(100), D(120)], 1) == D(340) / 3  # (120 + 100 + 120) / 3
    assert linear_fit(y) == (D(100), D(10))  # y = 100 + 10 t
    assert lin.predict(y, 1) == D(140) and lin.predict(y, 2) == D(150)


def test_error_metrics_hand_calculated():
    e = error_metrics([D(100), D(200)], [D(110), D(180)])  # errors -10, +20
    assert e.mae == D("15.00")  # (10 + 20) / 2
    assert e.rmse == D("15.81")  # sqrt((100 + 400) / 2) = sqrt(250)
    assert e.mape == D("0.100000")  # (10/100 + 20/200) / 2
    zero = error_metrics([D(0), D(100)], [D(10), D(100)])
    assert zero.mape is None and "zero or negative" in zero.mape_note


def test_backtest_never_uses_future_observations():
    y = [D(v) for v in (10, 12, 14, 16, 18, 20)]
    keys = [f"k{i}" for i in range(6)]
    naive = candidate_models(3, 6, False)[0]
    bt = backtest(naive, y, keys, start=3)
    assert [p["period"] for p in bt.predictions] == ["k3", "k4", "k5"]
    for i, p in enumerate(bt.predictions, start=3):
        assert p["trained_on"] == {"start": "k0", "end": f"k{i - 1}", "observations": i}  # strictly earlier only
        assert p["predicted"] == str(y[i - 1]) + ".00"
    assert bt.test_period == {"start": "k3", "end": "k5"} and bt.errors.mae == D("2.00")


def test_model_selection_picks_lowest_backtest_mae_with_simplest_tie_break():
    keys = ["a", "b", "c", "d"]
    sel = select_model(candidate_models(2, 2, False), [D(50), D(70), D(50), D(70)], keys, 1)
    maes = {b.model: b.errors.mae for b in sel.backtests}
    # test points c, d. naive: |50-70|, |70-50| -> 20; MA(2): 60 vs 50 / 70 -> 10;
    # linear: [50,70] -> 90 (err 40); [50,70,50] -> flat 56.67 (err 13.33) -> 26.67
    assert maes == {"naive": D("20.00"), "moving_average": D("10.00"), "linear_trend": D("26.67")}
    assert sel.model.name == "moving_average"
    flat = select_model(candidate_models(2, 2, False), [D(5)] * 4, keys, 1)
    assert flat.model.name == "naive"  # all MAE 0 -> simplest
    few = select_model(candidate_models(3, 6, False), [D(1)] * 4, keys, 3)
    assert [b.model for b in few.backtests] == ["naive"]
    assert {r["model"] for r in few.rejected} == {"moving_average", "linear_trend"}


def test_config_requires_a_possible_backtest():
    with pytest.raises(ValueError):
        SeriesConfig(min_observations=2, low_confidence_below=5, moving_average_window=2, linear_trend_min_train=2,
                     min_backtest_points=3, interval_min_backtest_points=3, horizon=1)
    assert load_config(DEFAULT_CONFIG_PATH).monthly.seasonality_min_months == 24


# =========================================================================== insufficient history
def test_no_history_means_no_forecast(client):
    prof = FSeed(client).build()
    assert [f["metric"] for f in prof["forecasts"]] == ["revenue", "business_inflow", "business_outflow",
                                                        "net_business_cash_flow"]
    for f in prof["forecasts"]:
        assert f["status"] == "NOT_AVAILABLE" and f["forecast"] == [] and f["model"] is None
        assert f["reason"].startswith("no historical observations")


def test_insufficient_history(client):
    s = FSeed(client)
    s.fact(2024, "revenue", 6000000)
    for ym in FY_MONTHS[:5]:
        s.month(ym, inflow=100000, outflow=80000)
    prof = s.build()
    rev, inflow = prof["forecasts"][0], prof["forecasts"][1]
    assert rev["status"] == "NOT_AVAILABLE" and rev["forecast"] == []
    assert "1 consecutive complete annual observation(s)" in rev["reason"] and "at least 2" in rev["reason"]
    assert inflow["status"] == "NOT_AVAILABLE" and "at least 6 required" in inflow["reason"]
    assert inflow["seasonality"]["status"] == "NOT_AVAILABLE"
    assert [h["actual"] for h in inflow["historical"]] == ["100000.00"] * 5  # history still reported


# =========================================================================== revenue
def test_revenue_linear_trend_forecast(client):
    s = FSeed(client)
    for i, v in enumerate((5000000, 6000000, 7000000)):
        s.fact(2021 + i, "revenue", v, doc=f"PL-{2021 + i}", page=2)
    s.build()
    f = s.detail("revenue")
    # backtest point FY2023-24: naive 60L (err 10L), MA(2) 55L (err 15L), linear [50L,60L] -> 70L (err 0)
    assert f["model"] == "linear_trend"
    assert f["forecast"][0]["period_key"] == "FY2024-25" and values(f) == ["8000000.00"]  # 50L + 10L x 3
    assert f["status"] == "LOW_CONFIDENCE" and "only 3 historical annual observations" in f["reason"]
    assert f["training_period"] == {"start": "FY2021-22", "end": "FY2023-24", "observations": 3}
    bt = f["backtest"]
    assert bt["model"] == "linear_trend" and bt["test_period"] == {"start": "FY2023-24", "end": "FY2023-24"}
    assert bt["training_period"]["start"] == "FY2021-22" and bt["training_period"]["end"] == "FY2022-23"
    assert bt["error_metrics"]["mae"] == "0.00"
    # uncertainty: one backtest error is not enough for an interval
    assert f["forecast"][0]["lower_bound"] is None and f["uncertainty"]["interval"] is None
    assert "only 1 backtest error" in f["uncertainty"]["reason"]
    assert f["seasonality"]["status"] == "NOT_AVAILABLE"
    assert "projection, not an actual" in f["forecast"][0]["explanation"]
    assert "Revenue basis: P&L revenue from operations." in f["assumptions"]


def test_revenue_moving_average_selected(client):
    s = FSeed(client)
    for i, v in enumerate((5000000, 7000000, 5000000, 7000000)):
        s.fact(2020 + i, "revenue", v, doc=f"PL-{i}")
    s.build()
    f = s.detail("revenue")
    maes = {c["model"]: c["error_metrics"]["mae"] for c in f["model_selection"]["candidates"]}
    assert maes == {"naive": "2000000.00", "moving_average": "1000000.00", "linear_trend": "2666666.67"}
    assert f["model"] == "moving_average" and values(f) == ["6000000.00"]  # (50L + 70L) / 2


def test_conflicting_revenue_observation_is_excluded(client):
    s = FSeed(client)
    for i, v in enumerate((5000000, 6000000, 7000000)):
        s.fact(2021 + i, "turnover", v, doc=f"ITR-{2021 + i}", dtype=DT.ITR)
    itr = s.fact(2024, "turnover", 8000000, doc="ITR-2024", dtype=DT.ITR)
    gst = s.fact(2024, "gst_turnover", 7500000, doc="GSTR9-2024", dtype=DT.GST_RETURN)
    s.conflict(itr, gst)
    s.build()
    f = s.detail("revenue")
    fy25 = next(h for h in f["historical"] if h["period_key"] == "FY2024-25")
    assert fy25["actual"] is None and fy25["source_status"] == "CONFLICTING" and not fy25["used_for_training"]
    assert fy25["exclusion_reason"].startswith("CONFLICTING") and len(fy25["sources"]["conflicts"]) == 1
    assert {fy25["sources"]["conflicts"][0]["source_a"], fy25["sources"]["conflicts"][0]["source_b"]} == \
        {"ITR-2024", "GSTR9-2024"}
    # trained on FY2021-22..FY2023-24 (50L, 60L, 70L); FY2025-26 is 2 steps ahead: 50L + 10L x 4 = 90L
    assert f["training_period"]["end"] == "FY2023-24"
    assert f["forecast"][0]["period_key"] == "FY2025-26" and f["forecast"][0]["horizon"] == 2
    assert values(f) == ["9000000.00"] and f["status"] == "LOW_CONFIDENCE"
    assert check(f, "conflicting_facts")["result"] == "WARN" and check(f, "forecast_horizon")["result"] == "WARN"


# =========================================================================== monthly cash flow
def test_constant_cash_flow_uses_naive_baseline_with_interval(client):
    s = FSeed(client)
    for ym in FY_MONTHS:
        s.month(ym, inflow=100000, outflow=80000)
    prof = s.build()
    inflow, outflow, net = prof["forecasts"][1:]
    for f, v in ((inflow, "100000.00"), (outflow, "80000.00"), (net, "20000.00")):
        assert f["model"] == "naive" and f["status"] == "AVAILABLE", f  # all MAE 0 -> simplest model
        assert values(f) == [v] * 3
        assert [x["period_key"] for x in f["forecast"]] == ["2025-04", "2025-05", "2025-06"]
        assert [x["horizon"] for x in f["forecast"]] == [1, 2, 3]
        # 6 backtest points (2024-10..2025-03) with zero error -> zero-width 80% interval
        assert f["backtest"]["test_period"] == {"start": "2024-10", "end": "2025-03"}
        assert f["forecast"][0]["lower_bound"] == v and f["forecast"][0]["upper_bound"] == v
        assert f["forecast"][0]["interval_level"] == 0.8
    assert net["backtest"]["error_metrics"]["mape"] == "0.000000"


def test_linear_trend_inflow_outflow_and_net(client):
    s = FSeed(client)
    for i, ym in enumerate(FY_MONTHS):
        s.month(ym, inflow=100000 + 10000 * i, outflow=50000)
    prof = s.build()
    inflow, outflow, net = prof["forecasts"][1:]
    assert inflow["model"] == "linear_trend" and values(inflow) == ["220000.00", "230000.00", "240000.00"]
    assert outflow["model"] == "naive" and values(outflow) == ["50000.00"] * 3
    assert net["model"] == "linear_trend" and values(net) == ["170000.00", "180000.00", "190000.00"]
    d = s.detail("business_inflow")
    maes = {c["model"]: c["error_metrics"]["mae"] for c in d["model_selection"]["candidates"]}
    assert maes == {"naive": "10000.00", "moving_average": "20000.00", "linear_trend": "0.00"}


def test_moving_average_monthly_and_no_interval_with_few_backtest_points(client):
    s = FSeed(client)
    for i, ym in enumerate(FY_MONTHS[:8]):
        s.month(ym, inflow=100000 if i % 2 == 0 else 120000, outflow=10000)
    s.build()
    f = s.detail("business_inflow")
    # naive MAE 20,000; MA(3) MAE 13,333.33; linear needs 6 + 3 observations (have 8) -> rejected
    assert f["model"] == "moving_average"
    assert values(f) == ["113333.33"] * 3  # (120,000 + 100,000 + 120,000) / 3
    assert [r["model"] for r in f["model_selection"]["rejected"]] == ["linear_trend"]
    assert f["status"] == "LOW_CONFIDENCE" and "only 8 historical monthly observations" in f["reason"]
    assert f["forecast"][0]["lower_bound"] is None and "only 5 backtest error(s)" in f["uncertainty"]["reason"]


def test_missing_months_are_not_filled(client):
    s = FSeed(client)
    for ym in FY_MONTHS[:3] + FY_MONTHS[4:]:  # 2024-07 has no data
        s.month(ym, inflow=100000, outflow=80000)
    s.build()
    f = s.detail("business_inflow")
    assert "2024-07" not in [h["period_key"] for h in f["historical"]]
    assert check(f, "missing_periods")["periods"] == ["2024-07"]
    early = [h for h in f["historical"] if h["period_key"] in FY_MONTHS[:3]]
    assert all(not h["used_for_training"] and "not consecutive" in h["exclusion_reason"] for h in early)
    assert f["training_period"] == {"start": "2024-08", "end": "2025-03", "observations": 8}
    assert f["status"] == "LOW_CONFIDENCE" and values(f) == ["100000.00"] * 3


def test_partial_months_are_not_complete_observations(client):
    s = FSeed(client)
    for ym in FY_MONTHS[:9]:
        s.month(ym, inflow=100000, outflow=80000)
    s.month(FY_MONTHS[9], inflow=30000, outflow=5000, availability=A.PARTIAL)  # Jan 2025, statement ends mid-month
    s.build()
    f = s.detail("business_inflow")
    jan = next(h for h in f["historical"] if h["period_key"] == "2025-01")
    assert jan["actual"] is None and jan["source_status"] == "PARTIAL" and not jan["used_for_training"]
    assert f["training_period"]["end"] == "2024-12"
    # forecasts start after the last observed month; Jan 2025 itself is never "filled"
    assert [(x["period_key"], x["horizon"]) for x in f["forecast"]] == [("2025-02", 2), ("2025-03", 3), ("2025-04", 4)]
    assert check(f, "partial_periods")["periods"] == ["2025-01"] and f["status"] == "LOW_CONFIDENCE"


def test_negative_projection_is_not_reported_for_a_non_negative_metric(client):
    s = FSeed(client)
    for i, ym in enumerate(FY_MONTHS):
        s.month(ym, inflow=120000 - 10000 * i, outflow=1000)  # 120k ... 10k
    s.build()
    f = s.detail("business_inflow")
    assert f["model"] == "linear_trend"
    assert values(f) == ["0.00", None, None]  # -10k and -20k are not meaningful
    assert f["forecast"][1]["status"] == "NOT_AVAILABLE" and "negative" in f["forecast"][1]["note"]


def test_low_classification_coverage_and_outliers_lower_confidence(client):
    s = FSeed(client)
    vals = [100, 102, 98, 101, 99, 100, 103, 97, 100, 101, 99, 1000]
    for ym, v in zip(FY_MONTHS, vals):
        s.month(ym, inflow=v * 1000, outflow=50000, unknown_inflow=60000)
    s.build()
    f = s.detail("business_inflow")
    assert check(f, "outliers")["periods"] == ["2025-03"]
    assert next(h for h in f["historical"] if h["period_key"] == "2025-03")["outlier"] is True
    assert next(h for h in f["historical"] if h["period_key"] == "2025-03")["actual"] == "1000000.00"  # unchanged
    assert check(f, "unknown_transaction_share")["result"] == "WARN"
    assert f["status"] == "LOW_CONFIDENCE"


# =========================================================================== seasonality
def test_seasonality_needs_two_years(client):
    s = FSeed(client)
    for ym in FY_MONTHS:
        s.month(ym, inflow=100000, outflow=80000)
    s.build()
    seas = s.detail("business_inflow")["seasonality"]
    assert seas["status"] == "NOT_AVAILABLE" and "at least 24 consecutive complete months" in seas["reason"]
    assert "have 12" in seas["reason"]


def test_seasonality_detected_with_two_full_cycles(client):
    s = FSeed(client)
    months = [f"2023-{m:02d}" for m in range(4, 13)] + [f"2024-{m:02d}" for m in range(1, 4)] + FY_MONTHS
    for ym in months:
        high = int(ym[5:]) in (10, 11, 12, 1, 2, 3)
        s.month(ym, inflow=200000 if high else 100000, outflow=50000)
    s.build()
    f = s.detail("business_inflow")
    assert f["seasonality"]["status"] == "DETECTED" and f["seasonality"]["strength"] >= 0.6
    assert f["model"] == "seasonal_naive"  # zero backtest error over 12 test months
    assert values(f) == ["100000.00"] * 3  # Apr-Jun 2025 = Apr-Jun 2024


# =========================================================================== end to end / provenance / API
def test_sample_application_forecast_provenance(client, business):
    app = create_app(client)
    upload(client, app["id"], sample_documents(business))
    base = f"/api/applications/{app['id']}"
    prof = client.get(f"{base}/forecasts").json()
    assert prof["built"] and prof["forecasts"][0]["status"] == "NOT_AVAILABLE"  # one FY of revenue only
    f = client.get(f"{base}/forecasts/business_inflow").json()
    assert f["status"] in ("AVAILABLE", "LOW_CONFIDENCE") and len(f["forecast"]) == 3
    assert "classified transactions" in f["provenance"]["chain"]
    assert len(f["provenance"]["training_observation_ids"]) == f["training_period"]["observations"] == 12

    # historical actuals are exactly the monthly business inflow (not total bank credits)
    monthly = {m["month"]: m for m in client.get(f"{base}/cashflow/monthly").json()}
    for h in f["historical"]:
        assert D(h["actual"]) == D(monthly[h["period_key"]]["values"]["business_inflow"])
        assert h["sources"]["monthly_aggregate_ids"] == [monthly[h["period_key"]]["id"]]
        assert h["evidence"][0]["document_type"] == "BANK_STATEMENT" and h["evidence"][0]["pages"]
    assert any(D(m["values"]["total_inflow"]) > D(m["values"]["business_inflow"]) for m in monthly.values())

    # observation -> canonical transaction -> statement row -> page/bbox, and only business INCOME
    tid = f["historical"][0]["sources"]["transaction_ids"][0]
    t = client.get(f"/api/transactions/{tid}/classification").json()
    assert t["classification"]["category_group"] == "INCOME" and t["classification"]["nature"] == "BUSINESS"
    assert t["provenance"]["statement_row"]["page_number"] and t["provenance"]["statement_row"]["bbox"]

    doc_id = f["historical"][0]["evidence"][0]["document_id"]
    stages = {x["stage"]: x["status"] for x in client.get(f"/api/documents/{doc_id}/status").json()["pipeline"]}
    assert stages["FINANCIAL_FORECAST"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS")


def test_revenue_forecast_provenance_to_facts(client):
    s = FSeed(client)
    for i, v in enumerate((5000000, 6000000, 7000000)):
        s.fact(2021 + i, "revenue", v, doc=f"PL-{2021 + i}", page=3)
    s.build()
    f = s.detail("revenue")
    assert f["provenance"]["chain"].startswith("forecast -> historical observations -> canonical financial facts")
    assert len(f["provenance"]["fact_ids"]) == 3
    for h in f["historical"]:
        fact = h["sources"]["facts"][0]
        assert fact["page"] == 3 and fact["bbox"] and fact["field_id"]
        assert h["evidence"][0]["document_code"] == f"PL-{h['period_key'][2:6]}" and h["evidence"][0]["pages"] == [3]


def test_rebuild_is_idempotent_and_unknown_metric_404(client, business):
    app = create_app(client)
    upload(client, app["id"], sample_documents(business))
    base = f"/api/applications/{app['id']}"

    def strip(p):
        return [(f["metric"], f["status"], f["model"], values(f), [h["actual"] for h in f["historical"]])
                for f in p["forecasts"]]

    before = client.get(f"{base}/forecasts").json()
    r = client.post(f"{base}/forecasts/rebuild")
    assert r.status_code == 200 and r.json()["metrics"] == 4
    after = client.get(f"{base}/forecasts").json()
    assert strip(before) == strip(after)
    db = database.SessionLocal()
    try:
        from app.models import ForecastRun
        assert db.query(ForecastRun).filter(ForecastRun.application_id == uuid.UUID(app["id"])).count() == 4
    finally:
        db.close()
    assert client.get(f"{base}/forecasts/total_credits").status_code == 404
