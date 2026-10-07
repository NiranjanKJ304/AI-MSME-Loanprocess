"""Monthly cash-flow aggregation and transaction-derived cash-flow metrics (pure Python).

Every classified transaction lands in exactly one bucket - nothing is dropped:
  business_inflow / business_outflow     INCOME / EXPENSE with BUSINESS nature
  financing_inflow / financing_outflow   loan disbursements / EMI, loan interest
  transfer_inflow / transfer_outflow     own-account, internal, related-account transfers
  cash_deposits / cash_withdrawals
  personal_inflow / personal_outflow     PERSONAL nature
  other_inflow / other_outflow           refunds, unpaired reversals, investments, unknown-nature items
  unknown_inflow / unknown_outflow       evidence insufficient - reported, never treated as zero
  excluded_inflow / excluded_outflow     reversal / refund pairs (both legs; net effect nil)
total_inflow / total_outflow are the gross observed credits / debits.
net_operating_cash_flow = business_inflow - business_outflow.

A month is PARTIAL when the statement does not cover the whole month, rows could not be parsed,
or transactions lack a date / direction. Observed values are kept even when PARTIAL; the
availability says how far they can be trusted. Only transaction-derived metrics are computed -
no credit-risk or forecasting logic.
"""

from __future__ import annotations

import calendar
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.models.enums import BusinessNature, ClassificationStatus, FactAvailability, TxnClass, TxnGroup
from app.transaction_intel.engine import Classification, TxnInput

A = FactAvailability
ZERO = Decimal("0.00")
BUCKETS = [
    "business_inflow", "business_revenue_inflow", "business_outflow", "financing_inflow", "financing_outflow",
    "transfer_inflow", "transfer_outflow", "cash_deposits", "cash_withdrawals", "personal_inflow",
    "personal_outflow", "other_inflow", "other_outflow", "unknown_inflow", "unknown_outflow", "excluded_inflow",
    "excluded_outflow", "total_inflow", "total_outflow", "net_operating_cash_flow",
    "low_confidence_business_inflow", "low_confidence_business_outflow",
]


@dataclass
class StatementInfo:
    document_id: str
    account_number: str | None
    period_start: date | None
    period_end: date | None
    failed_rows: int = 0


@dataclass
class MonthAgg:
    scope_document_id: str | None  # None = all statements of the application
    month: str  # YYYY-MM
    month_start: date
    month_end: date
    days_in_month: int
    days_covered: int | None
    values: dict[str, Decimal]
    counts: dict[str, int]
    coverage_count: float | None
    coverage_amount: float | None
    confidence: float | None
    availability: FactAvailability
    partial_reasons: list[str]
    provenance: dict[str, Any]


@dataclass
class MetricOut:
    metric: str
    value: Decimal | None
    unit: str
    availability: FactAvailability
    confidence: float | None
    months_used: int
    months_total: int
    details: dict[str, Any] = field(default_factory=dict)
    month_keys: list[str] = field(default_factory=list)


def bucket_of(t: TxnInput, c: Classification) -> str | None:
    if t.direction not in ("CREDIT", "DEBIT"):
        return None
    inflow = t.direction == "CREDIT"
    side = "inflow" if inflow else "outflow"
    if c.excluded_from_aggregates:
        return f"excluded_{side}"
    if c.status == ClassificationStatus.UNKNOWN or c.category == TxnClass.UNKNOWN:
        return f"personal_{side}" if c.nature == BusinessNature.PERSONAL else f"unknown_{side}"
    if c.nature == BusinessNature.PERSONAL:
        return f"personal_{side}"
    if c.group == TxnGroup.INCOME:
        return "business_inflow" if c.nature == BusinessNature.BUSINESS else "other_inflow"
    if c.group == TxnGroup.EXPENSE:
        return "business_outflow" if c.nature == BusinessNature.BUSINESS else "other_outflow"
    if c.group == TxnGroup.FINANCING:
        return f"financing_{side}"
    if c.group == TxnGroup.TRANSFER:
        return f"transfer_{side}"
    if c.group == TxnGroup.CASH:
        return "cash_deposits" if inflow else "cash_withdrawals"
    return f"other_{side}"


def _months(start: date, end: date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _month_bounds(y: int, m: int) -> tuple[date, date, int]:
    n = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, n), n


def _covered(st: StatementInfo, ms: date, me: date) -> int | None:
    if st.period_start is None or st.period_end is None:
        return None
    lo, hi = max(ms, st.period_start), min(me, st.period_end)
    return max(0, (hi - lo).days + 1)


def _aggregate_month(scope: str | None, y: int, m: int, txns: list[TxnInput], cls: dict[str, Classification],
                     statements: list[StatementInfo], coverage_ok: float) -> MonthAgg:
    ms, me, n_days = _month_bounds(y, m)
    values = {b: ZERO for b in BUCKETS}
    ids: dict[str, list[str]] = defaultdict(list)
    counts = {"txn_count": 0, "business_txn_count": 0, "unknown_txn_count": 0, "low_confidence_txn_count": 0,
              "excluded_txn_count": 0}
    weighted, total_amt, classified_amt, classified_n = Decimal(0), Decimal(0), Decimal(0), 0
    reasons: list[str] = []
    no_direction = 0
    for t in txns:
        c = cls[t.id]
        b = bucket_of(t, c)
        counts["txn_count"] += 1
        if b is None:
            no_direction += 1
            continue
        amt = t.amount or ZERO
        values[b] += amt
        ids[b].append(t.id)
        values["total_inflow" if t.direction == "CREDIT" else "total_outflow"] += amt
        if b == "business_inflow" and c.category == TxnClass.BUSINESS_REVENUE:
            values["business_revenue_inflow"] += amt
            ids["business_revenue_inflow"].append(t.id)
        if b in ("business_inflow", "business_outflow"):
            counts["business_txn_count"] += 1
            if c.status == ClassificationStatus.LOW_CONFIDENCE:
                values[f"low_confidence_{b}"] += amt
        if b.startswith("unknown_"):
            counts["unknown_txn_count"] += 1
        if b.startswith("excluded_"):
            counts["excluded_txn_count"] += 1
        if c.status == ClassificationStatus.LOW_CONFIDENCE:
            counts["low_confidence_txn_count"] += 1
        total_amt += amt
        if not b.startswith("unknown_"):
            classified_amt += amt
            classified_n += 1
            weighted += amt * Decimal(str(c.confidence))
    values["net_operating_cash_flow"] = values["business_inflow"] - values["business_outflow"]

    relevant = [s for s in statements if (scope is None or s.document_id == scope) and (
        (s.period_start and s.period_end and s.period_start <= me and ms <= s.period_end)
        or any(t.document_id == s.document_id for t in txns))]
    covered_days = None
    for s in relevant:
        cov = _covered(s, ms, me)
        if cov is None:
            reasons.append(f"statement {s.document_id[:8]}: period unknown - coverage of {ms:%b %Y} not verifiable")
        elif cov < n_days:
            reasons.append(f"statement {s.document_id[:8]} covers {cov} of {n_days} days of {ms:%b %Y}")
        covered_days = cov if covered_days is None or (cov is not None and cov < covered_days) else covered_days
        if s.failed_rows:
            reasons.append(f"statement {s.document_id[:8]}: {s.failed_rows} row(s) could not be parsed "
                           "(their amounts are not in these totals)")
    if no_direction:
        reasons.append(f"{no_direction} transaction(s) without debit/credit direction")
    n = counts["txn_count"] - no_direction
    cov_count = round(classified_n / n, 4) if n else None
    cov_amount = round(float(classified_amt / total_amt), 4) if total_amt else None
    if reasons:
        availability = A.PARTIAL
    elif cov_amount is not None and cov_amount < coverage_ok:
        availability = A.LOW_CONFIDENCE
    else:
        availability = A.AVAILABLE
    conf = round(float(weighted / total_amt), 3) if total_amt else None
    return MonthAgg(
        scope_document_id=scope, month=f"{y}-{m:02d}", month_start=ms, month_end=me, days_in_month=n_days,
        days_covered=covered_days, values=values, counts=counts, coverage_count=cov_count,
        coverage_amount=cov_amount, confidence=conf, availability=availability,
        partial_reasons=sorted(set(reasons)),
        provenance={"chain": "monthly aggregate -> classified transactions -> canonical bank transactions -> "
                             "extracted statement rows -> document -> page/bbox",
                    "transaction_ids": {k: v for k, v in ids.items() if v},
                    "document_ids": sorted({t.document_id for t in txns})},
    )


def aggregate(txns: list[TxnInput], cls: dict[str, Classification], statements: list[StatementInfo],
              coverage_ok: float) -> list[MonthAgg]:
    out: list[MonthAgg] = []
    undated = defaultdict(int)
    for t in txns:
        if t.date is None:
            undated[t.document_id] += 1

    def months_for(sts: list[StatementInfo], ts: list[TxnInput]) -> list[tuple[int, int]]:
        spans: set[tuple[int, int]] = set()
        for s in sts:
            if s.period_start and s.period_end:
                spans.update(_months(s.period_start, s.period_end))
        for t in ts:
            if t.date:
                spans.add((t.date.year, t.date.month))
        return sorted(spans)

    def build(scope: str | None, sts: list[StatementInfo], ts: list[TxnInput]) -> None:
        by_month: dict[tuple[int, int], list[TxnInput]] = defaultdict(list)
        for t in ts:
            if t.date:
                by_month[(t.date.year, t.date.month)].append(t)
        for y, m in months_for(sts, ts):
            agg = _aggregate_month(scope, y, m, by_month.get((y, m), []), cls, sts, coverage_ok)
            missing_dates = sum(undated[s.document_id] for s in sts)
            if missing_dates:
                agg.partial_reasons.append(f"{missing_dates} transaction(s) without a date could not be placed in a month")
                agg.availability = A.PARTIAL
            out.append(agg)

    for s in statements:
        build(s.document_id, [s], [t for t in txns if t.document_id == s.document_id])
    if statements:
        build(None, statements, txns)
    return out


def _availability_of(months: list[MonthAgg]) -> FactAvailability:
    if any(m.availability == A.PARTIAL for m in months):
        return A.PARTIAL
    if any(m.availability == A.LOW_CONFIDENCE for m in months):
        return A.LOW_CONFIDENCE
    return A.AVAILABLE


def cashflow_metrics(months: list[MonthAgg], txns: list[TxnInput], cls: dict[str, Classification],
                     statements: list[StatementInfo]) -> list[MetricOut]:
    """Transaction-derived cash-flow metrics over the combined (all statements) months."""
    allm = sorted((m for m in months if m.scope_document_id is None), key=lambda m: m.month)
    complete = [m for m in allm if m.availability != A.PARTIAL]
    excluded = [m.month for m in allm if m.availability == A.PARTIAL]
    keys = [m.month for m in allm]
    out: list[MetricOut] = []
    if not allm:
        return out

    for metric, key in (("monthly_business_inflow", "business_inflow"),
                        ("monthly_business_outflow", "business_outflow"),
                        ("monthly_net_business_cash_flow", "net_operating_cash_flow")):
        out.append(MetricOut(metric, None, "INR", _availability_of(allm), None, len(allm), len(allm), {
            "series": [{"month": m.month, "value": str(m.values[key]), "availability": m.availability.value,
                        "coverage_amount": m.coverage_amount} for m in allm]}, keys))

    for metric, key in (("average_monthly_business_inflow", "business_inflow"),
                        ("average_monthly_business_outflow", "business_outflow")):
        if not complete:
            out.append(MetricOut(metric, None, "INR", A.NOT_AVAILABLE, None, 0, len(allm),
                                 {"reason": "no complete month (all months are PARTIAL)", "months_excluded": excluded},
                                 keys))
            continue
        avg = (sum((m.values[key] for m in complete), ZERO) / len(complete)).quantize(Decimal("0.01"))
        avail = A.PARTIAL if excluded else _availability_of(complete)
        confs = [m.confidence for m in complete if m.confidence is not None]
        out.append(MetricOut(metric, avg, "INR", avail, round(statistics.fmean(confs), 3) if confs else None,
                             len(complete), len(allm), {"months_used": [m.month for m in complete],
                                                        "months_excluded_partial": excluded},
                             [m.month for m in complete]))

    for metric, key in (("inflow_consistency", "business_inflow"), ("outflow_consistency", "business_outflow")):
        vals = [float(m.values[key]) for m in complete]
        if len(vals) < 3 or statistics.fmean(vals) <= 0:
            out.append(MetricOut(metric, None, "RATIO", A.NOT_AVAILABLE, None, len(vals), len(allm),
                                 {"reason": "needs at least 3 complete months with business activity",
                                  "months_excluded_partial": excluded}, keys))
            continue
        cv = statistics.pstdev(vals) / statistics.fmean(vals)
        value = Decimal(str(round(max(0.0, min(1.0, 1 - cv)), 4)))
        out.append(MetricOut(metric, value, "RATIO", A.PARTIAL if excluded else _availability_of(complete), None,
                             len(vals), len(allm), {"definition": "1 - coefficient of variation of monthly totals",
                                                    "coefficient_of_variation": round(cv, 4),
                                                    "months_used": [m.month for m in complete],
                                                    "months_excluded_partial": excluded},
                             [m.month for m in complete]))

    any_failed = any(s.failed_rows for s in statements)
    placed = [t for t in txns if t.direction in ("CREDIT", "DEBIT")]
    business = [t for t in placed if bucket_of(t, cls[t.id]) in ("business_inflow", "business_outflow")]
    unknown = [t for t in placed if (bucket_of(t, cls[t.id]) or "").startswith("unknown_")]
    count_avail = A.PARTIAL if any_failed or len(placed) < len(txns) else A.AVAILABLE
    out.append(MetricOut("business_transaction_count", Decimal(len(business)), "COUNT", count_avail, None,
                         len(allm), len(allm), {"transaction_ids": [t.id for t in business]}, keys))
    out.append(MetricOut("unknown_transaction_count", Decimal(len(unknown)), "COUNT", count_avail, None,
                         len(allm), len(allm), {"transaction_ids": [t.id for t in unknown]}, keys))
    total_amt = sum((t.amount or ZERO for t in placed), ZERO)
    unknown_amt = sum((t.amount or ZERO for t in unknown), ZERO)
    cov_amount = Decimal(str(round(float((total_amt - unknown_amt) / total_amt), 4))) if total_amt else None
    out.append(MetricOut("classification_coverage", cov_amount, "RATIO",
                         count_avail if cov_amount is not None else A.NOT_AVAILABLE, None, len(allm), len(allm), {
                             "by_amount": str(cov_amount) if cov_amount is not None else None,
                             "by_count": round((len(placed) - len(unknown)) / len(placed), 4) if placed else None,
                             "classified_transactions": len(placed) - len(unknown), "transactions": len(placed)},
                         keys))
    return out
