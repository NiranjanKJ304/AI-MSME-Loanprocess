"""Transaction intelligence: classification, business nature, counterparties, recurring patterns,
reversals/refunds, internal transfers, monthly cash flow, coverage, partial data, provenance, rules."""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.enums import (
    BusinessNature,
    ClassificationStatus,
    CounterpartyType,
    FactAvailability,
    RecurrenceFrequency,
    TxnClass,
)
from app.transaction_intel.aggregation import StatementInfo, aggregate, cashflow_metrics
from app.transaction_intel.config import DEFAULT_RULES_PATH, RuleSet, get_rules, load_rules, set_rules
from app.transaction_intel.engine import Engine, EntityContext, TxnInput
from scripts import synthetic_docs as S
from tests.conftest import create_app, upload

OWN = "SRI LAKSHMI PRECISION COMPONENTS PRIVATE LIMITED"
A = FactAvailability


def T(i, narration, debit=None, credit=None, d=None, doc="docA", acct="50200031457788"):
    return TxnInput(id=str(i), document_id=doc, account_number=acct, date=d or date(2024, 4, 1) + timedelta(days=i % 28),
                    description=narration, reference=None,
                    debit=Decimal(str(debit)) if debit is not None else None,
                    credit=Decimal(str(credit)) if credit is not None else None,
                    direction="CREDIT" if credit is not None else "DEBIT", sequence=i)


def run(txns, own=(OWN,), related=(), accounts=None):
    ctx = EntityContext(own_names=list(own), related_names=list(related),
                        account_numbers=accounts or {t.document_id: t.account_number for t in txns})
    return Engine(get_rules()).run(txns, ctx)


def one(narration, debit=None, credit=None, **kw):
    t = T(1, narration, debit, credit)
    return run([t], **kw).classifications["1"]


# =========================================================================== categories
def test_business_revenue_vs_own_account_transfer():
    rev = one("NEFT CR SHAKTHI PUMPS LTD", credit=172445)
    assert rev.category == TxnClass.BUSINESS_REVENUE and rev.nature == BusinessNature.BUSINESS
    assert rev.status == ClassificationStatus.LOW_CONFIDENCE  # one business-entity credit: "likely", not certain
    assert rev.narration.normalized_counterparty == "SHAKTHI PUMPS LIMITED"
    assert rev.counterparty_type == CounterpartyType.BUSINESS_ENTITY

    own_kw = one("NEFT CR OWN ACCOUNT TRF", credit=200000)
    assert own_kw.category == TxnClass.OWN_ACCOUNT_TRANSFER and own_kw.nature == BusinessNature.TRANSFER
    own_name = one("IMPS CR SRI LAKSHMI PRECISION COMPONENTS", credit=150000)
    assert own_name.category == TxnClass.OWN_ACCOUNT_TRANSFER
    assert own_name.counterparty_type == CounterpartyType.OWN_ENTITY
    assert any(e["signal"] == "own_entity_counterparty" for e in own_name.evidence)


def test_loan_disbursement_is_financing_not_revenue():
    c = one("LOAN DISBURSEMENT TL 7788", credit=2500000)
    assert c.category == TxnClass.LOAN_DISBURSEMENT and c.nature == BusinessNature.FINANCING
    assert c.status == ClassificationStatus.CLASSIFIED


def test_emi_and_interest_payment():
    emi = one("ACH DR HDFC BANK LOAN EMI", debit=38250)
    assert emi.category == TxnClass.LOAN_REPAYMENT and emi.nature == BusinessNature.FINANCING
    assert emi.narration.normalized_counterparty == "HDFC BANK"
    assert emi.counterparty_type == CounterpartyType.BANK_OR_LENDER
    assert one("OD INT DEBITED MAR-25", debit=18250).category == TxnClass.INTEREST_PAYMENT


def test_supplier_payment():
    weak = one("RTGS DR KOVAI METALS PVT LTD", debit=285750)
    assert weak.category == TxnClass.SUPPLIER_PAYMENT and weak.status == ClassificationStatus.LOW_CONFIDENCE
    strong = one("NEFT DR KOVAI METALS PURCHASE INV 22", debit=150000)
    assert strong.category == TxnClass.SUPPLIER_PAYMENT and strong.status == ClassificationStatus.CLASSIFIED
    assert strong.narration.normalized_counterparty == "KOVAI METALS"


@pytest.mark.parametrize("narration,debit,credit,category,nature,counterparty", [
    ("SALARY BATCH APR-24", 310000, None, TxnClass.SALARY, BusinessNature.BUSINESS, None),
    ("RENT APR 2024 TO R KUMAR", 45000, None, TxnClass.RENT, BusinessNature.BUSINESS, "R KUMAR"),
    ("UPI DR ELECTRICITY BOARD", 8450, None, TxnClass.UTILITIES, BusinessNature.BUSINESS, "ELECTRICITY BOARD"),
    ("BESCOM ELECTRICITY BILL APR", 9000, None, TxnClass.UTILITIES, BusinessNature.BUSINESS, "BESCOM"),
    ("GST PAYMENT CPIN 2405", 120500, None, TxnClass.TAX_PAYMENT, BusinessNature.BUSINESS, None),
    ("CASH DEPOSIT BY SELF", None, 50000, TxnClass.CASH_DEPOSIT, BusinessNature.UNKNOWN, None),
    ("ATM WDL 4471 CBE", 20000, None, TxnClass.CASH_WITHDRAWAL, BusinessNature.UNKNOWN, None),
    ("SMS ALERT CHARGES Q1", 59, None, TxnClass.BANK_CHARGES, BusinessNature.BUSINESS, None),
    ("LIC PREMIUM POLICY 1234", 12000, None, TxnClass.INSURANCE, BusinessNature.UNKNOWN, "LIC"),
    ("INT PD 01-04-24 TO 30-06-24", None, 2311, TxnClass.INTEREST_INCOME, BusinessNature.BUSINESS, None),
    ("MF PURCHASE SIP GROWW", 10000, None, TxnClass.INVESTMENT, BusinessNature.UNKNOWN, "GROWW"),
])
def test_expense_cash_and_other_categories(narration, debit, credit, category, nature, counterparty):
    c = one(narration, debit=debit, credit=credit)
    assert (c.category, c.nature) == (category, nature)
    assert c.narration.normalized_counterparty == counterparty


def test_direction_is_respected_and_amount_limits_apply():
    # "CHARGES" on a 2 lakh debit is not a bank charge (max_amount 50,000); without other evidence -> UNKNOWN
    big = one("CHARGES RECOVERY 778", debit=200000)
    assert big.category != TxnClass.BANK_CHARGES
    # a salary keyword on a CREDIT cannot make it a salary EXPENSE
    assert one("SALARY CREDIT FROM EMPLOYER", credit=50000).category != TxnClass.SALARY


def test_unknown_and_personal_are_not_forced():
    u = one("UPI CR 98765@ybl", credit=1200)
    assert u.category == TxnClass.UNKNOWN and u.status == ClassificationStatus.UNKNOWN
    assert u.nature == BusinessNature.UNKNOWN
    vague = one("TRANSFER 778899", debit=90000)
    assert vague.status == ClassificationStatus.UNKNOWN  # weak transfer hint only - stays unknown
    p = one("UPI DR SWIGGY ORDER 4421", debit=640)
    assert p.nature == BusinessNature.PERSONAL and p.status == ClassificationStatus.UNKNOWN


# =========================================================================== reversals / refunds
def test_reversal_pair_is_linked_and_excluded():
    txns = [T(1, "NEFT DR TEXMO IND PAYMENT", debit=99000, d=date(2024, 4, 3)),
            T(2, "NEFT RETURN TEXMO IND REV", credit=99000, d=date(2024, 4, 5))]
    res = run(txns).classifications
    rev, orig = res["2"], res["1"]
    assert rev.category == TxnClass.REVERSAL and rev.linked_transaction_id == "1" and rev.link_type == "REVERSAL_OF"
    assert orig.link_type == "REVERSED_BY" and orig.linked_transaction_id == "2"
    assert rev.excluded_from_aggregates and orig.excluded_from_aggregates
    assert orig.category != TxnClass.REVERSAL  # the original keeps its own classification


def test_refund_paired_and_unpaired_never_revenue():
    txns = [T(1, "POS DR AMAZON PURCHASE", debit=1499, d=date(2024, 4, 2)),
            T(2, "REFUND AMAZON ORDER 123", credit=1499, d=date(2024, 4, 6)),
            T(3, "REFUND FROM FLIPKART", credit=899, d=date(2024, 4, 9))]
    res = run(txns).classifications
    assert res["2"].category == TxnClass.REFUND and res["2"].link_type == "REFUND_OF"
    assert res["1"].excluded_from_aggregates and res["2"].excluded_from_aggregates
    assert res["3"].category == TxnClass.REFUND and not res["3"].excluded_from_aggregates
    assert all(c.category != TxnClass.BUSINESS_REVENUE for c in res.values())


# =========================================================================== internal transfers
def test_internal_transfer_between_own_accounts():
    a, b = "50200031457788", "00451020007788"
    txns = [T(1, "NEFT DR TO A/C XXXX7788", debit=90000, d=date(2024, 5, 10), doc="docA", acct=a),
            T(2, "NEFT CR SRI LAKSHMI PRECI", credit=90000, d=date(2024, 5, 11), doc="docB", acct=b),
            T(3, "SALARY BATCH MAY", debit=90000, d=date(2024, 5, 10), doc="docA", acct=a)]
    res = run(txns, accounts={"docA": a, "docB": b}).classifications
    assert res["1"].category == TxnClass.INTERNAL_TRANSFER and res["1"].link_type == "TRANSFER_PAIR"
    assert res["2"].category in (TxnClass.INTERNAL_TRANSFER, TxnClass.OWN_ACCOUNT_TRANSFER)
    assert res["2"].nature == BusinessNature.TRANSFER and res["2"].linked_transaction_id == "1"
    assert {e["signal"] for e in res["1"].evidence} >= {"other_account_number_mentioned",
                                                         "matching_opposite_entry_other_account"}
    assert res["3"].category == TxnClass.SALARY  # same amount, but strong salary keyword vetoes pairing


def test_single_account_transfer_without_evidence_stays_unknown():
    res = run([T(1, "IMPS CR 412345 RAMESH K", credit=25000)]).classifications
    assert res["1"].status == ClassificationStatus.UNKNOWN
    assert res["1"].narration.normalized_counterparty == "RAMESH K"
    assert res["1"].counterparty_type == CounterpartyType.INDIVIDUAL


def test_related_party_transfer():
    res = run([T(1, "IMPS CR LAKSHMI RAMAN", credit=300000)], related=("LAKSHMI RAMAN",)).classifications
    assert res["1"].category == TxnClass.RELATED_ACCOUNT_TRANSFER and res["1"].nature == BusinessNature.TRANSFER


# =========================================================================== recurring
def _monthly(i0, narration, amounts, debit=True, day=5):
    out = []
    for k, amt in enumerate(amounts):
        d = date(2024, 4 + k, day)
        out.append(T(i0 + k, narration, debit=amt if debit else None, credit=None if debit else amt, d=d))
    return out


def test_recurring_salary_rent_emi_and_customer_receipts():
    txns = (_monthly(10, "SALARY BATCH", [310000] * 4)
            + _monthly(20, "RENT TO R KUMAR", [45000] * 4, day=3)
            + _monthly(30, "ACH DR HDFC BANK LOAN EMI", [38250] * 4, day=7)
            + _monthly(40, "NEFT CR SHAKTHI PUMPS LTD", [172445, 136328, 177931, 158200], debit=False, day=12))
    res = run(txns)
    by_type = {p.pattern_type: p for p in res.patterns}
    sal = by_type["SALARY"]
    assert sal.frequency == RecurrenceFrequency.MONTHLY and sal.occurrences == 4
    assert sal.average_amount == Decimal("310000.0") and sal.amount_cv == 0.0
    assert (sal.first_seen, sal.last_seen) == (date(2024, 4, 5), date(2024, 7, 5))
    assert sal.confidence >= 0.8
    assert by_type["RENT"].counterparty == "R KUMAR"
    assert by_type["EMI"].frequency == RecurrenceFrequency.MONTHLY
    cust = by_type["CUSTOMER_RECEIPTS"]
    assert cust.counterparty == "SHAKTHI PUMPS LIMITED" and cust.amount_cv > 0
    # a recurring customer turns "likely" revenue into confidently classified revenue
    c = res.classifications["40"]
    assert c.category == TxnClass.BUSINESS_REVENUE and c.status == ClassificationStatus.CLASSIFIED
    assert any(e["signal"] == "recurring_payer" for e in c.evidence)


def test_too_few_occurrences_is_not_recurring():
    res = run(_monthly(1, "RENT TO R KUMAR", [45000, 45000]))
    assert res.patterns == []


# =========================================================================== monthly aggregation (pure)
def _statement(txns, start=date(2024, 4, 1), end=date(2024, 5, 31), failed=0, doc="docA"):
    return StatementInfo(doc, "50200031457788", start, end, failed)


def test_monthly_aggregation_buckets_coverage_and_provenance():
    txns = [
        T(1, "NEFT CR SHAKTHI PUMPS LTD INV 12", credit=100000, d=date(2024, 4, 3)),
        T(2, "NEFT CR OWN ACCOUNT TRF", credit=200000, d=date(2024, 4, 4)),
        T(3, "LOAN DISBURSEMENT TL 7788", credit=500000, d=date(2024, 4, 5)),
        T(4, "NEFT DR KOVAI METALS PURCHASE", debit=60000, d=date(2024, 4, 6)),
        T(5, "SALARY BATCH APR", debit=30000, d=date(2024, 4, 28)),
        T(6, "ACH DR HDFC BANK LOAN EMI", debit=38250, d=date(2024, 4, 7)),
        T(7, "CASH DEPOSIT BY SELF", credit=20000, d=date(2024, 4, 8)),
        T(8, "UPI CR 98765@ybl", credit=1200, d=date(2024, 4, 9)),
        T(9, "NEFT DR TEXMO IND", debit=9000, d=date(2024, 4, 10)),
        T(10, "NEFT RETURN TEXMO IND", credit=9000, d=date(2024, 4, 12)),
        T(11, "NEFT CR SHAKTHI PUMPS LTD INV 19", credit=80000, d=date(2024, 5, 3)),
    ]
    res = run(txns)
    months = aggregate(txns, res.classifications, [_statement(txns)], 0.8)
    apr = next(m for m in months if m.scope_document_id is None and m.month == "2024-04")
    v = apr.values
    assert v["business_inflow"] == Decimal("100000") == v["business_revenue_inflow"]
    assert v["transfer_inflow"] == Decimal("200000") and v["financing_inflow"] == Decimal("500000")
    assert v["business_outflow"] == Decimal("90000")  # supplier 60,000 + salary 30,000
    assert v["financing_outflow"] == Decimal("38250") and v["cash_deposits"] == Decimal("20000")
    assert v["unknown_inflow"] == Decimal("1200")  # reported, not treated as zero
    assert v["excluded_inflow"] == Decimal("9000") and v["excluded_outflow"] == Decimal("9000")
    assert v["total_inflow"] == Decimal("830200") and v["total_outflow"] == Decimal("137250")
    assert v["net_operating_cash_flow"] == Decimal("10000")
    assert apr.counts["txn_count"] == 10 and apr.counts["unknown_txn_count"] == 1
    assert apr.counts["excluded_txn_count"] == 2
    assert apr.coverage_count == 0.9 and apr.coverage_amount == round(1 - 1200 / 967450, 4)
    assert apr.availability == A.AVAILABLE and apr.partial_reasons == []
    assert apr.provenance["transaction_ids"]["unknown_inflow"] == ["8"]
    assert set(apr.provenance["transaction_ids"]["excluded_inflow"]) == {"10"}
    # per-statement rows exist as well as the combined row
    assert any(m.scope_document_id == "docA" and m.month == "2024-04" for m in months)


def test_partial_statement_months_and_metrics():
    txns = [T(i, "NEFT CR SHAKTHI PUMPS LTD INV", credit=100000 + i, d=date(2024, 4 + i, 10)) for i in range(4)]
    res = run(txns)
    # statement starts mid-April and has one unparsed row
    st = _statement(txns, start=date(2024, 4, 15), end=date(2024, 7, 31), failed=1)
    months = aggregate(txns, res.classifications, [st], 0.8)
    allm = [m for m in months if m.scope_document_id is None]
    assert all(m.availability == A.PARTIAL for m in allm)  # the unparsed row could belong to any month
    assert any("covers 16 of 30 days" in r for r in allm[0].partial_reasons)
    metrics = {m.metric: m for m in cashflow_metrics(months, txns, res.classifications, [st])}
    avg = metrics["average_monthly_business_inflow"]
    assert avg.value is None and avg.availability == A.NOT_AVAILABLE  # no complete month: no fake average
    series = metrics["monthly_business_inflow"]
    assert series.availability == A.PARTIAL and len(series.details["series"]) == 4
    assert series.details["series"][1]["value"] == "100001.00"  # observed values kept


def test_cashflow_metrics_on_complete_months():
    amounts = [100000, 110000, 90000]
    txns = [T(i, "NEFT CR SHAKTHI PUMPS LTD INV", credit=a, d=date(2024, 4 + i, 10)) for i, a in enumerate(amounts)]
    txns += [T(10 + i, "NEFT DR KOVAI METALS PURCHASE", debit=50000, d=date(2024, 4 + i, 15)) for i in range(3)]
    txns.append(T(20, "UPI CR 98765@ybl", credit=10000, d=date(2024, 5, 20)))
    res = run(txns)
    st = _statement(txns, end=date(2024, 6, 30))
    months = aggregate(txns, res.classifications, [st], 0.8)
    m = {x.metric: x for x in cashflow_metrics(months, txns, res.classifications, [st])}
    assert m["average_monthly_business_inflow"].value == Decimal("100000.00")
    assert m["average_monthly_business_outflow"].value == Decimal("50000.00")
    assert m["outflow_consistency"].value == Decimal("1.0")
    assert Decimal("0.9") < m["inflow_consistency"].value < Decimal("1")
    assert m["business_transaction_count"].value == 6 and m["unknown_transaction_count"].value == 1
    assert m["classification_coverage"].value == Decimal(str(round(1 - 10000 / 460000, 4)))
    assert m["classification_coverage"].details["by_count"] == round(6 / 7, 4)


# =========================================================================== configuration
def test_rules_are_configurable(tmp_path):
    data = json.loads(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))
    data["version"] = "bank-x-2026"
    data["rules"].append({"id": "acme_lease", "category": "RENT", "direction": "DEBIT", "weight": 0.9,
                          "patterns": [r"\bACME LEASING\b"], "keyword_is_counterparty": True})
    custom = RuleSet.model_validate(data)
    set_rules(custom)
    try:
        c = one("NEFT DR ACME LEASING 0425", debit=60000)
        assert c.category == TxnClass.RENT and c.narration.normalized_counterparty == "ACME LEASING"
    finally:
        set_rules(None)
    bad = json.loads(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))
    bad["rules"].append({"id": "x", "category": "SALARY", "direction": "CREDIT", "weight": 0.5, "patterns": ["SAL"]})
    with pytest.raises(ValueError, match="contradicts"):
        RuleSet.model_validate(bad)
    bad["rules"][-1] = {"id": "y", "category": "RENT", "weight": 0.5, "patterns": ["(unclosed"]}
    with pytest.raises(Exception):
        RuleSet.model_validate(bad)


def test_default_rules_file_is_clean():
    raw = DEFAULT_RULES_PATH.read_text(encoding="utf-8")
    assert not any(ord(ch) < 32 and ch != "\n" for ch in raw), "control characters in rule patterns"
    rules = load_rules()
    assert set(rules.categories) == set(TxnClass)
    assert rules.version


# =========================================================================== end-to-end via the API
def _api_statement(business, failed_row=False):
    start, end = date(2024, 4, 1), date(2024, 6, 30)
    plan = []
    for k in range(3):
        m = 4 + k
        plan += [
            (date(2024, m, 3), "NEFT CR SHAKTHI PUMPS LTD", None, Decimal(150000 + 10000 * k)),
            (date(2024, m, 5), "SALARY BATCH", Decimal("120000"), None),
            (date(2024, m, 6), "RENT TO R KUMAR", Decimal("45000"), None),
            (date(2024, m, 7), "ACH DR HDFC BANK LOAN EMI", Decimal("38250"), None),
            (date(2024, m, 9), "UPI DR ELECTRICITY BOARD", Decimal("8450"), None),
        ]
    plan += [
        (date(2024, 4, 12), "NEFT CR OWN ACCOUNT TRF", None, Decimal("200000")),
        (date(2024, 5, 14), "LOAN DISBURSEMENT TL 7788", None, Decimal("500000")),
        (date(2024, 5, 16), "NEFT DR KOVAI METALS PVT LTD", Decimal("150000"), None),
        (date(2024, 6, 18), "CASH DEPOSIT BY SELF", None, Decimal("30000")),
        (date(2024, 6, 20), "ATM WDL 4471", Decimal("10000"), None),
        (date(2024, 6, 22), "SMS ALERT CHARGES", Decimal("59"), None),
        (date(2024, 6, 24), "UPI CR 98765@ybl", None, Decimal("1200")),
    ]
    plan.sort(key=lambda p: p[0])
    opening, bal, txns = Decimal("455000"), Decimal("455000"), []
    for i, (d, narr, dr, cr) in enumerate(plan):
        bal = bal + (cr or 0) - (dr or 0)
        txns.append(S.Txn(d, narr, f"N{100000 + i}", dr, cr, bal))
    if failed_row:
        txns.insert(5, S.Txn(txns[5].date, "", "", None, None, None, date_text="##/##/####"))
    spec = S.BankStatementSpec("SRI LAKSHMI PRECISION COMPONENTS PVT LTD", business.account_number, business.ifsc,
                               business.bank_name, start, end, opening, txns)
    return S.bank_statement_pdf(spec), plan


def test_api_end_to_end_bank_credit_is_not_business_revenue(client, db, business):
    data, plan = _api_statement(business)
    app = create_app(client)
    doc = upload(client, app["id"], {"stmt.pdf": data})["documents"][0]
    stages = {s["stage"]: s["status"] for s in client.get(f"/api/documents/{doc['id']}/status").json()["pipeline"]}
    assert stages["TRANSACTION_INTELLIGENCE"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS")

    txns = client.get(f"/api/applications/{app['id']}/transactions/classified").json()
    assert len(txns) == len(plan)
    cat = {t["description"]: t["classification"]["category"] for t in txns}
    assert cat["NEFT CR OWN ACCOUNT TRF"] == "OWN_ACCOUNT_TRANSFER"
    assert cat["LOAN DISBURSEMENT TL 7788"] == "LOAN_DISBURSEMENT"
    assert cat["NEFT CR SHAKTHI PUMPS LTD"] == "BUSINESS_REVENUE"
    assert cat["UPI CR 98765@ybl"] == "UNKNOWN"

    months = client.get(f"/api/applications/{app['id']}/cashflow/monthly").json()
    assert [m["month"] for m in months] == ["2024-04", "2024-05", "2024-06"]
    assert all(m["availability"] == "AVAILABLE" for m in months)
    apr, may = months[0]["values"], months[1]["values"]
    # REGRESSION: bank credit != business revenue; own-account transfer != business revenue
    assert Decimal(apr["total_inflow"]) == Decimal("350000.00")
    assert Decimal(apr["business_inflow"]) == Decimal("150000.00")
    assert Decimal(apr["transfer_inflow"]) == Decimal("200000.00")
    assert Decimal(may["financing_inflow"]) == Decimal("500000.00")
    assert Decimal(may["business_inflow"]) == Decimal("160000.00")
    assert Decimal(months[2]["values"]["unknown_inflow"]) == Decimal("1200.00")
    # the financial layer never turns bank credits into revenue either
    assert client.get(f"/api/applications/{app['id']}/financials/facts", params={"metric": "revenue"}).json() == []

    metrics = {m["metric"]: m for m in client.get(f"/api/applications/{app['id']}/cashflow/metrics").json()}
    assert Decimal(metrics["average_monthly_business_inflow"]["value"]) == Decimal("160000")
    assert metrics["average_monthly_business_inflow"]["availability"] == "AVAILABLE"
    assert metrics["unknown_transaction_count"]["value"] == "1.0000"
    assert len(metrics["monthly_business_inflow"]["provenance"]["monthly_aggregate_ids"]) == 3

    patterns = {p["pattern_type"]: p for p in client.get(f"/api/applications/{app['id']}/transactions/recurring").json()}
    assert {"SALARY", "RENT", "EMI", "CUSTOMER_RECEIPTS"} <= set(patterns)
    assert patterns["EMI"]["occurrences"] == 3 and patterns["EMI"]["frequency"] == "MONTHLY"

    summary = client.get(f"/api/applications/{app['id']}/transactions/intelligence").json()
    assert summary["transactions"] == len(plan) and summary["by_status"]["UNKNOWN"] == 1


def test_api_provenance_chain_and_idempotent_rebuild(client, db, business):
    data, plan = _api_statement(business)
    app = create_app(client)
    upload(client, app["id"], {"stmt.pdf": data})
    months = client.get(f"/api/applications/{app['id']}/cashflow/monthly").json()
    txn_id = months[0]["provenance"]["transaction_ids"]["business_inflow"][0]
    detail = client.get(f"/api/transactions/{txn_id}/classification").json()
    assert detail["classification"]["category"] == "BUSINESS_REVENUE"
    prov = detail["provenance"]
    assert prov["statement_row"]["bbox"] and prov["statement_row"]["page_number"] == 1
    assert prov["statement_row"]["raw_cells"][1] == "NEFT CR SHAKTHI PUMPS LTD"
    assert prov["document"]["filename"] == "stmt.pdf"
    assert prov["canonical_transaction"]["row_id"] == prov["statement_row"]["row_id"]

    first = client.get(f"/api/applications/{app['id']}/transactions/intelligence").json()
    r1 = client.post(f"/api/applications/{app['id']}/transactions/intelligence/rebuild").json()
    r2 = client.post(f"/api/applications/{app['id']}/financials/rebuild").json()["transaction_intelligence"]
    assert r1["transactions"] == r2["transactions"] == first["transactions"]
    assert client.get(f"/api/applications/{app['id']}/transactions/intelligence").json()["by_category"] == \
        first["by_category"]
    assert len(client.get(f"/api/applications/{app['id']}/cashflow/monthly").json()) == 3
    assert client.get(f"/api/transactions/{uuid.uuid4()}/classification").status_code == 404
    assert client.get("/api/transaction-rules").json()["version"]


def test_api_partially_parsed_statement_keeps_months_partial(client, business):
    data, plan = _api_statement(business, failed_row=True)
    app = create_app(client)
    upload(client, app["id"], {"stmt.pdf": data})
    months = client.get(f"/api/applications/{app['id']}/cashflow/monthly").json()
    assert months and all(m["availability"] == "PARTIAL" for m in months)
    assert any("could not be parsed" in r for r in months[0]["partial_reasons"])
    assert Decimal(months[0]["values"]["business_inflow"]) == Decimal("150000.00")  # observed value kept
    metrics = {m["metric"]: m for m in client.get(f"/api/applications/{app['id']}/cashflow/metrics").json()}
    assert metrics["average_monthly_business_inflow"]["value"] is None
    assert metrics["classification_coverage"]["availability"] == "PARTIAL"
