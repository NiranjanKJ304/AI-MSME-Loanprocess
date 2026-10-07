"""Canonical financial data layer: facts, periods, units, transactions, conflicts, provenance."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.financials.periods import (
    from_assessment_year,
    from_range,
    normalize_period_expression,
    parse_fiscal_year,
    parse_month,
)
from app.financials.transactions import classify
from app.models import BankTransaction, ExtractedField, ExtractedTableRow, FinancialFact
from app.models.enums import PeriodType, TxnCategory, TxnDirection
from scripts import synthetic_docs as S
from tests.conftest import create_app, sample_documents, upload
from tests.fixtures import layouts as L


def fin(client, app_id, what: str, **params):
    r = client.get(f"/api/applications/{app_id}/financials/{what}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def facts_by(client, app_id, metric: str) -> list[dict]:
    return fin(client, app_id, "facts", metric=metric)


def profile_cell(client, app_id, period_key: str, metric: str) -> dict:
    prof = fin(client, app_id, "profile")
    period = next(p for p in prof["periods"] if p["period_key"] == period_key)
    return period["metrics"][metric]


# =========================================================================== period normalisation
@pytest.mark.parametrize("expr", ["FY2024-25", "FY 2024-25", "2024-25", "2024-2025", "F.Y. 24-25", "2024/25"])
def test_equivalent_fiscal_year_expressions_normalise_to_one_period(expr):
    p = parse_fiscal_year(expr)
    assert p.key == "FY2024-25" and p.period_type == PeriodType.FY
    assert (p.start, p.end) == (date(2024, 4, 1), date(2025, 3, 31))


def test_date_range_and_assessment_year_map_to_the_same_fy():
    assert from_range(date(2024, 4, 1), date(2025, 3, 31)).key == "FY2024-25"
    assert normalize_period_expression("01-Apr-2024 to 31-Mar-2025").key == "FY2024-25"
    assert from_assessment_year("AY 2025-26").key == "FY2024-25"  # AY = FY + 1
    assert parse_fiscal_year("2023-24").key != parse_fiscal_year("2024-25").key  # never mixed


def test_month_quarter_custom_and_unreadable_periods():
    assert parse_month("March 2025").key == "2025-03"
    assert parse_month("03/2025").key == "2025-03"
    assert normalize_period_expression("01/03/2025 to 31/03/2025").key == "2025-03"
    q = from_range(date(2024, 4, 1), date(2024, 6, 30))
    assert (q.key, q.period_type) == ("FY2024-25-Q1", PeriodType.QUARTER)
    c = from_range(date(2024, 4, 1), date(2024, 9, 30))
    assert c.period_type == PeriodType.CUSTOM and c.fiscal_year == "2024-25"
    assert parse_fiscal_year("2024-26") is None  # not consecutive years: not a financial year
    assert normalize_period_expression("sometime last year") is None


# =========================================================================== end-to-end sample
def test_sample_application_canonical_facts(client, db, business):
    app = create_app(client)
    upload(client, app["id"], sample_documents(business))
    periods = fin(client, app["id"], "periods")
    assert [p["period_key"] for p in periods] == ["FY2024-25"]
    sources = {e["document_code"] for e in periods[0]["source_expressions"]}
    assert {"PL-007", "BS-008", "ITR-006", "GSTR-003", "BANK-009"} <= sources  # 5 different expressions, one period

    revenue = facts_by(client, app["id"], "revenue")
    assert len(revenue) == 1 and revenue[0]["value"] == "6820000.00" and revenue[0]["unit"] == "INR"
    assert revenue[0]["source_document_type"] == "PROFIT_LOSS" and revenue[0]["period_type"] == "FY"
    assert revenue[0]["raw_value"] == "68,20,000.00"  # raw preserved
    ta = facts_by(client, app["id"], "total_assets")[0]
    assert ta["measure_type"] == "STOCK" and ta["as_of_date"] == "2025-03-31"

    nw = facts_by(client, app["id"], "net_worth")[0]
    assert nw["source_kind"] == "DERIVED" and nw["value"] == "3145000.00"  # 10,00,000 + 21,45,000
    assert [c["component"] for c in nw["derivation"]["components"]] == ["capital", "reserves"]
    tl = facts_by(client, app["id"], "total_liabilities")[0]
    assert tl["value"] == "2955000.00"  # "Total equity and liabilities" 61,00,000 - net worth

    # profile states: things that are not printed are NOT_AVAILABLE, never 0
    ca = profile_cell(client, app["id"], "FY2024-25", "current_assets")
    assert ca["status"] == "NOT_AVAILABLE" and ca["value"] is None and ca["reason"]
    rv = profile_cell(client, app["id"], "FY2024-25", "revenue")
    assert rv["status"] == "AVAILABLE" and rv["value"] == "6820000.00"

    stages = {s["stage"]: s["status"] for s in client.get(f"/api/documents/{revenue[0]['source_document_id']}/status")
              .json()["pipeline"]}
    assert stages["FINANCIAL_NORMALIZATION"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS")


def test_bank_credits_are_never_revenue(client, business):
    app = create_app(client)
    upload(client, app["id"], {"stmt.pdf": S.bank_statement_pdf(S.default_bank_spec(business))})
    # a bank statement alone produces banking facts only
    assert facts_by(client, app["id"], "revenue") == [] and facts_by(client, app["id"], "turnover") == []
    credits = facts_by(client, app["id"], "total_credits")
    assert len(credits) == 1 and credits[0]["category"] == "BANKING"
    assert any("not revenue" in n for n in credits[0]["notes"])
    cell = profile_cell(client, app["id"], "FY2024-25", "revenue")
    assert cell["status"] == "NOT_AVAILABLE" and cell["value"] is None
    assert fin(client, app["id"], "conflicts") == []  # bank credits are never compared with revenue


# =========================================================================== multiple sources / conflicts
def test_itr_and_gst_turnover_both_kept_and_conflict_recorded(client, business):
    app = create_app(client)
    upload(client, app["id"], {"itr.pdf": S.itr_pdf(business, turnover=Decimal("5000000")),
                               "gstr9.pdf": S.gst_return_pdf(business, taxable=Decimal("4700000"))})
    itr = facts_by(client, app["id"], "turnover")
    gst = facts_by(client, app["id"], "gst_turnover")
    assert [f["value"] for f in itr] == ["5000000.00"] and [f["value"] for f in gst] == ["4700000.00"]
    conflicts = fin(client, app["id"], "conflicts")
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["metric"] == "turnover" and c["status"] == "CONFLICTING"
    assert {c["value_a"], c["value_b"]} == {"5000000.00", "4700000.00"}
    assert abs(Decimal(c["difference"])) == Decimal("300000.00") and c["difference_pct"] == 0.06
    assert {c["source_a"]["document_type"], c["source_b"]["document_type"]} == {"ITR", "GST_RETURN"}
    cell = profile_cell(client, app["id"], "FY2024-25", "turnover")
    assert cell["status"] == "CONFLICTING" and cell["value"] is None  # not resolved in this phase
    assert len(cell["facts"]) == 1  # the ITR fact is still there, untouched


def test_agreeing_sources_do_not_conflict(client, business):
    app = create_app(client)
    upload(client, app["id"], {"itr.pdf": S.itr_pdf(business, turnover=Decimal("6820000")),
                               "gstr9.pdf": S.gst_return_pdf(business, taxable=Decimal("6820000"))})
    assert fin(client, app["id"], "conflicts") == []
    assert profile_cell(client, app["id"], "FY2024-25", "turnover")["status"] == "AVAILABLE"


def test_different_financial_years_are_never_compared(client, business):
    app = create_app(client)
    upload(client, app["id"], {"itr_ay2024.pdf": S.itr_pdf(business, ay="2024-25", turnover=Decimal("4000000")),
                               "gstr9.pdf": S.gst_return_pdf(business, taxable=Decimal("6695000"))})
    keys = [p["period_key"] for p in fin(client, app["id"], "periods")]
    assert keys == ["FY2023-24", "FY2024-25"]
    assert fin(client, app["id"], "conflicts") == []


# =========================================================================== missing / partial / units
def test_missing_component_gives_partial_fact_never_zero(client, business):
    items = [r for r in S.BS_DEFAULT if r[0] != "Reserves and Surplus"]
    app = create_app(client)
    upload(client, app["id"], {"bs.pdf": S.balance_sheet_pdf(business, items=items)})
    nw = facts_by(client, app["id"], "net_worth")[0]
    assert nw["availability"] == "PARTIAL" and nw["value"] is None
    assert nw["derivation"]["missing_components"] == ["reserves"]
    tl = facts_by(client, app["id"], "total_liabilities")[0]
    assert tl["availability"] == "PARTIAL" and tl["value"] is None  # depends on net worth
    assert profile_cell(client, app["id"], "FY2024-25", "net_worth")["status"] == "PARTIAL"
    for f in fin(client, app["id"], "facts"):
        if f["category"] != "BANKING":
            assert f["value"] is None or Decimal(f["value"]) != 0 or f["raw_value"], f


def test_partly_parsed_statement_gives_partial_bank_metrics(client, business):
    spec = S.default_bank_spec(business)
    spec.txns.insert(8, S.Txn(spec.txns[8].date, "", "", None, None, None, date_text="##/##/####"))
    app = create_app(client)
    upload(client, app["id"], {"stmt.pdf": S.bank_statement_pdf(spec)})
    credits = facts_by(client, app["id"], "total_credits")[0]
    assert credits["availability"] == "PARTIAL" and credits["value"] is None
    assert Decimal(credits["derivation"]["observed_sum"]) > 0 and credits["derivation"]["unparsed_rows"] == 1


def test_bank_statement_for_part_of_the_year_is_partial_for_the_fy(client, business):
    start, end = date(2024, 4, 1), date(2024, 9, 30)
    txns = S.generate_transactions(start, end, Decimal("455000"))
    spec = S.BankStatementSpec("SRI LAKSHMI PRECISION COMPONENTS PVT LTD", business.account_number, business.ifsc,
                               business.bank_name, start, end, Decimal("455000"), txns)
    app = create_app(client)
    upload(client, app["id"], {"stmt_h1.pdf": S.bank_statement_pdf(spec), "pl.pdf": S.profit_loss_pdf(business)})
    keys = {p["period_key"]: p["period_type"] for p in fin(client, app["id"], "periods")}
    assert keys == {"2024-04-01..2024-09-30": "CUSTOM", "FY2024-25": "FY"}
    cell = profile_cell(client, app["id"], "FY2024-25", "total_credits")
    assert cell["status"] == "PARTIAL" and cell["value"] is None
    assert profile_cell(client, app["id"], "2024-04-01..2024-09-30", "total_credits")["status"] == "AVAILABLE"


def test_amounts_in_lakhs_are_normalised_to_inr(client):
    app = create_app(client)
    upload(client, app["id"], {"pl_lakhs.pdf": L.pl_in_lakhs()})
    rev = facts_by(client, app["id"], "revenue")[0]
    assert rev["value"] == "68240000.00" and rev["raw_value"] == "682.40"
    assert Decimal(rev["scale_applied"]) == Decimal(100000)
    assert rev["unit"] == "INR" and rev["currency"] == "INR"
    assert any(n.startswith("UNIT_NORMALISED") for n in rev["notes"])


# =========================================================================== bank transactions
def test_canonical_bank_transactions_and_banking_metrics(client, db, business):
    start, end = date(2024, 4, 1), date(2024, 4, 30)
    opening = Decimal("100000")
    rows = [
        ("02/04/2024", "CASH DEPOSIT BY SELF", None, Decimal("50000")),
        ("05/04/2024", "ATM WDL 4471 COIMBATORE", Decimal("20000"), None),
        ("10/04/2024", "SMS ALERT CHARGES Q1", Decimal("59"), None),
        ("15/04/2024", "ACH DR HDFC LOAN EMI 778", Decimal("38250"), None),
        ("20/04/2024", "NEFT CR SHAKTHI PUMPS", None, Decimal("172445")),
        ("28/04/2024", "RTGS DR KOVAI METALS", Decimal("85750"), None),
    ]
    bal, txns = opening, []
    for d, narr, dr, cr in rows:
        bal = bal + (cr or 0) - (dr or 0)
        txns.append(S.Txn(date(int(d[6:]), int(d[3:5]), int(d[:2])), narr, "REF001", dr, cr, bal))
    spec = S.BankStatementSpec("SRI LAKSHMI PRECISION COMPONENTS PVT LTD", business.account_number, business.ifsc,
                               business.bank_name, start, end, opening, txns)
    app = create_app(client)
    upload(client, app["id"], {"apr.pdf": S.bank_statement_pdf(spec)})
    tx = fin(client, app["id"], "bank-transactions")
    assert len(tx) == 6
    assert [t["category"] for t in tx] == ["CASH_DEPOSIT", "CASH_WITHDRAWAL", "BANK_CHARGES", "DEBT_PAYMENT",
                                           "OTHER", "OTHER"]
    assert tx[0]["direction"] == "CREDIT" and tx[0]["amount"] == "50000.00" and tx[0]["credit"] == "50000.00"
    assert tx[1]["direction"] == "DEBIT" and tx[1]["amount"] == "-20000.00"
    assert tx[0]["transaction_date"] == "2024-04-02" and tx[0]["balance"] == "150000.00"
    assert tx[0]["account_number"] == business.account_number
    # provenance down to the parsed statement row and its box
    row = db.get(ExtractedTableRow, __import__("uuid").UUID(tx[0]["source_row_id"]))
    assert row is not None and row.parsed["description"] == "CASH DEPOSIT BY SELF"
    assert tx[0]["provenance"]["bbox"] == row.bbox and tx[0]["source_page"] == 1

    def v(metric):
        return facts_by(client, app["id"], metric)[0]["value"]

    assert v("total_credits") == "222445.00" and v("total_debits") == "144059.00"
    assert v("cash_deposits") == "50000.00" and v("cash_withdrawals") == "20000.00"
    assert v("bank_charges") == "59.00" and v("debt_payments") == "38250.00"
    # balances: 1,50,000 / 1,30,000 / 1,29,941 / 91,691 / 2,64,136 / 1,78,386
    assert v("minimum_balance") == "91691.00" and v("maximum_balance") == "264136.00"
    # end-of-day balances over all 30 days, carrying forward (opening balance before the first txn)
    avg = facts_by(client, app["id"], "average_balance")[0]
    assert avg["derivation"]["days"] == 30 and Decimal(avg["value"]) > Decimal("100000")
    period = fin(client, app["id"], "periods")
    assert [(p["period_key"], p["period_type"]) for p in period] == [("2024-04", "MONTH")]
    credits = facts_by(client, app["id"], "total_credits")[0]
    assert set(credits["derivation"]["transaction_ids"]) <= {t["id"] for t in tx}


@pytest.mark.parametrize("narr,direction,cat", [
    ("BY CASH DEPOSIT CDM 0042", TxnDirection.CREDIT, TxnCategory.CASH_DEPOSIT),
    ("CASH DEPOSIT", TxnDirection.DEBIT, TxnCategory.OTHER),  # direction must match the category
    ("NFS CASH WDL ATM 7781", TxnDirection.DEBIT, TxnCategory.CASH_WITHDRAWAL),
    ("MIN BAL CHARGES INCL GST", TxnDirection.DEBIT, TxnCategory.BANK_CHARGES),
    ("NACH DR BAJAJ FIN EMI", TxnDirection.DEBIT, TxnCategory.DEBT_PAYMENT),
    ("NEFT CR CUSTOMER", TxnDirection.CREDIT, TxnCategory.OTHER),
])
def test_transaction_classification(narr, direction, cat):
    assert classify(narr, direction) == cat


# =========================================================================== provenance
def test_every_fact_traces_back_to_the_document(client, db, business):
    app = create_app(client)
    upload(client, app["id"], sample_documents(business))
    facts = fin(client, app["id"], "facts")
    assert facts
    tx_ids = {t["id"] for t in fin(client, app["id"], "bank-transactions")}
    for f in facts:
        prov = f["provenance"]
        assert prov["document_id"] == f["source_document_id"] and prov["document_code"] and prov["filename"]
        if f["source_kind"] == "EXTRACTED_FIELD":
            field = db.get(ExtractedField, __import__("uuid").UUID(f["source_field_id"]))
            assert field is not None and field.raw_value == f["raw_value"] == prov["raw_value"]
            assert prov["page"] == field.source_page and prov["source_location"]["bbox"] == field.source_location["bbox"]
            assert prov["source_text"] == field.source_snippet
            assert f["confidence"] == field.confidence
        elif f["source_kind"] == "DERIVED":
            for comp in f["derivation"]["components"]:
                assert comp.get("field_id") or comp.get("fact_id")
                if comp.get("field_id"):
                    assert comp["page"] and comp["source_location"]["bbox"]
        else:
            ids = set(f["derivation"].get("transaction_ids", []))
            assert ids <= tx_ids
            assert prov["pages"] and prov["table_ids"]
    detail = client.get(f"/api/financial-facts/{facts_by(client, app['id'], 'pat')[0]['id']}").json()
    assert detail["extracted_field"]["field_name"] == "profit_after_tax"
    assert detail["extracted_field"]["source_location"]["bbox"] and detail["page"]["page_number"] == 1


def test_rebuild_is_idempotent_and_follows_reprocessing(client, db, business):
    app = create_app(client)
    upload(client, app["id"], sample_documents(business))
    first = fin(client, app["id"], "facts")
    r1 = client.post(f"/api/applications/{app['id']}/financials/rebuild").json()
    r2 = client.post(f"/api/applications/{app['id']}/financials/rebuild").json()
    assert r1["facts"] == r2["facts"] == len(first)
    assert r1["transactions"] == r2["transactions"] == len(fin(client, app["id"], "bank-transactions"))
    pl = next(f for f in first if f["metric"] == "revenue")
    assert client.post(f"/api/documents/{pl['source_document_id']}/process").status_code == 200
    after = fin(client, app["id"], "facts")
    assert len(after) == len(first)
    new_rev = next(f for f in after if f["metric"] == "revenue")
    assert new_rev["source_field_id"] != pl["source_field_id"]  # re-linked to the new extraction run
    assert db.scalar(select(__import__("sqlalchemy").func.count()).select_from(FinancialFact)) == len(after)
    assert db.scalar(select(__import__("sqlalchemy").func.count()).select_from(BankTransaction)) == r2["transactions"]


def test_financial_api_errors(client):
    import uuid

    assert client.get(f"/api/applications/{uuid.uuid4()}/financials/profile").status_code == 404
    app = create_app(client)
    assert client.get(f"/api/applications/{app['id']}/financials/facts", params={"metric": "ebidta"}).status_code == 422
    prof = fin(client, app["id"], "profile")
    assert prof["periods"] == [] and any("never treated as revenue" in n for n in prof["notes"])
    assert client.get(f"/api/financial-facts/{uuid.uuid4()}").status_code == 404


# =========================================================================== extraction regression (column assignment)
def test_long_narration_and_split_dr_cr_stay_in_their_columns(write, business):
    from app.document_ai.pdf_parser import parse_document
    from app.document_ai.table_extractor import extract_tables

    path = write("stmt.pdf", S.bank_statement_pdf(S.default_bank_spec(business)))
    parsed = parse_document(path, "application/pdf")
    tables, _ = extract_tables(path, parsed)
    row = next(r for r in tables[0].rows if r[1] and r[1].startswith("NEFT CR SHAKTHI"))
    assert row[1] == "NEFT CR SHAKTHI PUMPS LTD" and not row[2].startswith("LTD")
    data, exp = L.bank_variant_c()
    path = write("ruled.pdf", data)
    parsed = parse_document(path, "application/pdf")
    tables, _ = extract_tables(path, parsed)
    assert any(c and c.endswith(" Dr") for r in tables[0].rows for c in r)
