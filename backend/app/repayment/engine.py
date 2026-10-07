"""Repayment-capacity engine (descriptive; no risk score, approval or rejection).

Cash available for debt service (CADS) = business inflow - business outflow, i.e. only transactions
classified INCOME / EXPENSE with BUSINESS nature. Own-account transfers, loan disbursements,
personal credits, financing inflows, refunds, reversals and UNKNOWN credits are never operating
income. Existing debt service = debits classified LOAN_REPAYMENT / INTEREST_PAYMENT; other recurring
debits are never assumed to be loan EMIs.

Bases
  HISTORICAL  averages over the complete months of the most recent history window
  FORECAST    averages over the forecast months (business inflow / outflow forecasts)
  STATEMENT   latest FY: (PAT + depreciation + interest) / annualised total debt service
DSCR = CADS / (existing debt service + proposed repayment), monthly basis.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.financial_health.formulas import Calc, Input, combined_status, difference, q_inr, q_ratio, ratio
from app.financial_health.inputs import Snapshot
from app.models import (
    FinancialHealthIndicator,
    ForecastResult,
    ForecastRun,
    RepaymentLoanTerms,
)
from app.models.enums import (
    CapacityOutcome,
    ClassificationStatus,
    CounterpartyType,
    FactAvailability,
    TxnClass,
)
from app.repayment.config import RepaymentConfig
from app.repayment.loan import Repayment, compute_repayment

A = FactAvailability
O = CapacityOutcome
PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
_WORSE = [A.CONFLICTING, A.NOT_AVAILABLE, A.PARTIAL, A.LOW_CONFIDENCE, A.AVAILABLE]
DEBT_CATEGORIES = (TxnClass.LOAN_REPAYMENT, TxnClass.INTEREST_PAYMENT)


def worst(*s: FactAvailability) -> FactAvailability:
    return min(s, key=_WORSE.index)


def add_inputs(*inputs: Input) -> Calc:
    """Sum of inputs with the usual status rules (missing / conflicting never summed as zero)."""
    status, reason = combined_status(list(inputs))
    if status in (A.CONFLICTING, A.NOT_AVAILABLE) or any(i.value is None for i in inputs):
        return Calc(None, status, reason)
    return Calc(q_inr(sum((i.value for i in inputs), Decimal(0))), status, reason)


def as_input(c: Calc, name: str, label: str, sources: list[dict], unit: str = "INR") -> Input:
    return Input(name=name, label=label, value=c.value, status=c.status, reason=c.reason, unit=unit, sources=sources)


def _mean(vals: list[Decimal]) -> Decimal:
    return q_inr(sum(vals, Decimal(0)) / Decimal(len(vals)))


@dataclass
class MetricOut:
    basis: str
    metric: str
    label: str
    period: str | None
    unit: str
    formula: str
    inputs: list[Input]
    calc: Calc


@dataclass
class ScenarioOut:
    scenario: str
    basis: str
    assumption: str
    inflow: Decimal | None = None
    outflow: Decimal | None = None
    cash: Decimal | None = None
    existing: Decimal | None = None
    proposed: Decimal | None = None
    total: Decimal | None = None
    dscr: Decimal | None = None
    post: Decimal | None = None
    status: FactAvailability = A.NOT_AVAILABLE
    reason: str | None = None


@dataclass
class Analysis:
    status: FactAvailability
    outcome: CapacityOutcome
    reasons: list[str]
    terms: dict[str, Any] | None
    repayment: Repayment | None
    existing_debt: dict[str, Any]
    by_period: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    health_context: list[dict[str, Any]]
    assumptions: list[str]
    metrics: list[MetricOut] = field(default_factory=list)
    scenarios: list[ScenarioOut] = field(default_factory=list)
    forecast_run_ids: list[str] = field(default_factory=list)
    monthly_aggregate_ids: list[str] = field(default_factory=list)


class RepaymentEngine:
    def __init__(self, db: Session, snap: Snapshot, terms: RepaymentLoanTerms | None, cfg: RepaymentConfig):
        self.db, self.s, self.t, self.cfg = db, snap, terms, cfg
        self.checks: list[dict[str, Any]] = []
        self.metrics: list[MetricOut] = []

    def chk(self, name: str, result: str, detail: str, **extra) -> None:
        self.checks.append({"check": name, "result": result, "detail": detail, **extra})

    def metric(self, basis, metric, label, period, unit, formula, inputs, calc) -> MetricOut:
        m = MetricOut(basis, metric, label, period, unit, formula, inputs, calc)
        self.metrics.append(m)
        return m

    # ------------------------------------------------------------------ loan terms
    def proposed(self) -> tuple[Repayment | None, Input]:
        name, label = "proposed_debt_service", "Proposed repayment (monthly equivalent)"
        if self.t is None:
            self.chk("proposed_loan_terms", FAIL, "no proposed loan terms provided; repayment is calculated only "
                     "from explicitly provided terms (amount, rate, tenure, frequency)")
            return None, Input(name, label, None, A.NOT_AVAILABLE, reason="no proposed loan terms provided")
        try:
            rep = compute_repayment(self.t.requested_amount, self.t.annual_interest_rate, self.t.tenure_months,
                                    self.t.repayment_frequency, self.t.grace_period_months,
                                    self.t.grace_period_treatment)
        except ValueError as e:
            self.chk("proposed_loan_terms", FAIL, f"stored loan terms are invalid: {e}")
            return None, Input(name, label, None, A.NOT_AVAILABLE, reason=f"invalid loan terms: {e}")
        self.chk("proposed_loan_terms", PASS, "amount, rate, tenure and frequency provided "
                 f"by {self.t.provided_by or 'an unnamed user'} ({self.t.source})")
        src = {"kind": "LOAN_TERMS", "loan_terms_id": str(self.t.id), "source": self.t.source,
               "provided_by": self.t.provided_by, "installment": str(rep.installment),
               "frequency": rep.frequency.value, "formula": rep.formula}
        return rep, Input(name, label, rep.monthly_equivalent, A.AVAILABLE,
                          basis=f"{rep.frequency.value.lower()} installment {rep.installment} / {rep.months_per_period}",
                          sources=[src])

    # ------------------------------------------------------------------ history
    def history(self):
        cfg = self.cfg
        window = self.s.months[-cfg.history_months:]
        complete = [m for m in window if m.availability != A.PARTIAL]
        partial = [m for m in window if m.availability == A.PARTIAL]
        missing: list[str] = []
        if window:
            idx = {m.month_start.year * 12 + m.month_start.month - 1 for m in window}
            lo, hi = min(idx), max(idx)
            missing = [f"{i // 12}-{i % 12 + 1:02d}" for i in range(lo, hi + 1) if i not in idx]
        n = len(complete)
        reasons: list[str] = []
        if n < cfg.min_history_months:
            status = A.NOT_AVAILABLE
            reasons.append(f"only {n} complete month(s) of classified bank cash flow; at least "
                           f"{cfg.min_history_months} required")
            self.chk("historical_cash_flow_coverage", FAIL, reasons[-1], complete_months=n)
        else:
            status = A.AVAILABLE
            self.chk("historical_cash_flow_coverage", WARN if n < cfg.history_months else PASS,
                     f"{n} complete month(s) in the {cfg.history_months}-month history window", complete_months=n)
            if n < cfg.history_months:
                status = A.LOW_CONFIDENCE
                reasons.append(f"only {n} complete months (fewer than {cfg.history_months})")
        self.chk("partial_months", WARN if partial else PASS,
                 f"PARTIAL month(s) {', '.join(m.month for m in partial)} excluded (not complete)" if partial
                 else "no partial months", months=[m.month for m in partial])
        self.chk("missing_months", WARN if missing else PASS,
                 f"no bank data for {', '.join(missing)} (not filled)" if missing else "no missing months",
                 months=missing)
        if status != A.NOT_AVAILABLE and (partial or missing):
            status = A.PARTIAL
            reasons.append("history window has partial / missing months: " + ", ".join(
                [m.month for m in partial] + missing))
        if complete:
            total = sum((m.total_inflow + m.total_outflow for m in complete), Decimal(0))
            unknown = sum((m.unknown_inflow + m.unknown_outflow for m in complete), Decimal(0))
            cov = float((total - unknown) / total) if total else None
            tin = sum((m.total_inflow for m in complete), Decimal(0))
            tout = sum((m.total_outflow for m in complete), Decimal(0))
            uin = float(sum((m.unknown_inflow for m in complete), Decimal(0)) / tin) if tin else None
            uout = float(sum((m.unknown_outflow for m in complete), Decimal(0)) / tout) if tout else None
            weak = []
            if cov is not None and cov < cfg.min_classification_coverage:
                weak.append(f"classification coverage {cov:.1%}")
            self.chk("classification_coverage", WARN if cov is not None and cov < cfg.min_classification_coverage
                     else PASS, f"{cov:.1%} of transaction value classified" if cov is not None else "no transactions",
                     value=None if cov is None else round(cov, 4), threshold=cfg.min_classification_coverage)
            for name, share, what in (("unknown_credit_share", uin, "credits (could hide business inflow)"),
                                      ("unknown_debit_share", uout, "debits (could hide expenses or debt payments)")):
                bad = share is not None and share > cfg.max_unknown_share
                if bad:
                    weak.append(f"{share:.1%} UNKNOWN {what.split(' (')[0]}")
                self.chk(name, WARN if bad else PASS,
                         f"{share:.1%} of {what} is UNKNOWN" if share is not None else f"no {what.split(' ')[0]}",
                         value=None if share is None else round(share, 4), threshold=cfg.max_unknown_share)
            if any(m.availability == A.LOW_CONFIDENCE for m in complete):
                weak.append("low-confidence months")
            if weak and status == A.AVAILABLE:
                status = A.LOW_CONFIDENCE
            if weak:
                reasons.append("; ".join(weak))
        return window, complete, partial, missing, status, "; ".join(reasons) or None

    # ------------------------------------------------------------------ existing debt evidence
    def existing_debt(self, complete, status, reason) -> tuple[Input, dict[str, Any]]:
        name, label = "existing_debt_service", "Existing debt service (monthly average)"
        ids = [t for m in complete for t in ((m.provenance or {}).get("transaction_ids") or {}).get("financing_outflow", [])]
        txns, low = [], []
        for tid in ids:
            c, t = self.s.classifications.get(tid), self.s.transactions.get(tid)
            if c is None or t is None or c.category not in DEBT_CATEGORIES:
                continue
            txns.append({"transaction_id": tid, "date": t.transaction_date.isoformat() if t.transaction_date else None,
                         "amount": str(t.debit), "category": c.category.value, "status": c.status.value,
                         "confidence": c.confidence, "counterparty": c.normalized_counterparty,
                         "recurring_pattern_id": str(c.recurring_pattern_id) if c.recurring_pattern_id else None,
                         "page": t.source_page})
            if c.status == ClassificationStatus.LOW_CONFIDENCE:
                low.append(tid)
        months = {m.month for m in complete}
        possible = []
        for tid, c in self.s.classifications.items():
            t = self.s.transactions.get(tid)
            if (t is not None and t.debit is not None and t.transaction_date is not None
                    and t.transaction_date.strftime("%Y-%m") in months
                    and c.category == TxnClass.UNKNOWN and c.counterparty_type == CounterpartyType.BANK_OR_LENDER):
                possible.append({"transaction_id": tid, "date": t.transaction_date.isoformat(), "amount": str(t.debit),
                                 "counterparty": c.normalized_counterparty, "page": t.source_page,
                                 "note": "debit to a bank / lender left UNKNOWN - not counted as debt service"})
        patterns = [{"pattern_id": str(p.id), "type": p.pattern_type, "counterparty": p.counterparty,
                     "frequency": p.frequency.value, "average_amount": str(p.average_amount),
                     "occurrences": p.occurrences} for p in self.s.patterns.values()
                    if p.direction == "DEBIT" and p.pattern_type in ("EMI", "LOAN_INTEREST")]
        fys = self.s.fy_periods()
        borrowings = self.s.fact_input(fys[-1], ["borrowings"], "borrowings", "Borrowings") if fys else None
        value = _mean([m.financing_outflow for m in complete]) if complete and status != A.NOT_AVAILABLE else None
        st, reasons = status, [reason] if reason else []
        warn = []
        if low:
            warn.append(f"{len(low)} debt payment(s) classified with LOW_CONFIDENCE")
        if possible:
            warn.append(f"{len(possible)} debit(s) to banks / lenders left UNKNOWN (possible debt, not counted)")
        if borrowings is not None and borrowings.has_value and borrowings.value > 0 and value is not None and value == 0:
            warn.append(f"balance sheet reports borrowings of {borrowings.value} ({borrowings.period_key}) but no "
                        "repayments are observed in the bank statements provided")
        self.chk("existing_debt_evidence", WARN if warn else PASS,
                 "; ".join(warn) if warn else f"{len(txns)} classified debt payment(s) in the history window",
                 transactions=len(txns))
        if warn and st == A.AVAILABLE:
            st = A.LOW_CONFIDENCE
        reasons += warn
        inp = Input(name, label, value, st, reason="; ".join(reasons) or None,
                    basis="mean monthly debits classified LOAN_REPAYMENT / INTEREST_PAYMENT",
                    sources=[self.s.month_source(m, ["financing_outflow"]) for m in complete])
        evidence = {
            "monthly_average": None if value is None else str(value), "status": st.value,
            "rule": "only debits classified LOAN_REPAYMENT / INTEREST_PAYMENT are counted; other recurring debits are "
                    "not assumed to be loan EMIs",
            "transactions": txns, "low_confidence_transaction_ids": low, "recurring_debt_patterns": patterns,
            "possible_unclassified_debt": possible,
            "borrowings": None if borrowings is None else borrowings.as_dict(),
        }
        return inp, evidence

    # ------------------------------------------------------------------ forecast
    def forecast_inputs(self):
        runs = {r.metric: r for r in self.db.scalars(select(ForecastRun).where(
            ForecastRun.application_id == self.s.application_id,
            ForecastRun.metric.in_(["business_inflow", "business_outflow"])))}
        na = lambda why: (Input("forecast_business_inflow", "Forecast business inflow (monthly average)", None,  # noqa: E731
                                A.NOT_AVAILABLE, reason=why),
                          Input("forecast_business_outflow", "Forecast business outflow (monthly average)", None,
                                A.NOT_AVAILABLE, reason=why), [], [])
        if len(runs) < 2:
            self.chk("forecast_confidence", WARN, "forecasts not built; forecast capacity not calculated")
            return na("forecasts not built")
        bad = [r for r in runs.values() if r.status == A.NOT_AVAILABLE]
        if bad:
            why = "; ".join(f"{r.metric}: {r.reason}" for r in bad)
            self.chk("forecast_confidence", WARN, f"forecast NOT_AVAILABLE ({why}); forecast capacity not calculated")
            return na(why)
        res = {m: {x.period_key: x for x in self.db.scalars(select(ForecastResult).where(
            ForecastResult.run_id == r.id))} for m, r in runs.items()}
        periods = sorted(k for k in res["business_inflow"] if k in res["business_outflow"]
                         and res["business_inflow"][k].predicted_value is not None
                         and res["business_outflow"][k].predicted_value is not None)
        if not periods:
            self.chk("forecast_confidence", WARN, "no forecast month with both inflow and outflow")
            return na("no forecast month with both business inflow and outflow")
        st = worst(*(r.status for r in runs.values()))
        self.chk("forecast_confidence", WARN if st != A.AVAILABLE else PASS,
                 f"forecasts are {st.value}: " + "; ".join(f"{r.metric} ({r.model}): {r.reason or 'all checks passed'}"
                                                          for r in runs.values()))

        def inp(metric: str, label: str) -> Input:
            run = runs[metric]
            rows = [res[metric][k] for k in periods]
            srcs = [{"kind": "FORECAST", "forecast_run_id": str(run.id), "forecast_result_id": str(x.id),
                     "period": x.period_key, "value": str(x.predicted_value), "model": run.model,
                     "status": x.status.value} for x in rows]
            srcs.append({"kind": "TRANSACTIONS", "selection": f"training history of the {metric} forecast",
                         "transaction_ids": (run.provenance or {}).get("transaction_ids", [])})
            return Input(f"forecast_{metric}", label, _mean([x.predicted_value for x in rows]), st,
                         reason=run.reason, basis=f"mean of {len(rows)} forecast month(s) ({run.model})", sources=srcs)

        return (inp("business_inflow", "Forecast business inflow (monthly average)"),
                inp("business_outflow", "Forecast business outflow (monthly average)"), periods,
                [str(r.id) for r in runs.values()])

    # ------------------------------------------------------------------ capacity per basis
    def capacity(self, basis: str, period: str | None, inflow: Input, outflow: Input, existing: Input,
                 proposed: Input) -> dict[str, Any]:
        cads = difference(inflow, outflow)
        cads_in = as_input(cads, "cash_available", "Cash available for debt service", inflow.sources + outflow.sources)
        total = add_inputs(existing, proposed)
        total_in = as_input(total, "total_debt_service", "Total debt service (monthly)",
                            existing.sources + proposed.sources)
        m = lambda *a: self.metric(basis, *a)  # noqa: E731
        m("average_monthly_business_inflow", "Average monthly business inflow", period, "INR",
          "mean of monthly business inflow (INCOME with BUSINESS nature)", [inflow], Calc(inflow.value, inflow.status,
                                                                                         inflow.reason))
        m("average_monthly_business_outflow", "Average monthly business outflow", period, "INR",
          "mean of monthly business outflow (EXPENSE with BUSINESS nature)", [outflow],
          Calc(outflow.value, outflow.status, outflow.reason))
        m("cash_available_for_debt_service", "Cash available for debt service (monthly)", period, "INR",
          "business_inflow - business_outflow", [inflow, outflow], cads)
        m("existing_debt_service", "Existing debt service (monthly)", period, "INR",
          "mean monthly debits classified LOAN_REPAYMENT / INTEREST_PAYMENT", [existing],
          Calc(existing.value, existing.status, existing.reason))
        m("proposed_debt_service", "Proposed repayment (monthly equivalent)", period, "INR",
          "installment / months per repayment period", [proposed], Calc(proposed.value, proposed.status,
                                                                        proposed.reason))
        m("total_debt_service", "Total debt service (monthly)", period, "INR",
          "existing_debt_service + proposed_debt_service", [existing, proposed], total)
        m("post_debt_service_cash_flow", "Cash flow after total debt service (monthly)", period, "INR",
          "cash_available_for_debt_service - total_debt_service", [cads_in, total_in], difference(cads_in, total_in))
        m("existing_dscr", "DSCR on existing debt only", period, "TIMES",
          "cash_available_for_debt_service / existing_debt_service", [cads_in, existing], ratio(cads_in, existing))
        dscr = m("dscr", "DSCR including the proposed loan", period, "TIMES",
                 "cash_available_for_debt_service / (existing_debt_service + proposed_debt_service)",
                 [cads_in, total_in], ratio(cads_in, total_in))
        return {"inflow": inflow, "outflow": outflow, "existing": existing, "proposed": proposed, "cads": cads,
                "total": total, "dscr": dscr}

    def statement(self, total_monthly: Calc, ds_sources: list[dict]) -> None:
        fys = [p for p in self.s.fy_periods() if any(f.metric == "pat" for f in self.s.facts_in(p))]
        if not fys:
            for metric, label in (("annual_cash_accrual", "Annual cash accrual (PAT + depreciation)"),
                                  ("statement_dscr", "Statement-based DSCR")):
                self.metric("STATEMENT", metric, label, None, "INR" if "accrual" in metric else "TIMES",
                            "needs a profit and loss statement", [],
                            Calc(None, A.NOT_AVAILABLE, "no profit and loss statement among the documents"))
            return
        p = fys[-1]
        pat = self.s.fact_input(p, ["pat"], "pat", "Profit after tax")
        dep = self.s.fact_input(p, ["depreciation"], "depreciation", "Depreciation")
        intr = self.s.fact_input(p, ["interest_expense"], "interest_expense", "Interest expense")
        conflicting = [i.label for i in (pat, dep, intr) if i.status == A.CONFLICTING]
        if conflicting:
            self.chk("conflicting_facts", WARN, f"conflicting financial facts for {p.period_key}: "
                     f"{', '.join(conflicting)} - statement-based capacity not calculated", period=p.period_key)
        else:
            self.chk("conflicting_facts", PASS, f"no conflicting facts among PAT / depreciation / interest "
                     f"({p.period_key})")
        accrual = add_inputs(pat, dep)
        self.metric("STATEMENT", "annual_cash_accrual", "Annual cash accrual (PAT + depreciation)", p.period_key,
                    "INR", "PAT + depreciation", [pat, dep], accrual)
        numerator = add_inputs(pat, dep, intr)
        num_in = as_input(numerator, "cash_accrual_plus_interest", "PAT + depreciation + interest",
                          pat.sources + dep.sources + intr.sources)
        num_in.conflicts = pat.conflicts + dep.conflicts + intr.conflicts
        annual = Calc(None if total_monthly.value is None else q_inr(total_monthly.value * 12), total_monthly.status,
                      total_monthly.reason)
        ann_in = as_input(annual, "annual_total_debt_service", "Annualised total debt service (12 x monthly)",
                          ds_sources)
        self.metric("STATEMENT", "statement_dscr", "Statement-based DSCR (latest FY)", p.period_key, "TIMES",
                    "(PAT + depreciation + interest expense) / (12 x (existing + proposed monthly debt service))",
                    [pat, dep, intr, ann_in], ratio(num_in, ann_in))

    # ------------------------------------------------------------------ scenarios
    def scenarios(self, basis: str, cap: dict[str, Any] | None) -> list[ScenarioOut]:
        d = Decimal(str(self.t.revenue_down_pct if self.t and self.t.revenue_down_pct is not None
                        else self.cfg.stress.revenue_down_pct))
        u = Decimal(str(self.t.expense_up_pct if self.t and self.t.expense_up_pct is not None
                        else self.cfg.stress.expense_up_pct))
        defs = [("BASE", Decimal(0), Decimal(0), f"{basis.lower()} values unchanged"),
                ("REVENUE_DOWN", d, Decimal(0), f"business inflow reduced by {d:.0%}; outflow and debt service unchanged"),
                ("EXPENSE_UP", Decimal(0), u, f"business outflow increased by {u:.0%}; inflow and debt service unchanged"),
                ("COMBINED_STRESS", d, u, f"business inflow reduced by {d:.0%} and outflow increased by {u:.0%}; "
                                          "debt service unchanged")]
        out = []
        for name, dd, uu, assumption in defs:
            sc = ScenarioOut(name, basis, assumption)
            if cap is None:
                sc.reason = "no data for this basis"
                out.append(sc)
                continue
            inflow, outflow, existing, proposed = cap["inflow"], cap["outflow"], cap["existing"], cap["proposed"]
            status, reason = combined_status([inflow, outflow, existing, proposed])
            sc.status, sc.reason = status, reason
            if status in (A.CONFLICTING, A.NOT_AVAILABLE) or None in (inflow.value, outflow.value, existing.value,
                                                                      proposed.value):
                out.append(sc)
                continue
            sc.inflow, sc.outflow = q_inr(inflow.value * (1 - dd)), q_inr(outflow.value * (1 + uu))
            sc.cash = sc.inflow - sc.outflow
            sc.existing, sc.proposed = existing.value, proposed.value
            sc.total = sc.existing + sc.proposed
            sc.post = sc.cash - sc.total
            sc.dscr = q_ratio(sc.cash / sc.total) if sc.total > 0 else None
            out.append(sc)
        return out

    # ------------------------------------------------------------------ run
    def run(self) -> Analysis:
        rep, proposed = self.proposed()
        window, complete, partial, missing, hstatus, hreason = self.history()
        existing, debt = self.existing_debt(complete, hstatus, hreason)
        ok = hstatus != A.NOT_AVAILABLE
        h_in = Input("business_inflow", "Average monthly business inflow",
                     _mean([m.business_inflow for m in complete]) if ok else None, hstatus, reason=hreason,
                     sources=[self.s.month_source(m, ["business_inflow"]) for m in complete])
        h_out = Input("business_outflow", "Average monthly business outflow",
                      _mean([m.business_outflow for m in complete]) if ok else None, hstatus, reason=hreason,
                      sources=[self.s.month_source(m, ["business_outflow"]) for m in complete])
        hperiod = f"{complete[0].month}..{complete[-1].month}" if complete else None
        hist = self.capacity("HISTORICAL", hperiod, h_in, h_out, existing, proposed)
        positives = [m.net_operating_cash_flow for m in complete if m.net_operating_cash_flow > 0]
        pos_calc = (Calc(_mean(positives), hstatus, hreason, {"months": len(positives)}) if ok and positives else
                    Calc(None, A.NOT_AVAILABLE, "no complete month with positive business cash flow" if ok else hreason))
        self.metric("HISTORICAL", "average_positive_business_cash_flow",
                    "Average business cash flow of the positive months", hperiod, "INR",
                    "mean of (business inflow - business outflow) over complete months where it is > 0",
                    [Input("monthly_net", "Monthly net business cash flow", None, hstatus, unit="SERIES",
                           sources=[self.s.month_source(m, ["business_inflow", "business_outflow"])
                                    for m in complete if m.net_operating_cash_flow > 0])], pos_calc)

        f_in, f_out, f_periods, run_ids = self.forecast_inputs()
        fexisting = replace(existing, label="Existing debt service (historical average, assumed to continue)")
        fcap = self.capacity("FORECAST", f"{f_periods[0]}..{f_periods[-1]}" if f_periods else None, f_in, f_out,
                             fexisting, proposed)
        self.statement(hist["total"], existing.sources + proposed.sources)
        scenarios = self.scenarios("HISTORICAL", hist) + self.scenarios("FORECAST", fcap)

        by_period = []
        for m in window:
            row = {"period": m.month, "kind": "HISTORICAL", "status": m.availability.value}
            if m.availability == A.PARTIAL:
                row["reason"] = "PARTIAL month - excluded (not complete)"
            else:
                cash = m.business_inflow - m.business_outflow
                prop = proposed.value
                total = None if prop is None else m.financing_outflow + prop
                row.update(business_inflow=str(m.business_inflow), business_outflow=str(m.business_outflow),
                           cash_available=str(cash), existing_debt_service=str(m.financing_outflow),
                           proposed_debt_service=None if prop is None else str(prop),
                           total_debt_service=None if total is None else str(total),
                           post_debt_service_cash_flow=None if total is None else str(cash - total),
                           dscr=None if not total else str(q_ratio(cash / total)))
            by_period.append(row)
        for k in missing:
            by_period.append({"period": k, "kind": "HISTORICAL", "status": A.NOT_AVAILABLE.value,
                              "reason": "no bank data for this month (not filled)"})
        for src_in, src_out in [(f_in, f_out)] if f_periods else []:
            fi = {s["period"]: Decimal(s["value"]) for s in src_in.sources if s.get("kind") == "FORECAST"}
            fo = {s["period"]: Decimal(s["value"]) for s in src_out.sources if s.get("kind") == "FORECAST"}
            for k in f_periods:
                cash = fi[k] - fo[k]
                total = None if None in (existing.value, proposed.value) else existing.value + proposed.value
                by_period.append({"period": k, "kind": "FORECAST", "status": f_in.status.value,
                                  "business_inflow": str(fi[k]), "business_outflow": str(fo[k]),
                                  "cash_available": str(cash),
                                  "existing_debt_service": None if existing.value is None else str(existing.value),
                                  "proposed_debt_service": None if proposed.value is None else str(proposed.value),
                                  "total_debt_service": None if total is None else str(total),
                                  "post_debt_service_cash_flow": None if total is None else str(cash - total),
                                  "dscr": None if not total else str(q_ratio(cash / total))})
        by_period.sort(key=lambda r: (r["kind"] != "HISTORICAL", r["period"]))

        status, outcome, reasons = self.outcome(rep, hist)
        return Analysis(
            status=status, outcome=outcome, reasons=reasons, terms=self.terms_view(), repayment=rep,
            existing_debt=debt, by_period=by_period, checks=self.checks, health_context=self.health_context(),
            assumptions=self.assumptions(rep), metrics=self.metrics, scenarios=scenarios, forecast_run_ids=run_ids,
            monthly_aggregate_ids=[str(m.id) for m in complete])

    def outcome(self, rep, hist) -> tuple[FactAvailability, CapacityOutcome, list[str]]:
        dscr: MetricOut = hist["dscr"]
        post = next(m for m in self.metrics if m.basis == "HISTORICAL" and m.metric == "post_debt_service_cash_flow")
        warns = [c["detail"] for c in self.checks if c["result"] == WARN]
        fails = [c["detail"] for c in self.checks if c["result"] == FAIL]
        if rep is None or dscr.calc.value is None and dscr.calc.status != A.CONFLICTING:
            return (A.NOT_AVAILABLE, O.LIMITED_DATA,
                    fails or [dscr.calc.reason or "historical repayment capacity not calculable"])
        conflicting = [m for m in self.metrics if m.calc.status == A.CONFLICTING]
        if conflicting:
            return dscr.calc.status, O.CONFLICTING_DATA, [
                f"{m.label} ({m.basis.lower()}): {m.calc.reason}" for m in conflicting] + warns
        if post.calc.value < 0:
            return dscr.calc.status, O.NEGATIVE_CAPACITY, [
                f"average monthly business cash flow does not cover total debt service: cash flow after debt service "
                f"{post.calc.value} per month (DSCR {dscr.calc.value})"] + warns
        if dscr.calc.value < Decimal(str(self.cfg.low_capacity_dscr_below)):
            return dscr.calc.status, O.LOW_CAPACITY, [
                f"historical DSCR {dscr.calc.value} is below the descriptive threshold "
                f"{self.cfg.low_capacity_dscr_below}"] + warns
        if dscr.calc.status != A.AVAILABLE or warns:
            return dscr.calc.status, O.LIMITED_DATA, warns or [dscr.calc.reason or "data limitations"]
        return dscr.calc.status, O.ADEQUATE_DATA, [
            f"historical DSCR {dscr.calc.value} with complete, well-classified history"]

    def terms_view(self) -> dict[str, Any] | None:
        t = self.t
        if t is None:
            return None
        return {"loan_terms_id": str(t.id), "source": t.source, "provided_by": t.provided_by,
                "requested_amount": str(t.requested_amount), "annual_interest_rate": str(t.annual_interest_rate),
                "tenure_months": t.tenure_months, "repayment_frequency": t.repayment_frequency.value,
                "grace_period_months": t.grace_period_months,
                "grace_period_treatment": t.grace_period_treatment.value if t.grace_period_treatment else None,
                "revenue_down_pct": t.revenue_down_pct, "expense_up_pct": t.expense_up_pct,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None}

    def health_context(self) -> list[dict[str, Any]]:
        fys = self.s.fy_periods()
        if not fys:
            return []
        key = fys[-1].period_key
        rows = self.db.scalars(select(FinancialHealthIndicator).where(
            FinancialHealthIndicator.application_id == self.s.application_id,
            FinancialHealthIndicator.period_key == key))
        return [{"indicator_id": str(r.id), "dimension": r.dimension.value, "period_key": r.period_key,
                 "indicator": r.indicator.value, "rule": r.rule,
                 "note": "context from the financial health layer; not used in the calculations"}
                for r in rows if r.dimension.value in ("CASH_FLOW", "LEVERAGE", "PROFITABILITY")]

    def assumptions(self, rep: Repayment | None) -> list[str]:
        d = self.t.revenue_down_pct if self.t and self.t.revenue_down_pct is not None else self.cfg.stress.revenue_down_pct
        u = self.t.expense_up_pct if self.t and self.t.expense_up_pct is not None else self.cfg.stress.expense_up_pct
        out = [
            "Cash available for debt service = business inflow - business outflow (transactions classified INCOME / "
            "EXPENSE with BUSINESS nature). Own-account transfers, loan disbursements, personal credits, financing "
            "inflows, refunds, reversals and UNKNOWN credits are not operating income.",
            "Existing debt service = debits classified LOAN_REPAYMENT / INTEREST_PAYMENT, averaged over the complete "
            "months, and assumed to continue unchanged over the forecast months. Other recurring debits are not "
            "assumed to be loan EMIs.",
            "Loan terms are officer / user-provided inputs, not derived from documents.",
            f"Stress scenarios: business inflow -{d:.0%}, business outflow +{u:.0%}; debt service unchanged.",
            "PARTIAL months are excluded and missing months are not filled; no missing value is treated as zero.",
            "Descriptive analysis only - not a risk grade, an approval, a rejection or a credit recommendation.",
        ]
        if rep is not None:
            out.insert(3, f"Proposed repayment uses the full amortising installment {rep.installment} "
                          f"({rep.frequency.value.lower()}), converted to a monthly equivalent of "
                          f"{rep.monthly_equivalent}" + (f"; the first {rep.grace_periods} grace period(s) have lower "
                                                         "payments" if rep.grace_periods else "") + ".")
        return out
