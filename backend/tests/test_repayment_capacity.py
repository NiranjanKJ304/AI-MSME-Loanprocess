"""Repayment capacity: loan terms, EMI / schedule, existing and proposed debt service, cash-flow
capacity, DSCR (historical / forecast / statement), stress scenarios, data quality, provenance and
APIs. Expected values are hand-calculated (shown in comments)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.forecasting.service import build_forecasts
from app.models import Application
from app.models.enums import DocumentType, FactAvailability, GraceTreatment, RepaymentFrequency
from app.repayment.config import set_config
from app.repayment.loan import compute_repayment
from scripts import synthetic_docs as S
from tests.conftest import create_app, sample_documents, upload
from tests.test_financial_health import Seed

A = FactAvailability
D = Decimal
F = RepaymentFrequency
FY_MONTHS = [f"2024-{m:02d}" for m in range(4, 13)] + [f"2025-{m:02d}" for m in range(1, 4)]
ZERO_RATE_12L = {"requested_amount": "1200000", "annual_interest_rate": "0", "tenure_months": 12,
                 "repayment_frequency": "MONTHLY", "provided_by": "officer-7"}


class RSeed(Seed):
    def build(self, terms: dict | None = ZERO_RATE_12L) -> dict:
        self.db.commit()
        build_forecasts(self.db, self.db.get(Application, self.app_id))
        self.db.commit()
        self.db.close()
        url = f"/api/applications/{self.app_id}/repayment-capacity"
        r = self.client.post(url, json=terms) if terms else self.client.post(f"{url}/rebuild")
        assert r.status_code == 200, r.text
        return r.json()


def months(s: RSeed, inflow=500000, outflow=350000, debt=50000, keys=FY_MONTHS, **kw) -> None:
    for ym in keys:
        s.month(ym, inflow=inflow, outflow=outflow, financing_outflow=debt, **kw)


def cap(v: dict, basis: str, metric: str) -> dict:
    return next(m for m in v["cash_flow_capacity"][basis] if m["metric"] == metric)


def scen(v: dict, basis: str, name: str) -> dict:
    return next(s for s in v["stress_scenarios"] if s["basis"] == basis and s["scenario"] == name)


def check(v: dict, name: str) -> dict:
    return next(c for c in v["data_quality"] if c["check"] == name)


@pytest.fixture(autouse=True)
def default_config():
    set_config(None)
    yield
    set_config(None)


# =========================================================================== repayment calculation
def test_emi_calculation():
    r = compute_repayment(D(1000000), D(12), 12, F.MONTHLY)
    # i = 1%; EMI = 10,00,000 x 0.01 x 1.01^12 / (1.01^12 - 1) = 88,848.79
    assert r.periodic_rate == D("0.01") and r.installments == 12 and r.installment == D("88848.79")
    first = r.schedule[0]
    assert (first["interest"], first["principal"]) == ("10000.00", "78848.79")  # 1% of 10L; EMI - interest
    assert r.schedule[-1]["closing"] == "0.00" and r.schedule[-1]["payment"] == "88848.76"  # last clears balance
    assert r.total_repayment == D("1066185.45") and r.total_interest == D("66185.45")
    assert r.formula.startswith("installment = P x i x (1 + i)^n")


def test_zero_interest():
    r = compute_repayment(D(1200000), D(0), 12, F.MONTHLY)
    assert r.installment == D("100000.00") and r.total_interest == D("0.00")  # 12,00,000 / 12
    assert r.formula.startswith("installment = P / n (zero interest)")


def test_quarterly_repayment():
    r = compute_repayment(D(400000), D(8), 12, F.QUARTERLY)
    # i = 8% / 4 = 2%, n = 4: 4,00,000 x 0.02 x 1.02^4 / (1.02^4 - 1) = 1,05,049.50
    assert (r.periods_per_year, r.months_per_period, r.installments) == (4, 3, 4)
    assert r.installment == D("105049.50") and r.monthly_equivalent == D("35016.50")
    assert [x["month"] for x in r.schedule] == [3, 6, 9, 12] and r.schedule[0]["interest"] == "8000.00"


def test_grace_period_treatments():
    io = compute_repayment(D(1200000), D(12), 15, F.MONTHLY, 3, GraceTreatment.INTEREST_ONLY)
    assert [x["payment"] for x in io.schedule[:3]] == ["12000.00"] * 3  # 1% of 12L, principal deferred
    assert io.installment == D("106618.55")  # 12L amortised over 12 months at 1%
    cap_ = compute_repayment(D(1000000), D(12), 15, F.MONTHLY, 3, GraceTreatment.CAPITALISED)
    assert cap_.amortised_principal == D("1030301.00")  # 10L x 1.01^3
    assert [x["payment"] for x in cap_.schedule[:3]] == ["0.00"] * 3


@pytest.mark.parametrize("kw, msg", [
    (dict(amount=D(0)), "requested_amount"),
    (dict(rate=D(-1)), "annual_interest_rate"),
    (dict(tenure=0), "tenure_months"),
    (dict(tenure=10, freq=F.QUARTERLY), "multiples of 3"),
    (dict(grace=3), "grace_period_treatment"),
    (dict(grace=12, treatment=GraceTreatment.INTEREST_ONLY), "shorter than the tenure"),
])
def test_invalid_terms_are_rejected(kw, msg):
    with pytest.raises(ValueError, match=msg):
        compute_repayment(kw.get("amount", D(100000)), kw.get("rate", D(10)), kw.get("tenure", 12),
                          kw.get("freq", F.MONTHLY), kw.get("grace", 0), kw.get("treatment"))


def test_invalid_terms_api_422_and_terms_never_assumed(client):
    s = RSeed(client)
    months(s)
    v = s.build(terms=None)  # no terms posted: nothing is assumed
    assert v["status"] == "NOT_AVAILABLE" and v["outcome"] == "LIMITED_DATA" and v["repayment"] is None
    assert check(v, "proposed_loan_terms")["result"] == "FAIL"
    assert cap(v, "HISTORICAL", "dscr")["value"] is None
    assert cap(v, "HISTORICAL", "cash_available_for_debt_service")["value"] == "150000.00"  # still described
    url = f"/api/applications/{s.app_id}/repayment-capacity"
    for bad in ({**ZERO_RATE_12L, "annual_interest_rate": "-2"}, {**ZERO_RATE_12L, "tenure_months": 0},
                {**ZERO_RATE_12L, "requested_amount": "0"}, {**ZERO_RATE_12L, "repayment_frequency": "WEEKLY"},
                {k: v for k, v in ZERO_RATE_12L.items() if k != "annual_interest_rate"}):
        assert client.post(url, json=bad).status_code == 422


# =========================================================================== capacity / DSCR / scenarios
def test_monthly_capacity_dscr_and_stress(client):
    s = RSeed(client)
    months(s)  # inflow 5,00,000; outflow 3,50,000; existing EMI 50,000 every month
    v = s.build()
    assert v["loan_terms"]["source"] == "USER_PROVIDED" and v["loan_terms"]["provided_by"] == "officer-7"
    assert v["repayment"]["installment"] == "100000.00"
    h = {m: cap(v, "HISTORICAL", m)["value"] for m in (
        "average_monthly_business_inflow", "average_monthly_business_outflow", "cash_available_for_debt_service",
        "existing_debt_service", "proposed_debt_service", "total_debt_service", "post_debt_service_cash_flow",
        "existing_dscr", "dscr", "average_positive_business_cash_flow")}
    assert h == {"average_monthly_business_inflow": "500000.00", "average_monthly_business_outflow": "350000.00",
                 "cash_available_for_debt_service": "150000.00", "existing_debt_service": "50000.00",
                 "proposed_debt_service": "100000.00", "total_debt_service": "150000.00",
                 "post_debt_service_cash_flow": "0.00", "existing_dscr": "3.000000", "dscr": "1.000000",
                 "average_positive_business_cash_flow": "150000.00"}
    assert cap(v, "HISTORICAL", "dscr")["status"] == "AVAILABLE"
    assert cap(v, "HISTORICAL", "dscr")["formula"] == \
        "cash_available_for_debt_service / (existing_debt_service + proposed_debt_service)"
    # forecast: naive 5,00,000 / 3,50,000 -> same DSCR
    f = cap(v, "FORECAST", "dscr")
    assert f["value"] == "1.000000" and f["status"] == "AVAILABLE" and f["period"] == "2025-04..2025-06"
    assert v["outcome"] == "LOW_CAPACITY" and "below the descriptive threshold 1.25" in v["outcome_reasons"][0]

    # stress: inflow -20%, outflow +10%; debt service 1,50,000 unchanged
    expected = {"BASE": ("150000.00", "1.000000", "0.00"),
                "REVENUE_DOWN": ("50000.00", "0.333333", "-100000.00"),  # 4,00,000 - 3,50,000
                "EXPENSE_UP": ("115000.00", "0.766667", "-35000.00"),  # 5,00,000 - 3,85,000
                "COMBINED_STRESS": ("15000.00", "0.100000", "-135000.00")}  # 4,00,000 - 3,85,000
    for basis in ("HISTORICAL", "FORECAST"):
        for name, (cash, dscr, post) in expected.items():
            sc = scen(v, basis, name)
            assert (sc["cash_available"], sc["dscr"], sc["post_debt_service_cash_flow"]) == (cash, dscr, post), sc
            assert sc["total_debt_service"] == "150000.00"
    assert "reduced by 20%" in scen(v, "HISTORICAL", "REVENUE_DOWN")["assumption"]
    assert "increased by 10%" in scen(v, "HISTORICAL", "EXPENSE_UP")["assumption"]

    rows = v["by_period"]
    assert [r["period"] for r in rows] == FY_MONTHS + ["2025-04", "2025-05", "2025-06"]
    assert rows[0]["post_debt_service_cash_flow"] == "0.00" and rows[-1]["kind"] == "FORECAST"
    # descriptive outcomes only - no decision or risk labels anywhere in the structured results
    assert v["outcome"] in {"ADEQUATE_DATA", "LIMITED_DATA", "LOW_CAPACITY", "NEGATIVE_CAPACITY", "CONFLICTING_DATA"}
    structured = str([v["outcome"], v["status"], v["outcome_reasons"], v["stress_scenarios"], v["by_period"],
                      v["cash_flow_capacity"]]).upper()
    assert not any(w in structured for w in ("APPROVE", "REJECT", "LOW_RISK", "HIGH_RISK"))


def test_stress_overrides_from_terms(client):
    s = RSeed(client)
    months(s)
    v = s.build({**ZERO_RATE_12L, "revenue_down_pct": 0.1, "expense_up_pct": 0.2})
    assert scen(v, "HISTORICAL", "REVENUE_DOWN")["cash_available"] == "100000.00"  # 4,50,000 - 3,50,000
    assert scen(v, "HISTORICAL", "EXPENSE_UP")["cash_available"] == "80000.00"  # 5,00,000 - 4,20,000


def test_quarterly_proposed_debt(client):
    s = RSeed(client)
    months(s)
    v = s.build({"requested_amount": "400000", "annual_interest_rate": "8", "tenure_months": 12,
                 "repayment_frequency": "QUARTERLY"})
    assert cap(v, "HISTORICAL", "proposed_debt_service")["value"] == "35016.50"  # 1,05,049.50 / 3
    assert cap(v, "HISTORICAL", "total_debt_service")["value"] == "85016.50"
    assert cap(v, "HISTORICAL", "dscr")["value"] == "1.764363"  # 1,50,000 / 85,016.50
    assert v["outcome"] == "ADEQUATE_DATA"


def test_adequate_and_negative_capacity(client):
    s = RSeed(client)
    months(s, inflow=600000)
    v = s.build()
    assert cap(v, "HISTORICAL", "dscr")["value"] == "1.666667"  # 2,50,000 / 1,50,000
    assert v["outcome"] == "ADEQUATE_DATA" and v["status"] == "AVAILABLE"
    s2 = RSeed(client)
    months(s2, inflow=400000)
    v2 = s2.build()
    assert cap(v2, "HISTORICAL", "post_debt_service_cash_flow")["value"] == "-100000.00"  # 50,000 - 1,50,000
    assert v2["outcome"] == "NEGATIVE_CAPACITY"


def test_no_existing_debt_gives_no_existing_dscr(client):
    s = RSeed(client)
    months(s, debt=0)
    v = s.build()
    assert cap(v, "HISTORICAL", "existing_debt_service")["value"] == "0.00"  # observed: no debt debits
    e = cap(v, "HISTORICAL", "existing_dscr")
    assert e["value"] is None and "division by zero" in e["reason"]
    assert cap(v, "HISTORICAL", "dscr")["value"] == "1.500000"  # 1,50,000 / 1,00,000


# =========================================================================== data quality
def test_insufficient_history(client):
    s = RSeed(client)
    months(s, keys=FY_MONTHS[:5])
    v = s.build()
    assert v["status"] == "NOT_AVAILABLE" and v["outcome"] == "LIMITED_DATA"
    assert check(v, "historical_cash_flow_coverage")["result"] == "FAIL"
    for m in ("cash_available_for_debt_service", "dscr", "post_debt_service_cash_flow"):
        assert cap(v, "HISTORICAL", m)["value"] is None  # never zero-filled
    assert scen(v, "HISTORICAL", "BASE")["status"] == "NOT_AVAILABLE"
    assert cap(v, "FORECAST", "dscr")["value"] is None  # forecasts need 6 months too
    assert check(v, "forecast_confidence")["result"] == "WARN"


def test_partial_cash_flow(client):
    s = RSeed(client)
    months(s, inflow=600000, keys=FY_MONTHS[:11])
    s.month(FY_MONTHS[11], inflow=100000, outflow=20000, availability=A.PARTIAL)
    v = s.build()
    d = cap(v, "HISTORICAL", "dscr")
    assert d["status"] == "PARTIAL" and d["value"] == "1.666667"  # 11 complete months; partial excluded
    assert d["period"] == "2024-04..2025-02"
    assert check(v, "partial_months")["months"] == ["2025-03"]
    assert next(r for r in v["by_period"] if r["period"] == "2025-03")["status"] == "PARTIAL"
    assert v["outcome"] == "LIMITED_DATA"


def test_unknown_transaction_share(client):
    s = RSeed(client)
    months(s, inflow=600000, unknown_inflow=300000)  # 3,00,000 of 9,00,000 credits UNKNOWN = 33.3%
    v = s.build()
    assert check(v, "unknown_credit_share")["result"] == "WARN" and check(v, "unknown_credit_share")["value"] == 0.3333
    c = cap(v, "HISTORICAL", "cash_available_for_debt_service")
    assert c["value"] == "250000.00" and c["status"] == "LOW_CONFIDENCE"  # UNKNOWN credits never counted
    assert v["outcome"] == "LIMITED_DATA"


def test_conflicting_statement_facts(client):
    s = RSeed(client)
    months(s, inflow=600000)
    a = s.fact(2024, "pat", 900000, doc="PL-A")
    b = s.fact(2024, "pat", 700000, doc="PL-B")
    s.fact(2024, "depreciation", 100000, doc="PL-A")
    s.fact(2024, "interest_expense", 50000, doc="PL-A")
    s.conflict(a, b, metric="pat")
    v = s.build()
    st = cap(v, "STATEMENT", "statement_dscr")
    assert st["status"] == "CONFLICTING" and st["value"] is None and st["provenance"]["conflicts"]
    assert check(v, "conflicting_facts")["result"] == "WARN"
    assert v["outcome"] == "CONFLICTING_DATA"


def test_statement_dscr(client):
    s = RSeed(client)
    months(s)
    for metric, value in (("pat", 900000), ("depreciation", 100000), ("interest_expense", 50000)):
        s.fact(2024, metric, value, doc="PL-1", page=2)
    v = s.build()
    assert cap(v, "STATEMENT", "annual_cash_accrual")["value"] == "1000000.00"  # 9L + 1L
    st = cap(v, "STATEMENT", "statement_dscr")
    assert st["value"] == "0.583333"  # 10,50,000 / (12 x 1,50,000)
    assert st["provenance"]["fact_ids"] and st["evidence"][0]["document_code"] == "PL-1"
    assert st["evidence"][0]["pages"] == [2]


# =========================================================================== existing debt + provenance (end to end)
def _statement_with_emi(business) -> bytes:
    spec = S.default_bank_spec(business)
    extra = []
    for y, m in [(2024, mm) for mm in range(4, 13)] + [(2025, mm) for mm in range(1, 4)]:
        extra.append(S.Txn(date(y, m, 5), "ACH DR HDFC BANK LOAN EMI", f"E{y}{m:02d}", D("38250.00"), None))
        extra.append(S.Txn(date(y, m, 7), "RENT PAYMENT KOVAI ESTATES", f"T{y}{m:02d}", D("25000.00"), None))
    txns = sorted(spec.txns + extra, key=lambda t: t.date)
    bal = spec.opening
    for t in txns:
        bal = bal + (t.credit or 0) - (t.debit or 0)
        t.balance = bal
    spec.txns = txns
    return S.bank_statement_pdf(spec)


def test_existing_debt_from_classified_emi_with_provenance(client, business):
    app = create_app(client)
    upload(client, app["id"], {"stmt.pdf": _statement_with_emi(business)})
    url = f"/api/applications/{app['id']}/repayment-capacity"
    v = client.post(url, json=ZERO_RATE_12L).json()
    ex = cap(v, "HISTORICAL", "existing_debt_service")
    assert ex["value"] == "38250.00"  # one classified EMI of 38,250 per month
    evidence = v["existing_debt_service"]["evidence"]
    assert len(evidence["transactions"]) == 12 and {t["category"] for t in evidence["transactions"]} == {"LOAN_REPAYMENT"}
    assert any(p["type"] == "EMI" for p in evidence["recurring_debt_patterns"])
    # the recurring rent debit is NOT treated as a loan EMI
    rent = [t for t in client.get(f"/api/applications/{app['id']}/transactions/classified").json()
            if "RENT PAYMENT" in (t["description"] or "")]
    assert rent and all(t["transaction_id"] not in {e["transaction_id"] for e in evidence["transactions"]} for t in rent)

    # provenance: metric -> monthly aggregates -> transactions -> statement row -> page / bbox
    assert ex["provenance"]["chain"].startswith("repayment metric -> monthly cash-flow aggregates")
    tid = evidence["transactions"][0]["transaction_id"]
    assert tid in ex["provenance"]["transaction_ids"]
    t = client.get(f"/api/transactions/{tid}/classification").json()
    assert t["provenance"]["statement_row"]["page_number"] and t["provenance"]["statement_row"]["bbox"]
    assert ex["evidence"][0]["document_type"] == "BANK_STATEMENT" and ex["evidence"][0]["pages"]
    # loan terms identified separately as user-provided inputs
    prop = cap(v, "HISTORICAL", "proposed_debt_service")
    assert prop["provenance"]["user_provided_inputs"][0]["source"] == "USER_PROVIDED"
    assert prop["provenance"]["user_provided_inputs"][0]["provided_by"] == "officer-7"
    f = cap(v, "FORECAST", "cash_available_for_debt_service")
    assert f["provenance"]["forecast_result_ids"] and "forecast results" in f["provenance"]["chain"]


def test_sample_application_rebuild_is_idempotent(client, business):
    app = create_app(client)
    upload(client, app["id"], sample_documents(business))
    url = f"/api/applications/{app['id']}/repayment-capacity"
    assert client.get(url).json()["outcome"] == "LIMITED_DATA"  # pipeline ran, no terms yet
    first = client.post(url, json={"requested_amount": "2500000", "annual_interest_rate": "11.5",
                                   "tenure_months": 60, "repayment_frequency": "MONTHLY"}).json()
    # borrowings on the balance sheet but no repayments in the statement -> existing debt LOW_CONFIDENCE
    assert check(first, "existing_debt_evidence")["result"] == "WARN"
    assert cap(first, "HISTORICAL", "existing_debt_service")["status"] == "LOW_CONFIDENCE"
    assert first["health_context"] and all("not used" in h["note"] for h in first["health_context"])

    def strip(v):
        return (v["status"], v["outcome"], v["repayment"]["installment"],
                sorted((m["basis"], m["metric"], m["value"], m["status"])
                       for ms in v["cash_flow_capacity"].values() for m in ms),
                [(x["basis"], x["scenario"], x["dscr"]) for x in v["stress_scenarios"]])

    again = client.post(f"{url}/rebuild").json()
    assert strip(first) == strip(again) and first["analysis_id"] != again["analysis_id"]
    audit = client.get(f"/api/applications/{app['id']}/audit").json()
    assert any(a["action"] == "LOAN_TERMS_PROVIDED" for a in (audit if isinstance(audit, list) else audit["items"]))
