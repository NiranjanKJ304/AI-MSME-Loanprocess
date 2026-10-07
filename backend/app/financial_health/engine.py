"""Financial health engine: independent health dimensions per compatible period, then descriptive
indicators. Describes financial condition only - no risk score, repayment capacity or decision.

Periods
  * fact-based metrics are calculated per canonical financial period (FY = ANNUAL, quarter,
    month, custom range = PARTIAL_PERIOD) and only ever combine facts of that one period;
  * trends (year-over-year change, compound change, consistency) compare consecutive financial
    years only, and revenue only on the same basis;
  * bank cash-flow metrics are calculated per financial year from the combined monthly
    aggregates (ANNUAL when all 12 months are covered, otherwise PARTIAL_PERIOD with status
    PARTIAL), plus one MONTHLY net cash-flow metric per month. Bank-derived values are never
    combined with financial-statement values in one formula.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from app.financial_health.config import HealthThresholds
from app.financial_health.explain import fmt
from app.financial_health.formulas import (
    Calc,
    Input,
    change,
    combined_status,
    compound_change,
    consistency,
    difference,
    level,
    q_ratio,
    ratio,
)
from app.financial_health.inputs import PERIOD_KIND, CashWindow, Snapshot
from app.financials.periods import fiscal_year
from app.models import FinancialPeriod
from app.models.enums import (
    DocumentType,
    FactAvailability,
    HealthDimension,
    HealthIndicator,
    HealthPeriodKind,
    PeriodType,
)

A = FactAvailability
D = HealthDimension
HI = HealthIndicator
K = HealthPeriodKind
_WORSE = [A.CONFLICTING, A.NOT_AVAILABLE, A.PARTIAL, A.LOW_CONFIDENCE, A.AVAILABLE]


def _worst(*statuses: FactAvailability) -> FactAvailability:
    return min(statuses, key=_WORSE.index)


@dataclass
class MetricResult:
    dimension: HealthDimension
    metric: str
    label: str
    period_key: str
    period_label: str
    period_kind: HealthPeriodKind
    period_start: date | None
    period_end: date | None
    unit: str
    formula: str
    inputs: list[Input]
    calc: Calc
    compare_period_key: str | None = None
    compare_label: str | None = None
    scope: str | None = None
    window: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)

    @property
    def value(self) -> Decimal | None:
        return self.calc.value

    @property
    def status(self) -> FactAvailability:
        return self.calc.status

    @property
    def confidence(self) -> float | None:
        confs = [i.confidence for i in self.inputs if i.confidence is not None]
        return round(min(confs), 3) if confs else None

    def brief(self) -> dict[str, Any]:
        return {"metric_id": str(self.id), "metric": self.metric, "label": self.label,
                "value": None if self.value is None else str(self.value), "unit": self.unit,
                "status": self.status.value, "period_key": self.period_key, "period_kind": self.period_kind.value,
                "scope": self.scope}

    def as_input(self, name: str, label: str) -> Input:
        """Use a calculated metric as the input of another formula (keeps its sources)."""
        return Input(name=name, label=label, value=self.value, status=self.status, period_key=self.period_key,
                     unit=self.unit, reason=self.calc.reason, confidence=self.confidence,
                     sources=[s for i in self.inputs for s in i.sources],
                     conflicts=[c for i in self.inputs for c in i.conflicts])


@dataclass
class IndicatorResult:
    dimension: HealthDimension
    period_key: str
    period_label: str
    period_kind: HealthPeriodKind
    indicator: HealthIndicator
    rule: str
    evidence: list[dict[str, Any]]
    explanation: str


@dataclass
class _Ctx:
    period_key: str
    period_label: str
    period_kind: HealthPeriodKind
    start: date | None
    end: date | None
    window: str | None = None


class HealthEngine:
    def __init__(self, snapshot: Snapshot, thresholds: HealthThresholds):
        self.s = snapshot
        self.th = thresholds
        self.metrics: list[MetricResult] = []
        self.warnings: list[str] = []
        self._annual: dict[str, dict[str, Any]] = {}  # FY key -> inputs / results used by trends

    # ------------------------------------------------------------------ helpers
    def add(self, ctx: _Ctx, dimension: HealthDimension, metric: str, label: str, unit: str, formula: str,
            inputs: list[Input], calc: Calc, *, keep_unavailable: bool = True, **extra) -> MetricResult | None:
        if not keep_unavailable and calc.status == A.NOT_AVAILABLE:
            return None
        m = MetricResult(dimension=dimension, metric=metric, label=label, period_key=ctx.period_key,
                         period_label=ctx.period_label, period_kind=ctx.period_kind, period_start=ctx.start,
                         period_end=ctx.end, unit=unit, formula=formula, inputs=inputs, calc=calc,
                         window=ctx.window, **extra)
        self.metrics.append(m)
        return m

    def run(self) -> tuple[list[MetricResult], list[IndicatorResult]]:
        fys = {p.period_key: p for p in self.s.fy_periods()}
        for p in self.s.periods:
            self.fact_metrics(p)
        for key in sorted(fys):
            if key in self._annual:
                self.annual_trends(fys[key], fys)
        self.bank_balances()
        windows = self.s.cash_windows()
        for w in windows.values():
            self.cash_flow(w)
        self.monthly_cash_flow()
        self.tax_compliance(set(fys) | set(windows) | {f"FY{p.fiscal_year}" for p in self.s.periods if p.fiscal_year})
        return self.metrics, self.indicators()

    # ------------------------------------------------------------------ financial-statement metrics
    def fact_metrics(self, p: FinancialPeriod) -> None:
        if not any(f.category != "BANKING" for f in self.s.facts_in(p)):
            return
        full = p.period_type == PeriodType.FY
        ctx = _Ctx(p.period_key, p.label, PERIOD_KIND[p.period_type], p.start_date, p.end_date)

        def fi(metric: str, label: str) -> Input:
            return self.s.fact_input(p, [metric], metric, label)

        add = lambda *a, **k: self.add(ctx, *a, keep_unavailable=full, **k)  # noqa: E731
        rev = self.s.revenue_input(p)
        add(D.REVENUE, "revenue", "Revenue", "INR",
            "revenue = P&L revenue from operations (if absent: declared turnover from ITR / GST return)",
            [rev], level(rev))

        gp, op, pat = fi("gross_profit", "Gross profit"), fi("operating_profit", "Operating profit"), \
            fi("pat", "Profit after tax")
        add(D.PROFITABILITY, "gross_margin", "Gross margin", "RATIO", "gross_margin = gross_profit / revenue",
            [gp, rev], ratio(gp, rev))
        add(D.PROFITABILITY, "operating_margin", "Operating margin", "RATIO",
            "operating_margin = operating_profit / revenue", [op, rev], ratio(op, rev))
        net_margin = add(D.PROFITABILITY, "net_margin", "Net margin", "RATIO", "net_margin = PAT / revenue",
                         [pat, rev], ratio(pat, rev))

        cash, ca, cl = fi("cash_and_bank", "Cash and bank balances"), fi("current_assets", "Current assets"), \
            fi("current_liabilities", "Current liabilities")
        add(D.LIQUIDITY, "cash_and_bank", "Cash and bank position (balance sheet)", "INR",
            "cash_and_bank as reported / derived in the balance sheet", [cash], level(cash))
        add(D.LIQUIDITY, "current_ratio", "Current ratio", "TIMES",
            "current_ratio = current_assets / current_liabilities", [ca, cl], ratio(ca, cl))
        add(D.LIQUIDITY, "net_working_capital", "Current assets less current liabilities", "INR",
            "net_working_capital = current_assets - current_liabilities", [ca, cl], difference(ca, cl))

        bor, nw = fi("borrowings", "Borrowings"), fi("net_worth", "Net worth")
        add(D.LEVERAGE, "borrowings", "Borrowings", "INR", "borrowings as reported in the balance sheet",
            [bor], level(bor))
        add(D.LEVERAGE, "debt_to_revenue", "Debt to revenue", "TIMES", "debt_to_revenue = borrowings / revenue",
            [bor, rev], ratio(bor, rev))
        add(D.LEVERAGE, "debt_to_net_worth", "Debt to net worth", "TIMES",
            "debt_to_net_worth = borrowings / net_worth", [bor, nw], ratio(bor, nw))

        if full:
            self._annual[p.period_key] = {"revenue": rev, "pat": pat, "borrowings": bor, "net_margin": net_margin}

    def _annual_inputs(self, key: str, fys: dict[str, FinancialPeriod]) -> dict[str, Any]:
        if key in self._annual:
            return self._annual[key]
        label = fiscal_year(int(key[2:6])).label
        missing = {n: Input(name=n, label=l, value=None, status=A.NOT_AVAILABLE, period_key=key,
                            reason=f"no financial statement / tax data for {label}")
                   for n, l in (("revenue", "Revenue"), ("pat", "Profit after tax"), ("borrowings", "Borrowings"))}
        return {**missing, "net_margin": None}

    def annual_trends(self, p: FinancialPeriod, fys: dict[str, FinancialPeriod]) -> None:
        cur = self._annual[p.period_key]
        prev_fy = fiscal_year(p.start_date.year - 1)
        prev = self._annual_inputs(prev_fy.key, fys)
        cmp = dict(compare_period_key=prev_fy.key, compare_label=prev_fy.label)
        ctx = _Ctx(p.period_key, p.label, K.ANNUAL, prev_fy.start, p.end_date)

        def yoy(dim, name: str, label: str):
            a = replace(prev[name], name=f"{name}_previous", label=f"{label} {prev_fy.label}")
            b = replace(cur[name], name=f"{name}_current", label=f"{label} {p.label}")
            absolute, pct = change(a, b)
            if name == "revenue" and a.has_value and b.has_value and a.basis != b.basis:
                why = f"revenue bases differ ({a.basis} vs {b.basis}); not comparable"
                absolute = pct = Calc(None, A.NOT_AVAILABLE, why)
            self.add(ctx, dim, f"{name}_yoy_change", f"{label} change", "INR",
                     f"{name}_yoy_change = {name}({p.label}) - {name}({prev_fy.label})", [a, b], absolute, **cmp)
            self.add(ctx, dim, f"{name}_yoy_growth", f"{label} growth", "RATIO",
                     f"{name}_yoy_growth = ({name}({p.label}) - {name}({prev_fy.label})) / {name}({prev_fy.label})",
                     [a, b], pct, **cmp)

        yoy(D.REVENUE, "revenue", "Revenue")
        yoy(D.PROFITABILITY, "pat", "Profit after tax")
        yoy(D.LEVERAGE, "borrowings", "Borrowings")

        # net margin change in percentage points
        nm_cur: MetricResult | None = cur["net_margin"]
        nm_prev: MetricResult | None = prev["net_margin"]
        a = nm_prev.as_input("net_margin_previous", f"Net margin {prev_fy.label}") if nm_prev else Input(
            "net_margin_previous", f"Net margin {prev_fy.label}", None, A.NOT_AVAILABLE, prev_fy.key, unit="RATIO",
            reason=f"no profit and loss data for {prev_fy.label}")
        b = nm_cur.as_input("net_margin_current", f"Net margin {p.label}")
        absolute, _ = change(a, b)
        self.add(ctx, D.PROFITABILITY, "net_margin_change", "Net margin change (percentage points)", "RATIO",
                 f"net_margin_change = net_margin({p.label}) - net_margin({prev_fy.label})", [a, b], absolute, **cmp)

        # compound change and consistency over the consecutive years ending here
        for dim, name, label, metric in ((D.REVENUE, "revenue", "Revenue", "revenue_trend"),
                                         (D.PROFITABILITY, "pat", "Profit after tax", "profit_trend"),
                                         (D.LEVERAGE, "borrowings", "Borrowings", "debt_trend")):
            series = self._consecutive(p, name, fys)
            calc = compound_change(series)
            first = fys.get(series[0].period_key)
            trend_ctx = _Ctx(p.period_key, p.label, K.ANNUAL, first.start_date if first else p.start_date, p.end_date)
            self.add(trend_ctx, dim, metric, f"{label} trend (compound annual change)", "RATIO",
                     f"({name}(last) / {name}(first)) ^ (1 / (years - 1)) - 1 over consecutive financial years "
                     f"({', '.join(s.period_key for s in series)})", series, calc)
            if name == "revenue":
                min_years = self.th.calculation.min_years_for_consistency
                status, reason = combined_status(series)
                if status in (A.CONFLICTING, A.NOT_AVAILABLE):
                    cons = Calc(None, status, reason)
                else:
                    cons = consistency([s.value for s in series], min_years)
                    if cons.value is not None:
                        cons.status, cons.reason = status, reason
                self.add(trend_ctx, D.REVENUE, "revenue_consistency", "Revenue consistency (annual)", "RATIO",
                         f"1 - (std dev / mean) of annual revenue over at least {min_years} consecutive financial "
                         "years", series, cons)

    def _consecutive(self, p: FinancialPeriod, name: str, fys: dict[str, FinancialPeriod]) -> list[Input]:
        """Inputs for `name` in consecutive FYs ending at p (stops at a gap, a missing value or a basis change)."""
        cur = self._annual[p.period_key][name]
        series = [replace(cur, label=f"{cur.label} {p.label}")]
        if not cur.has_value:
            return series
        year = p.start_date.year - 1
        while True:
            key = fiscal_year(year).key
            if key not in self._annual:
                break
            inp = self._annual[key][name]
            if not inp.has_value or inp.status == A.PARTIAL or (name == "revenue" and inp.basis != cur.basis):
                break
            series.insert(0, replace(inp, label=f"{inp.label} {fiscal_year(year).label}"))
            year -= 1
        return series

    # ------------------------------------------------------------------ bank balances
    def bank_balances(self) -> None:
        for f in self.s.bank_balance_facts():
            p = self.s.period_by_id.get(f.period_id)
            if p is None:
                self.warnings.append(f"{(f.provenance or {}).get('document_code')}: {f.metric} has no statement "
                                     "period; not analysed")
                continue
            prov = f.provenance or {}
            inp = self.s.fact_input(p, [f.metric], f.metric, "Average balance" if f.metric == "average_balance"
                                    else "Minimum balance")
            # one statement = one account: use this fact only (accounts are never added together)
            inp.sources = [s for s in inp.sources if s["fact_id"] == str(f.id)]
            inp.value, inp.status, inp.confidence = f.value, f.availability, f.confidence
            inp.notes = list(f.notes or [])[:3]
            ctx = _Ctx(p.period_key, p.label, PERIOD_KIND[p.period_type], p.start_date, p.end_date)
            metric, label, formula = (
                ("average_bank_balance", "Average bank balance", "mean of end-of-day balances over the statement period")
                if f.metric == "average_balance" else
                ("minimum_bank_balance", "Minimum bank balance", "lowest end-of-day balance over the statement period"))
            self.add(ctx, D.LIQUIDITY, metric, label, "INR", formula, [inp], level(inp),
                     scope=f"{prov.get('document_code')} / A/c {prov.get('account_number') or 'unknown'}")

    # ------------------------------------------------------------------ bank cash flow (per FY window)
    def cash_flow(self, w: CashWindow) -> None:
        window = f"{w.start:%b %Y} - {w.end:%b %Y}"
        ctx = _Ctx(w.fy_key, w.fy_label, w.kind, w.start, w.end, window)
        cal = self.th.calculation
        inflow = self.s.window_sum(w, "business_inflow", "business_inflow", "Business inflow")
        outflow = self.s.window_sum(w, "business_outflow", "business_outflow", "Business outflow")
        self.add(ctx, D.CASH_FLOW, "business_inflow", "Business inflow (bank)", "INR",
                 "sum of credits classified INCOME with BUSINESS nature (transfers, loans, refunds, reversals, "
                 "cash deposits and unknown credits are excluded)", [inflow], level(inflow))
        self.add(ctx, D.CASH_FLOW, "business_outflow", "Business outflow (bank)", "INR",
                 "sum of debits classified EXPENSE with BUSINESS nature", [outflow], level(outflow))
        net = difference(inflow, outflow)
        self.add(ctx, D.CASH_FLOW, "net_business_cash_flow", "Net business cash flow (bank)", "INR",
                 "net_business_cash_flow = business_inflow - business_outflow", [inflow, outflow], net)
        net_in = Input("net_business_cash_flow", "Net business cash flow", net.value, net.status, w.fy_key,
                       reason=net.reason, sources=inflow.sources + outflow.sources)
        self.add(ctx, D.CASH_FLOW, "cash_flow_margin", "Cash flow margin", "RATIO",
                 "cash_flow_margin = net_business_cash_flow / business_inflow", [inflow, outflow],
                 ratio(net_in, inflow))

        series = Input("monthly_net_business_cash_flow", "Monthly net business cash flow", None, w.status, w.fy_key,
                       unit="SERIES", reason=w.reason,
                       sources=[self.s.month_source(m, ["business_inflow", "business_outflow",
                                                         "net_operating_cash_flow"]) for m in w.months])
        excluded = [m.month for m in w.months if m not in w.complete]
        pos = [m.month for m in w.complete if m.net_operating_cash_flow > 0]
        neg = [m.month for m in w.complete if m.net_operating_cash_flow < 0]
        details = {"positive_months": pos, "negative_months": neg,
                   "zero_months": [m.month for m in w.complete if m.net_operating_cash_flow == 0],
                   "complete_months": len(w.complete), "partial_months_excluded": excluded}
        for metric, label, months in (("positive_cash_flow_months", "Positive cash-flow months", pos),
                                      ("negative_cash_flow_months", "Negative cash-flow months", neg)):
            calc = (Calc(Decimal(len(months)), w.status, w.reason, details) if w.complete else
                    Calc(None, A.NOT_AVAILABLE, "no complete month (all months are PARTIAL)", details))
            self.add(ctx, D.CASH_FLOW, metric, label, "MONTHS",
                     f"count of complete months with net business cash flow {'>' if 'positive' in metric else '<'} 0",
                     [series], calc)
        if len(w.complete) < cal.min_months_for_consistency:
            calc = Calc(None, A.NOT_AVAILABLE, f"needs at least {cal.min_months_for_consistency} complete months "
                                               f"(have {len(w.complete)})", details)
        else:
            calc = Calc(q_ratio(Decimal(len(pos)) / Decimal(len(w.complete))), w.status, w.reason, details)
        self.add(ctx, D.CASH_FLOW, "cash_flow_consistency", "Cash-flow consistency", "RATIO",
                 "positive_cash_flow_months / complete_months", [series], calc)

        total = sum((m.total_inflow + m.total_outflow for m in w.months), Decimal("0.00"))
        unknown = sum((m.unknown_inflow + m.unknown_outflow for m in w.months), Decimal("0.00"))
        classified_in = Input("classified_amount", "Classified transaction value", total - unknown, w.status, w.fy_key,
                              reason=w.reason, sources=[self.s.month_source(m, ["unknown_inflow", "unknown_outflow"])
                                                        for m in w.months])
        total_in = Input("total_amount", "Total transaction value (credits + debits)", total, w.status, w.fy_key,
                         reason=w.reason, sources=[self.s.month_source(m, ["total_inflow", "total_outflow"])
                                                   for m in w.months])
        self.add(ctx, D.CASH_FLOW, "classification_coverage", "Classification coverage (by value)", "RATIO",
                 "classification_coverage = (total value - UNKNOWN value) / total value", [classified_in, total_in],
                 ratio(classified_in, total_in))
        unknown_in = self.s.window_sum(w, "unknown_inflow", "unknown_inflow", "Unknown inflow")
        all_in = self.s.window_sum(w, "total_inflow", "total_inflow", "Total inflow (all credits)")
        self.add(ctx, D.CASH_FLOW, "unknown_inflow_share", "Share of credits left UNKNOWN", "RATIO",
                 "unknown_inflow_share = unknown_inflow / total_inflow", [unknown_in, all_in], ratio(unknown_in, all_in))

        # leverage: observed debt servicing / new loans in the bank data (descriptive only)
        fin_out = self.s.window_sum(w, "financing_outflow", "financing_outflow", "Financing outflow")
        fin_in = self.s.window_sum(w, "financing_inflow", "financing_inflow", "Financing inflow")
        self.add(ctx, D.LEVERAGE, "debt_payments_observed", "Debt payments observed in bank data", "INR",
                 "sum of debits classified FINANCING (loan EMI / repayment, loan interest)", [fin_out], level(fin_out))
        self.add(ctx, D.LEVERAGE, "loan_disbursements_observed", "Loan disbursements observed in bank data", "INR",
                 "sum of credits classified LOAN_DISBURSEMENT", [fin_in], level(fin_in))

        self.stability(ctx, w, details)

    def stability(self, ctx: _Ctx, w: CashWindow, details: dict[str, Any]) -> None:
        cal = self.th.calculation
        series = Input("monthly_business_inflow", "Monthly business inflow", None, w.status, w.fy_key, unit="SERIES",
                       reason=w.reason, sources=[self.s.month_source(m, ["business_inflow"]) for m in w.complete])
        cons = consistency([m.business_inflow for m in w.complete], cal.min_months_for_consistency)
        if cons.value is not None:
            cons.status, cons.reason = w.status, w.reason
        cons.details["months_used"] = [m.month for m in w.complete]
        cons.details["partial_months_excluded"] = details["partial_months_excluded"]
        self.add(ctx, D.BUSINESS_STABILITY, "business_inflow_consistency", "Business inflow consistency", "RATIO",
                 "1 - (std dev / mean) of monthly business inflow over complete months", [series], cons)

        active = [m.month for m in w.complete if m.business_inflow > 0]
        n_txn = sum(m.business_txn_count for m in w.complete)
        if w.complete:
            self.add(ctx, D.BUSINESS_STABILITY, "active_business_months", "Months with business inflow", "MONTHS",
                     "count of complete months with business inflow > 0", [series],
                     Calc(Decimal(len(active)), w.status, w.reason, {"months": active,
                                                                     "complete_months": len(w.complete)}))
            self.add(ctx, D.BUSINESS_STABILITY, "average_monthly_business_transactions",
                     "Average business transactions per month", "COUNT",
                     "business transactions (inflow + outflow) in complete months / complete months", [series],
                     Calc(q_ratio(Decimal(n_txn) / Decimal(len(w.complete))), w.status, w.reason,
                          {"business_transactions": n_txn, "complete_months": len(w.complete)}))
        else:
            for metric, label in (("active_business_months", "Months with business inflow"),
                                  ("average_monthly_business_transactions", "Average business transactions per month")):
                self.add(ctx, D.BUSINESS_STABILITY, metric, label, "COUNT", "over complete months", [series],
                         Calc(None, A.NOT_AVAILABLE, "no complete month (all months are PARTIAL)"))

        # concentration of business inflow by counterparty + recurring share
        ids = self.s.business_inflow_transactions(w.months)
        txn_src = Input("business_inflow_transactions", "Business inflow transactions", None, w.status, w.fy_key,
                        unit="SERIES", reason=w.reason,
                        sources=[{"kind": "TRANSACTIONS", "selection": "business inflow transactions",
                                  "transaction_ids": ids}])
        total = sum((self.s.txn_amount(t) for t in ids), Decimal("0"))
        by_cp: dict[str, Decimal] = defaultdict(Decimal)
        unidentified = Decimal("0")
        recurring = Decimal("0")
        patterns: set[str] = set()
        for t in ids:
            c = self.s.classifications.get(t)
            amt = self.s.txn_amount(t)
            if c is not None and c.normalized_counterparty:
                by_cp[c.normalized_counterparty] += amt
            else:
                unidentified += amt
            if c is not None and c.recurring_pattern_id:
                recurring += amt
                patterns.add(str(c.recurring_pattern_id))
        if total <= 0:
            for metric, label in (("top_counterparty_share", "Largest customer share of business inflow"),
                                  ("recurring_business_inflow_share", "Recurring share of business inflow")):
                self.add(ctx, D.BUSINESS_STABILITY, metric, label, "RATIO", "share of business inflow", [txn_src],
                         Calc(None, A.NOT_AVAILABLE, "no business inflow in the bank data for this period"))
            return
        ranked = sorted(by_cp.items(), key=lambda kv: (-kv[1], kv[0]))
        unid_share = unidentified / total
        status, reason = w.status, w.reason
        if not ranked:
            conc = Calc(None, A.NOT_AVAILABLE, "no business inflow has an identified counterparty")
        else:
            if unid_share >= Decimal(str(cal.unidentified_counterparty_share_low_confidence)):
                status = _worst(status, A.LOW_CONFIDENCE)
                reason = "; ".join(x for x in (reason, f"{unid_share:.0%} of business inflow has no identified "
                                                       "counterparty") if x)
            conc = Calc(q_ratio(ranked[0][1] / total), status, reason, {
                "top_counterparties": [{"counterparty": k, "amount": str(v), "share": str(q_ratio(v / total))}
                                       for k, v in ranked[:3]],
                "top_3_share": str(q_ratio(sum(v for _, v in ranked[:3]) / total)),
                "identified_counterparties": len(ranked), "unidentified_share": str(q_ratio(unid_share)),
                "business_inflow": str(total)})
        self.add(ctx, D.BUSINESS_STABILITY, "top_counterparty_share", "Largest customer share of business inflow",
                 "RATIO", "top_counterparty_share = business inflow from the largest counterparty / business inflow",
                 [txn_src], conc)
        pats = [self.s.patterns[p] for p in sorted(patterns) if p in self.s.patterns]
        self.add(ctx, D.BUSINESS_STABILITY, "recurring_business_inflow_share", "Recurring share of business inflow",
                 "RATIO", "recurring_business_inflow_share = business inflow belonging to a recurring pattern / "
                          "business inflow", [txn_src],
                 Calc(q_ratio(recurring / total), w.status, w.reason, {
                     "recurring_amount": str(recurring), "business_inflow": str(total),
                     "patterns": [{"pattern_id": str(p.id), "counterparty": p.counterparty, "type": p.pattern_type,
                                   "frequency": p.frequency.value, "occurrences": p.occurrences} for p in pats]}))

    def monthly_cash_flow(self) -> None:
        for m in self.s.months:
            ctx = _Ctx(m.month, m.month_start.strftime("%b %Y"), K.MONTHLY, m.month_start, m.month_end)
            reason = "; ".join(m.partial_reasons or []) or None
            src = [self.s.month_source(m, ["business_inflow"])]
            inflow = Input("business_inflow", "Business inflow", m.business_inflow, m.availability, m.month,
                           reason=reason, confidence=m.confidence, sources=src)
            outflow = Input("business_outflow", "Business outflow", m.business_outflow, m.availability, m.month,
                            reason=reason, confidence=m.confidence,
                            sources=[self.s.month_source(m, ["business_outflow"])])
            calc = difference(inflow, outflow)
            if calc.value is not None:
                calc.details = {"direction": "POSITIVE" if calc.value > 0 else "NEGATIVE" if calc.value < 0 else "ZERO",
                                "days_covered": m.days_covered, "days_in_month": m.days_in_month,
                                "classification_coverage_amount": m.classification_coverage_amount}
            self.add(ctx, D.CASH_FLOW, "net_business_cash_flow", "Net business cash flow (bank, month)", "INR",
                     "net_business_cash_flow = business_inflow - business_outflow", [inflow, outflow], calc)

    # ------------------------------------------------------------------ tax / compliance coverage
    def tax_compliance(self, fy_keys: set[str]) -> None:
        for key in sorted(fy_keys):
            fy = fiscal_year(int(key[2:6]))
            ctx = _Ctx(fy.key, fy.label, K.ANNUAL, fy.start, fy.end)
            itr, gst = {}, {}
            months: set[str] = set()
            for p in self.s.periods:
                if not (fy.start <= p.start_date and p.end_date <= fy.end):
                    continue
                # documents that normalised to this period: from the period's source expressions, and
                # from the facts of the period (which carry the source document type themselves)
                refs: dict[str, dict[str, Any]] = {}
                for e in p.source_expressions or []:
                    d = self.s.documents.get(e.get("document_id") or "")
                    if d is not None:
                        refs[str(d.id)] = {"kind": "DOCUMENT", "document_id": str(d.id),
                                           "document_code": d.document_code, "document_type": d.document_type.value,
                                           "period_key": p.period_key, "period_expression": e.get("expression")}
                for f in self.s.facts_in(p):
                    prov = f.provenance or {}
                    doc_id = prov.get("document_id")
                    if doc_id and doc_id not in refs and f.source_document_type is not None:
                        refs[doc_id] = {"kind": "DOCUMENT", "document_id": doc_id,
                                        "document_code": prov.get("document_code"),
                                        "document_type": f.source_document_type.value, "period_key": p.period_key,
                                        "period_expression": None}
                for doc_id, ref in refs.items():
                    if ref["document_type"] == DocumentType.ITR.value:
                        itr[doc_id] = ref
                    elif ref["document_type"] == DocumentType.GST_RETURN.value:
                        gst[doc_id] = ref
                        y, mth = p.start_date.year, p.start_date.month
                        while (y, mth) <= (p.end_date.year, p.end_date.month):
                            months.add(f"{y}-{mth:02d}")
                            y, mth = (y + 1, 1) if mth == 12 else (y, mth + 1)
            all_months = []
            y, mth = fy.start.year, fy.start.month
            for _ in range(12):
                all_months.append(f"{y}-{mth:02d}")
                y, mth = (y + 1, 1) if mth == 12 else (y, mth + 1)
            missing = [m for m in all_months if m not in months]
            itr_in = Input("itr_documents", "ITR documents", Decimal(len(itr)), A.AVAILABLE, key, unit="COUNT",
                           sources=list(itr.values()))
            gst_in = Input("gst_documents", "GST return documents", Decimal(len(gst)), A.AVAILABLE, key, unit="COUNT",
                           sources=list(gst.values()))
            note = "documents provided in this application (absence here does not establish non-filing)"
            self.add(ctx, D.TAX_COMPLIANCE, "itr_documents", "ITR available for the year", "COUNT",
                     f"count of ITR documents whose assessment year maps to {fy.label} - {note}", [itr_in],
                     Calc(Decimal(len(itr)), A.AVAILABLE))
            self.add(ctx, D.TAX_COMPLIANCE, "gst_return_documents", "GST returns available for the year", "COUNT",
                     f"count of GST returns whose tax period falls in {fy.label} - {note}", [gst_in],
                     Calc(Decimal(len(gst)), A.AVAILABLE))
            self.add(ctx, D.TAX_COMPLIANCE, "gst_period_coverage", "GST filing-period coverage", "RATIO",
                     "months of the financial year covered by GST returns / 12", [gst_in],
                     Calc(q_ratio(Decimal(12 - len(missing)) / Decimal(12)), A.AVAILABLE, None,
                          {"months_covered": sorted(months & set(all_months)), "missing_months": missing}))
            self.add(ctx, D.TAX_COMPLIANCE, "missing_gst_months", "Months without a GST return", "MONTHS",
                     "12 - months covered by GST returns", [gst_in],
                     Calc(Decimal(len(missing)), A.AVAILABLE, None, {"missing_months": missing}))
            conflicts = self.s.conflicts_within(fy.start, fy.end)
            conf_in = Input("conflicts", "Source conflicts", Decimal(len(conflicts)), A.AVAILABLE, key, unit="COUNT",
                            conflicts=[{"conflict_id": str(c.id), "metric": c.metric, "value_a": str(c.value_a),
                                        "value_b": str(c.value_b), "difference_pct": c.difference_pct,
                                        "source_a": c.source_a, "source_b": c.source_b} for c in conflicts])
            self.add(ctx, D.TAX_COMPLIANCE, "source_conflicts", "Conflicting sources", "COUNT",
                     "count of recorded conflicts between sources for periods in this financial year", [conf_in],
                     Calc(Decimal(len(conflicts)), A.AVAILABLE, None,
                          {"metrics": sorted({c.metric for c in conflicts})}))

    # ------------------------------------------------------------------ indicators
    def indicators(self) -> list[IndicatorResult]:
        groups: dict[tuple[HealthDimension, str], list[MetricResult]] = defaultdict(list)
        for m in self.metrics:
            if m.period_kind != K.MONTHLY:
                groups[(m.dimension, m.period_key)].append(m)
        rules: dict[HealthDimension, Callable] = {
            D.REVENUE: self._revenue_rule, D.PROFITABILITY: self._profitability_rule,
            D.LIQUIDITY: self._liquidity_rule, D.CASH_FLOW: self._cash_flow_rule, D.LEVERAGE: self._leverage_rule,
            D.BUSINESS_STABILITY: self._stability_rule, D.TAX_COMPLIANCE: self._tax_rule,
        }
        out = []
        for (dim, key), ms in sorted(groups.items(), key=lambda kv: (kv[0][1], list(D).index(kv[0][0]))):
            by_name = {m.metric: m for m in ms if m.scope is None}
            indicator, rule, used = rules[dim](by_name, ms)
            used = [u for u in used if u is not None]
            kind = used[0].period_kind if used else ms[0].period_kind
            caveats = sorted({m.status.value for m in used if m.status in (A.PARTIAL, A.LOW_CONFIDENCE)})
            explanation = (f"{dim.value.replace('_', ' ').title()} - {ms[0].period_label} ({kind.value.lower()}): "
                           f"{indicator.value}. Rule: {rule}.")
            if used:
                explanation += " Based on: " + "; ".join(
                    f"{u.label} = {fmt(u.value, u.unit)} ({u.status.value})" for u in used) + "."
            if caveats:
                explanation += f" Caution: based on {' / '.join(caveats)} data."
            explanation += " Descriptive only - not a credit assessment."
            out.append(IndicatorResult(dim, key, ms[0].period_label, kind, indicator, rule,
                                       [u.brief() for u in used], explanation))
        return out

    @staticmethod
    def _conflicting(*ms: MetricResult | None) -> list[MetricResult]:
        return [m for m in ms if m is not None and m.status == A.CONFLICTING]

    def _revenue_rule(self, by, ms):
        t = self.th.revenue
        rev, g = by.get("revenue"), by.get("revenue_yoy_growth")
        if c := self._conflicting(rev, g):
            return HI.CONFLICTING_DATA, "revenue sources conflict; no value selected", c
        if g is None or g.value is None:
            why = g.calc.reason if g is not None else "single period"
            return HI.INSUFFICIENT_DATA, f"year-over-year revenue growth not available ({why})", [rev, g]
        if g.value <= Decimal(str(t.declining_growth_at_most)):
            return HI.DECLINING, f"revenue growth <= {t.declining_growth_at_most:.0%}", [rev, g]
        if g.value >= Decimal(str(t.strong_growth_at_least)):
            return HI.STRONG, f"revenue growth >= {t.strong_growth_at_least:.0%}", [rev, g]
        return HI.STABLE, (f"{t.declining_growth_at_most:.0%} < revenue growth < "
                           f"{t.strong_growth_at_least:.0%}"), [rev, g]

    def _profitability_rule(self, by, ms):
        t = self.th.profitability
        nm, chg = by.get("net_margin"), by.get("net_margin_change")
        if c := self._conflicting(nm):
            return HI.CONFLICTING_DATA, "net margin inputs conflict; no value selected", c
        if nm is None or nm.value is None:
            why = nm.calc.reason if nm is not None else "no profit and loss data"
            return HI.INSUFFICIENT_DATA, f"net margin not available ({why})", [nm]
        if nm.value < Decimal(str(t.weak_net_margin_below)):
            return HI.WEAK, f"net margin < {t.weak_net_margin_below:.0%}", [nm]
        if chg is not None and chg.value is not None and chg.value <= -Decimal(str(t.declining_margin_drop_at_least)):
            return HI.DECLINING, (f"net margin fell by >= {t.declining_margin_drop_at_least * 100:.1f} percentage "
                                  "points year over year"), [nm, chg]
        if nm.value >= Decimal(str(t.strong_net_margin_at_least)):
            return HI.STRONG, f"net margin >= {t.strong_net_margin_at_least:.0%}", [nm, chg]
        return HI.STABLE, (f"{t.weak_net_margin_below:.0%} <= net margin < {t.strong_net_margin_at_least:.0%}"), [nm, chg]

    def _liquidity_rule(self, by, ms):
        t = self.th.liquidity
        cr = by.get("current_ratio")
        if c := self._conflicting(cr):
            return HI.CONFLICTING_DATA, "current assets / liabilities conflict; no value selected", c
        if cr is not None and cr.value is not None:
            if cr.value < Decimal(str(t.weak_current_ratio_below)):
                return HI.WEAK, f"current ratio < {t.weak_current_ratio_below}", [cr]
            if cr.value >= Decimal(str(t.strong_current_ratio_at_least)):
                return HI.STRONG, f"current ratio >= {t.strong_current_ratio_at_least}", [cr]
            return HI.STABLE, (f"{t.weak_current_ratio_below} <= current ratio < "
                               f"{t.strong_current_ratio_at_least}"), [cr]
        mins = [m for m in ms if m.metric == "minimum_bank_balance" and m.value is not None]
        overdrawn = [m for m in mins if m.value < 0]
        if overdrawn:
            return HI.WEAK, "a bank account's minimum end-of-day balance is negative (overdrawn)", overdrawn
        why = cr.calc.reason if cr is not None else "no balance sheet for this period"
        return HI.INSUFFICIENT_DATA, f"current ratio not available ({why}); bank balances are reported, not rated", \
            [cr] + mins

    def _cash_flow_rule(self, by, ms):
        t, cal = self.th.cash_flow, self.th.calculation
        margin, pos, cov = by.get("cash_flow_margin"), by.get("cash_flow_consistency"), \
            by.get("classification_coverage")
        if margin is None or margin.value is None:
            why = margin.calc.reason if margin is not None else "no bank statement for this period"
            return HI.INSUFFICIENT_DATA, f"cash flow margin not available ({why})", [margin]
        if cov is not None and cov.value is not None and cov.value < Decimal(str(cal.min_classification_coverage)):
            return HI.INSUFFICIENT_DATA, (f"only {cov.value:.0%} of transaction value is classified "
                                          f"(< {cal.min_classification_coverage:.0%})"), [cov, margin]
        if margin.value < Decimal(str(t.weak_cash_flow_margin_below)):
            return HI.WEAK, f"cash flow margin < {t.weak_cash_flow_margin_below:.0%}", [margin, pos, cov]
        if margin.value >= Decimal(str(t.strong_cash_flow_margin_at_least)) and pos is not None and \
                pos.value is not None and pos.value >= Decimal(str(t.strong_positive_month_share_at_least)):
            return HI.STRONG, (f"cash flow margin >= {t.strong_cash_flow_margin_at_least:.0%} and positive months "
                               f">= {t.strong_positive_month_share_at_least:.0%}"), [margin, pos, cov]
        return HI.STABLE, "cash flow margin >= 0 without meeting the STRONG thresholds", [margin, pos, cov]

    def _leverage_rule(self, by, ms):
        t = self.th.leverage
        d = by.get("debt_to_revenue")
        if c := self._conflicting(d):
            return HI.CONFLICTING_DATA, "borrowings / revenue inputs conflict; no value selected", c
        if d is None or d.value is None:
            why = d.calc.reason if d is not None else "no balance sheet for this period"
            return HI.INSUFFICIENT_DATA, f"debt to revenue not available ({why})", [d]
        if d.value > Decimal(str(t.weak_debt_to_revenue_above)):
            return HI.WEAK, f"debt to revenue > {t.weak_debt_to_revenue_above}", [d]
        if d.value <= Decimal(str(t.strong_debt_to_revenue_at_most)):
            return HI.STRONG, f"debt to revenue <= {t.strong_debt_to_revenue_at_most} (low leverage)", [d]
        return HI.STABLE, (f"{t.strong_debt_to_revenue_at_most} < debt to revenue <= "
                           f"{t.weak_debt_to_revenue_above}"), [d]

    def _stability_rule(self, by, ms):
        t = self.th.stability
        c, conc = by.get("business_inflow_consistency"), by.get("top_counterparty_share")
        if c is None or c.value is None:
            why = c.calc.reason if c is not None else "no bank statement for this period"
            return HI.INSUFFICIENT_DATA, f"business inflow consistency not available ({why})", [c]
        if c.value < Decimal(str(t.weak_consistency_below)):
            return HI.WEAK, f"business inflow consistency < {t.weak_consistency_below}", [c, conc]
        concentrated = conc is not None and conc.value is not None and \
            conc.value >= Decimal(str(t.high_concentration_at_least))
        if c.value >= Decimal(str(t.strong_consistency_at_least)) and not concentrated:
            return HI.STRONG, (f"business inflow consistency >= {t.strong_consistency_at_least} and largest customer "
                               f"share < {t.high_concentration_at_least:.0%}"), [c, conc]
        rule = "business inflow consistency between the WEAK and STRONG thresholds"
        if concentrated:
            rule = f"largest customer share >= {t.high_concentration_at_least:.0%} (concentrated inflows)"
        return HI.STABLE, rule, [c, conc]

    def _tax_rule(self, by, ms):
        itr, cov, conf = by.get("itr_documents"), by.get("gst_period_coverage"), by.get("source_conflicts")
        if conf is not None and conf.value:
            return HI.CONFLICTING_DATA, "sources for this financial year conflict (see conflicts)", [conf]
        has_itr = itr is not None and itr.value
        gst_cov = cov.value if cov is not None and cov.value is not None else Decimal(0)
        if not has_itr and gst_cov == 0:
            return HI.INSUFFICIENT_DATA, "no ITR or GST return for this financial year among the documents provided", \
                [itr, cov]
        if has_itr and gst_cov == 1:
            return HI.STRONG, "ITR available and GST returns cover all 12 months", [itr, cov, conf]
        return HI.WEAK, "filing coverage incomplete among the documents provided (ITR missing or GST months missing)", \
            [itr, cov, conf]
