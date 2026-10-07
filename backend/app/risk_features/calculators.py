"""Feature calculators - one per definition in definitions.py, registered by the same name.

Rules: values are copied from the source layer (with its status, period, confidence and provenance)
or calculated with the formula stated in the definition. A missing input gives NOT_AVAILABLE with
value None (never 0); a conflicting input gives CONFLICTING with value None; nothing is resolved here.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

from app.financial_health.formulas import Input
from app.financial_health.inputs import Snapshot
from app.models import CashflowMonthly
from app.models.enums import ClassificationStatus, FactAvailability
from app.requirements.document_requirements import evaluate_completeness
from app.risk_features.context import FeatureContext
from app.risk_features.definitions import BY_NAME, DEFINITIONS, FEATURE_CONFIG

A = FactAvailability
R6 = Decimal("0.000001")
R2 = Decimal("0.01")
_ORDER = [A.CONFLICTING, A.NOT_AVAILABLE, A.PARTIAL, A.LOW_CONFIDENCE, A.AVAILABLE]
CHAIN_MONTHS = ("risk_feature -> monthly cash-flow aggregates -> classified transactions -> canonical bank "
                "transactions -> bank statement rows -> document -> page/bbox")


def r6(v: Decimal) -> Decimal:
    return v.quantize(R6, rounding=ROUND_HALF_UP)


@dataclass
class FeatureValue:
    value: Decimal | str | None
    status: FactAvailability
    period: str | None = None
    confidence: float | None = None
    reason: str | None = None
    inputs: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


def na(reason: str, period: str | None = None) -> FeatureValue:
    return FeatureValue(None, A.NOT_AVAILABLE, period, reason=reason)


CALCULATORS: dict[str, Callable[[FeatureContext, dict[str, FeatureValue]], FeatureValue]] = {}


def calc(name: str):
    def deco(fn):
        if name in CALCULATORS:
            raise ValueError(f"duplicate calculator {name}")
        if name not in BY_NAME:
            raise ValueError(f"calculator without a definition: {name}")
        CALCULATORS[name] = fn
        return fn
    return deco


# --------------------------------------------------------------------------- source helpers
def from_health(ctx: FeatureContext, metric: str) -> FeatureValue:
    if ctx.latest_fy is None:
        return na("no financial statement / tax facts for any financial year")
    key = ctx.latest_fy.period_key
    m = ctx.health_metric(metric, key)
    if m is None:
        return na(f"financial health metric '{metric}' not available for {key} (health not built or no such "
                  "metric)", key)
    p = m.provenance or {}
    return FeatureValue(
        m.value, m.status, m.period_key, m.confidence, m.reason,
        inputs=[{"source": "financial_health_metrics", "id": str(m.id), "metric": m.metric, "period_key": m.period_key,
                 "period_kind": m.period_kind.value, "value": None if m.value is None else str(m.value),
                 "status": m.status.value, "formula": m.formula}],
        provenance={"chain": "risk_feature -> financial health metric -> " + p.get("chain", "").replace(
            "health metric -> ", ""), "source_records": [{"table": "financial_health_metrics", "id": str(m.id)}],
            "fact_ids": p.get("fact_ids", []), "conflict_ids": p.get("conflict_ids", []),
            "monthly_aggregate_ids": p.get("monthly_aggregate_ids", []),
            "transaction_ids": p.get("transaction_ids", [])},
        details={"period_kind": m.period_kind.value, "health_metric_reason": m.reason})


def from_fact(ctx: FeatureContext, metric: str) -> FeatureValue:
    if ctx.latest_fy is None:
        return na("no financial statement / tax facts for any financial year")
    inp: Input = ctx.s.fact_input(ctx.latest_fy, [metric], metric, metric)
    value = inp.value if inp.status not in (A.CONFLICTING, A.NOT_AVAILABLE) else None
    return FeatureValue(
        value, inp.status, ctx.latest_fy.period_key, inp.confidence, inp.reason,
        inputs=[inp.as_dict()],
        provenance={"chain": "risk_feature -> financial facts -> extracted fields -> document -> page/bbox",
                    "source_records": [{"table": "financial_facts", "id": s["fact_id"]} for s in inp.sources],
                    "fact_ids": [s["fact_id"] for s in inp.sources],
                    "conflict_ids": [c["conflict_id"] for c in inp.conflicts],
                    "fields": [{"field_id": s.get("field_id"), "document_code": s.get("document_code"),
                                "page": s.get("page"), "bbox": s.get("bbox")} for s in inp.sources]},
        details={"notes": inp.notes} if inp.notes else {})


def window_status(ctx: FeatureContext) -> tuple[FactAvailability, str | None]:
    if not ctx.complete:
        return A.NOT_AVAILABLE, "no complete month of classified bank cash flow"
    if ctx.partial or ctx.missing:
        return A.PARTIAL, ("bank window has PARTIAL / missing months: "
                           + ", ".join([m.month for m in ctx.partial] + ctx.missing))
    low = [m.month for m in ctx.complete if m.availability == A.LOW_CONFIDENCE]
    if low:
        return A.LOW_CONFIDENCE, "low classification coverage in " + ", ".join(low)
    return A.AVAILABLE, None


def from_months(ctx: FeatureContext, value: Decimal | None, buckets: list[str], *, reason_if_none: str | None = None,
                months: list[CashflowMonthly] | None = None, details: dict | None = None) -> FeatureValue:
    status, reason = window_status(ctx)
    if status == A.NOT_AVAILABLE:
        return na(reason, ctx.window_key)
    if value is None:
        return na(reason_if_none or "not calculable", ctx.window_key)
    used = months if months is not None else ctx.complete
    confs = [m.confidence for m in used if m.confidence is not None]
    srcs = [Snapshot.month_source(m, buckets) for m in used]
    return FeatureValue(
        value, status, ctx.window_key, round(sum(confs) / len(confs), 3) if confs else None, reason,
        inputs=[{"source": "cashflow_monthly", "id": s["monthly_aggregate_id"], "month": s["month"],
                 "values": s["values"], "availability": s["availability"]} for s in srcs],
        provenance={"chain": CHAIN_MONTHS, "monthly_aggregate_ids": [s["monthly_aggregate_id"] for s in srcs],
                    "transaction_ids": sorted({t for s in srcs for t in s["transaction_ids"]})},
        details={"months_used": [m.month for m in used], "partial_months_excluded": [m.month for m in ctx.partial],
                 "missing_months": ctx.missing, **(details or {})})


def _sum(months, *cols) -> Decimal:
    return sum((getattr(m, c) for m in months for c in cols), Decimal(0))


def _mean(vals: list[Decimal]) -> Decimal:
    return (sum(vals, Decimal(0)) / Decimal(len(vals))).quantize(R2, rounding=ROUND_HALF_UP)


def share(num: Decimal, den: Decimal) -> Decimal | None:
    return r6(num / den) if den > 0 else None


# --------------------------------------------------------------------------- A. business financials
for _name, _metric in (("revenue", "revenue"), ("revenue_growth", "revenue_yoy_growth"),
                       ("gross_margin", "gross_margin"), ("profit_margin", "net_margin"),
                       ("current_ratio", "current_ratio"), ("debt_to_equity", "debt_to_net_worth"),
                       ("total_debt", "borrowings"), ("cash_balance", "cash_and_bank"),
                       ("operating_cash_flow", "net_business_cash_flow"), ("revenue_trend", "revenue_trend"),
                       ("profit_trend", "profit_trend")):
    calc(_name)(lambda ctx, done, _m=_metric: from_health(ctx, _m))

for _name, _metric in (("gross_profit", "gross_profit"), ("net_profit", "pat"), ("net_worth", "net_worth")):
    calc(_name)(lambda ctx, done, _m=_metric: from_fact(ctx, _m))


# --------------------------------------------------------------------------- B. transaction features
@calc("average_monthly_business_inflow")
def _avg_in(ctx, done):
    return from_months(ctx, _mean([m.business_inflow for m in ctx.complete]) if ctx.complete else None,
                       ["business_inflow"])


@calc("average_monthly_business_outflow")
def _avg_out(ctx, done):
    return from_months(ctx, _mean([m.business_outflow for m in ctx.complete]) if ctx.complete else None,
                       ["business_outflow"])


@calc("average_monthly_business_cash_flow")
def _avg_net(ctx, done):
    return from_months(ctx, _mean([m.net_operating_cash_flow for m in ctx.complete]) if ctx.complete else None,
                       ["business_inflow", "business_outflow"])


def _month_ratio(ctx, pred, buckets):
    hits = [m for m in ctx.complete if pred(m)]
    v = r6(Decimal(len(hits)) / Decimal(len(ctx.complete))) if ctx.complete else None
    return from_months(ctx, v, buckets, details={"matching_months": [m.month for m in hits],
                                                 "complete_months": len(ctx.complete)})


@calc("positive_cash_flow_month_ratio")
def _pos(ctx, done):
    return _month_ratio(ctx, lambda m: m.net_operating_cash_flow > 0, ["business_inflow", "business_outflow"])


@calc("negative_cash_flow_month_ratio")
def _neg(ctx, done):
    return _month_ratio(ctx, lambda m: m.net_operating_cash_flow < 0, ["business_inflow", "business_outflow"])


@calc("profitable_month_ratio")
def _profitable(ctx, done):
    return _month_ratio(ctx, lambda m: m.business_inflow - m.business_outflow - m.financing_outflow > 0,
                        ["business_inflow", "business_outflow", "financing_outflow"])


def _growth(ctx, col):
    n, need = len(ctx.complete), FEATURE_CONFIG["min_months_growth"]
    if n < need:
        return from_months(ctx, None, [col], reason_if_none=f"needs at least {need} complete months (have {n})")
    h = n // 2
    old, new = [getattr(m, col) for m in ctx.complete[:h]], [getattr(m, col) for m in ctx.complete[-h:]]
    base = sum(old, Decimal(0)) / h
    if base <= 0:
        return from_months(ctx, None, [col], reason_if_none="mean of the oldest half is zero or negative")
    newest = sum(new, Decimal(0)) / h
    return from_months(ctx, r6((newest - base) / base), [col],
                       details={"oldest_half": [m.month for m in ctx.complete[:h]],
                                "newest_half": [m.month for m in ctx.complete[-h:]],
                                "oldest_mean": str(r6(base)), "newest_mean": str(r6(newest))})


@calc("business_inflow_growth")
def _in_growth(ctx, done):
    return _growth(ctx, "business_inflow")


@calc("business_outflow_growth")
def _out_growth(ctx, done):
    return _growth(ctx, "business_outflow")


def _ratio_feature(ctx, num_cols, den_cols, what):
    num, den = _sum(ctx.complete, *num_cols), _sum(ctx.complete, *den_cols)
    return from_months(ctx, share(num, den), list(num_cols) + list(den_cols),
                       reason_if_none=f"no {what} in the complete months (denominator is 0)",
                       details={"numerator": str(num), "denominator": str(den)})


@calc("unknown_credit_ratio")
def _unk_cr(ctx, done):
    return _ratio_feature(ctx, ["unknown_inflow"], ["total_inflow"], "credits")


@calc("unknown_debit_ratio")
def _unk_dr(ctx, done):
    return _ratio_feature(ctx, ["unknown_outflow"], ["total_outflow"], "debits")


@calc("transfer_ratio")
def _transfer(ctx, done):
    return _ratio_feature(ctx, ["transfer_inflow", "transfer_outflow"], ["total_inflow", "total_outflow"],
                          "transactions")


@calc("financing_inflow_ratio")
def _fin_in(ctx, done):
    return _ratio_feature(ctx, ["financing_inflow"], ["total_inflow"], "credits")


@calc("business_transaction_coverage")
def _biz_cov(ctx, done):
    return _ratio_feature(ctx, ["business_inflow", "business_outflow"], ["total_inflow", "total_outflow"],
                          "transactions")


@calc("transaction_classification_coverage")
def _cls_cov(ctx, done):
    total = _sum(ctx.complete, "total_inflow", "total_outflow")
    unknown = _sum(ctx.complete, "unknown_inflow", "unknown_outflow")
    return from_months(ctx, share(total - unknown, total), ["unknown_inflow", "unknown_outflow"],
                       reason_if_none="no transactions in the complete months",
                       details={"total_value": str(total), "unknown_value": str(unknown)})


def _classified(ctx) -> FeatureValue | None:
    if not ctx.s.classifications:
        return na("no classified bank transactions")
    return None


@calc("recurring_obligation_count")
def _recurring(ctx, done):
    if (x := _classified(ctx)) is not None:
        return x
    types = set(FEATURE_CONFIG["recurring_obligation_types"])
    pats = sorted((p for p in ctx.s.patterns.values() if p.direction == "DEBIT" and p.pattern_type in types),
                  key=lambda p: str(p.id))
    return FeatureValue(Decimal(len(pats)), A.AVAILABLE, None, None, None,
                        inputs=[{"source": "recurring_patterns", "id": str(p.id), "type": p.pattern_type,
                                 "counterparty": p.counterparty, "occurrences": p.occurrences} for p in pats],
                        provenance={"chain": "risk_feature -> recurring patterns -> classified transactions -> "
                                             "statement rows -> document -> page/bbox",
                                    "source_records": [{"table": "recurring_patterns", "id": str(p.id)} for p in pats],
                                    "transaction_ids": sorted({t for p in pats for t in p.transaction_ids})},
                        details={"by_type": {t: sum(1 for p in pats if p.pattern_type == t) for t in sorted(types)}})


@calc("reversal_count")
def _reversals(ctx, done):
    if (x := _classified(ctx)) is not None:
        return x
    pairs = sorted(tid for tid, c in ctx.s.classifications.items() if c.link_type in ("REVERSAL_OF", "REFUND_OF"))
    return FeatureValue(Decimal(len(pairs)), A.AVAILABLE, None, None, None,
                        inputs=[{"source": "transaction_classifications", "transaction_id": t} for t in pairs],
                        provenance={"chain": "risk_feature -> classified transactions -> statement rows -> document "
                                             "-> page/bbox", "transaction_ids": pairs})


@calc("unknown_transaction_ratio")
def _unk_count(ctx, done):
    if (x := _classified(ctx)) is not None:
        return x
    cls = ctx.s.classifications
    unknown = sorted(t for t, c in cls.items() if c.status == ClassificationStatus.UNKNOWN)
    return FeatureValue(r6(Decimal(len(unknown)) / Decimal(len(cls))), A.AVAILABLE, None, None, None,
                        inputs=[{"unknown": len(unknown), "classified_transactions": len(cls)}],
                        provenance={"chain": "risk_feature -> classified transactions -> statement rows -> document "
                                             "-> page/bbox", "transaction_ids": unknown})


# --------------------------------------------------------------------------- C. stability
@calc("cash_flow_trend")
def _cf_trend(ctx, done):
    n, need = len(ctx.complete), FEATURE_CONFIG["min_months_trend"]
    if n < need:
        return from_months(ctx, None, ["business_inflow", "business_outflow"],
                           reason_if_none=f"needs at least {need} complete months (have {n})")
    x = [Decimal(m.month_start.year * 12 + m.month_start.month) for m in ctx.complete]
    y = [m.net_operating_cash_flow for m in ctx.complete]
    xm, ym = sum(x, Decimal(0)) / n, sum(y, Decimal(0)) / n
    sxx = sum(((a - xm) ** 2 for a in x), Decimal(0))
    slope = sum(((a - xm) * (b - ym) for a, b in zip(x, y)), Decimal(0)) / sxx
    return from_months(ctx, slope.quantize(R2, rounding=ROUND_HALF_UP), ["business_inflow", "business_outflow"],
                       details={"method": "OLS slope against calendar month index (gaps keep their spacing)"})


def _cv(ctx, values: list[Decimal], denom_values: list[Decimal], buckets, what):
    need = FEATURE_CONFIG["min_months_volatility"]
    if len(values) < need:
        return from_months(ctx, None, buckets, reason_if_none=f"needs at least {need} complete months "
                                                              f"(have {len(values)})")
    mean = statistics.fmean(float(v) for v in denom_values)
    if mean <= 0:
        return from_months(ctx, None, buckets, reason_if_none=f"mean {what} is zero or negative")
    sd = statistics.pstdev([float(v) for v in values])
    return from_months(ctx, r6(Decimal(str(sd)) / Decimal(str(mean))), buckets,
                       details={"std_dev": round(sd, 2), "mean_denominator": round(mean, 2)})


@calc("inflow_volatility")
def _vol_in(ctx, done):
    v = [m.business_inflow for m in ctx.complete]
    return _cv(ctx, v, v, ["business_inflow"], "business inflow")


@calc("outflow_volatility")
def _vol_out(ctx, done):
    v = [m.business_outflow for m in ctx.complete]
    return _cv(ctx, v, v, ["business_outflow"], "business outflow")


@calc("cash_flow_volatility")
def _vol_cf(ctx, done):
    return _cv(ctx, [m.net_operating_cash_flow for m in ctx.complete], [m.business_inflow for m in ctx.complete],
               ["business_inflow", "business_outflow"], "business inflow")


@calc("partial_month_ratio")
def _partial_ratio(ctx, done):
    if not ctx.window:
        return na("no bank cash flow")
    srcs = [Snapshot.month_source(m, []) for m in ctx.window]
    return FeatureValue(r6(Decimal(len(ctx.partial)) / Decimal(len(ctx.window))), A.AVAILABLE, ctx.window_key,
                        inputs=[{"partial_months": [m.month for m in ctx.partial], "months_with_data": len(ctx.window)}],
                        provenance={"chain": CHAIN_MONTHS,
                                    "monthly_aggregate_ids": [s["monthly_aggregate_id"] for s in srcs]})


@calc("partial_period_count")
def _partial_count(ctx, done):
    if not ctx.window:
        return na("no bank cash flow")
    return FeatureValue(Decimal(len(ctx.partial) + len(ctx.missing)), A.AVAILABLE, ctx.window_key,
                        inputs=[{"partial_months": [m.month for m in ctx.partial], "missing_months": ctx.missing}],
                        provenance={"chain": CHAIN_MONTHS,
                                    "monthly_aggregate_ids": [str(m.id) for m in ctx.window]})


# --------------------------------------------------------------------------- D. forecast
def _forecast_prov(run, result=None) -> dict:
    p = run.provenance or {}
    return {"chain": "risk_feature -> forecast run / result -> historical observations -> " + p.get("chain", "").split(
        "historical observations -> ", 1)[-1],
        "source_records": [{"table": "forecast_runs", "id": str(run.id)}]
        + ([{"table": "forecast_results", "id": str(result.id)}] if result else []),
        "training_observation_ids": p.get("training_observation_ids", []), "fact_ids": p.get("fact_ids", []),
        "monthly_aggregate_ids": p.get("monthly_aggregate_ids", []), "transaction_ids": p.get("transaction_ids", [])}


def _run(ctx, metric):
    run = ctx.runs.get(metric)
    if run is None:
        return None, na("forecasts not built")
    if run.status == A.NOT_AVAILABLE:
        return None, na(f"{metric} forecast NOT_AVAILABLE: {run.reason}")
    return run, None


def _forecast_value(ctx, metric):
    run, err = _run(ctx, metric)
    if err:
        return err
    res = next((r for r in ctx.results.get(metric, []) if r.predicted_value is not None), None)
    if res is None:
        return na(f"{metric} forecast has no usable value")
    return FeatureValue(res.predicted_value, run.status, res.period_key, None, run.reason,
                        inputs=[{"source": "forecast_results", "id": str(res.id), "period_key": res.period_key,
                                 "horizon": res.horizon, "model": res.model, "value": str(res.predicted_value)}],
                        provenance=_forecast_prov(run, res), details={"model": run.model, "projection": True})


for _name, _metric in (("forecast_revenue", "revenue"), ("forecast_business_inflow", "business_inflow"),
                       ("forecast_business_outflow", "business_outflow"),
                       ("forecast_net_cash_flow", "net_business_cash_flow")):
    calc(_name)(lambda ctx, done, _m=_metric: _forecast_value(ctx, _m))


def _backtest_metric(ctx, key):
    run, err = _run(ctx, "business_inflow")
    if err:
        return err
    em = (run.backtest or {}).get("error_metrics") or {}
    if em.get(key) is None:
        return na(em.get("mape_note") if key == "mape" else f"backtest {key} not available")
    bt = run.backtest
    return FeatureValue(Decimal(em[key]), run.status, f"{bt['test_period']['start']}..{bt['test_period']['end']}",
                        None, run.reason,
                        inputs=[{"source": "forecast_runs", "id": str(run.id), "model": run.model,
                                 "test_points": bt.get("test_points")}],
                        provenance=_forecast_prov(run), details={"model": run.model})


for _name, _key in (("forecast_error_mae", "mae"), ("forecast_error_rmse", "rmse"), ("forecast_mape", "mape")):
    calc(_name)(lambda ctx, done, _k=_key: _backtest_metric(ctx, _k))


@calc("forecast_interval_width")
def _interval(ctx, done):
    run, err = _run(ctx, "business_inflow")
    if err:
        return err
    res = next((r for r in ctx.results.get("business_inflow", []) if r.predicted_value is not None), None)
    if res is None or res.lower_bound is None or res.upper_bound is None:
        return na((run.uncertainty or {}).get("reason") or "no interval for the first forecast")
    return FeatureValue(res.upper_bound - res.lower_bound, run.status, res.period_key, None, run.reason,
                        inputs=[{"source": "forecast_results", "id": str(res.id), "lower": str(res.lower_bound),
                                 "upper": str(res.upper_bound), "level": res.interval_level}],
                        provenance=_forecast_prov(run, res))


@calc("forecast_data_quality")
def _fq(ctx, done):
    if not ctx.runs:
        return na("forecasts not built")
    worst = min((r.status for r in ctx.runs.values()), key=_ORDER.index)
    return FeatureValue(worst.value, A.AVAILABLE, None,
                        inputs=[{"metric": r.metric, "status": r.status.value, "reason": r.reason}
                                for r in sorted(ctx.runs.values(), key=lambda r: r.metric)],
                        provenance={"chain": "risk_feature -> forecast runs (data-quality checks)",
                                    "source_records": [{"table": "forecast_runs", "id": str(r.id)}
                                                       for r in sorted(ctx.runs.values(), key=lambda r: r.metric)]})


@calc("forecast_model")
def _fmodel(ctx, done):
    run, err = _run(ctx, "business_inflow")
    if err:
        return err
    return FeatureValue(run.model, A.AVAILABLE, None, inputs=[{"source": "forecast_runs", "id": str(run.id)}],
                        provenance={"chain": "risk_feature -> forecast run (model selection)",
                                    "source_records": [{"table": "forecast_runs", "id": str(run.id)}],
                                    "selection_rule": (run.selection or {}).get("rule")})


# --------------------------------------------------------------------------- E. repayment
def _terms_feature(ctx, attr):
    t = ctx.terms
    if t is None:
        return na("no proposed loan terms provided")
    v = getattr(t, attr)
    return FeatureValue(Decimal(v), A.AVAILABLE, None,
                        inputs=[{"source": "repayment_loan_terms", "id": str(t.id), attr: str(v),
                                 "provided_by": t.provided_by, "input_source": t.source}],
                        provenance={"chain": "risk_feature -> loan terms (user / officer-provided input)",
                                    "source_records": [{"table": "repayment_loan_terms", "id": str(t.id)}],
                                    "user_provided_inputs": [{"loan_terms_id": str(t.id), "source": t.source,
                                                              "provided_by": t.provided_by}]})


for _name, _attr in (("requested_loan_amount", "requested_amount"), ("annual_interest_rate", "annual_interest_rate"),
                     ("tenure_months", "tenure_months")):
    calc(_name)(lambda ctx, done, _a=_attr: _terms_feature(ctx, _a))


@calc("proposed_periodic_payment")
def _installment(ctx, done):
    a = ctx.analysis
    if a is None or not a.repayment:
        return na("no repayment calculation (loan terms not provided or analysis not built)")
    rep = a.repayment
    return FeatureValue(Decimal(rep["installment"]), A.AVAILABLE, rep["frequency"],
                        inputs=[{"source": "repayment_analyses", "id": str(a.id), "installment": rep["installment"],
                                 "formula": rep["formula"], "inputs": rep["inputs"]}],
                        provenance={"chain": "risk_feature -> repayment calculation -> loan terms (user-provided)",
                                    "source_records": [{"table": "repayment_analyses", "id": str(a.id)}],
                                    "user_provided_inputs": [{"loan_terms_id": str(a.loan_terms_id)}]})


def _rep_metric(ctx, basis, metric):
    if ctx.analysis is None:
        return na("repayment capacity not built")
    m = ctx.rep_metric(basis, metric)
    if m is None:
        return na(f"repayment metric {basis}.{metric} not found")
    p = m.provenance or {}
    return FeatureValue(
        m.value, m.status, m.period, None, m.reason,
        inputs=[{"source": "repayment_capacity_metrics", "id": str(m.id), "basis": m.basis, "metric": m.metric,
                 "value": None if m.value is None else str(m.value), "status": m.status.value, "formula": m.formula}],
        provenance={"chain": "risk_feature -> " + p.get("chain", ""),
                    "source_records": [{"table": "repayment_capacity_metrics", "id": str(m.id)}],
                    **{k: p.get(k, []) for k in ("fact_ids", "monthly_aggregate_ids", "transaction_ids",
                                                 "forecast_result_ids", "user_provided_inputs")}},
        details={"basis": basis})


for _name, _basis, _metric in (("existing_debt_service", "HISTORICAL", "existing_debt_service"),
                               ("proposed_debt_service", "HISTORICAL", "proposed_debt_service"),
                               ("total_debt_service", "HISTORICAL", "total_debt_service"),
                               ("historical_dscr", "HISTORICAL", "dscr"),
                               ("forecast_dscr", "FORECAST", "dscr"),
                               ("statement_dscr", "STATEMENT", "statement_dscr"),
                               ("post_debt_service_cash_flow", "HISTORICAL", "post_debt_service_cash_flow")):
    calc(_name)(lambda ctx, done, _b=_basis, _m=_metric: _rep_metric(ctx, _b, _m))


def _stress(ctx, name):
    if ctx.analysis is None:
        return na("repayment capacity not built")
    s = ctx.scenario("HISTORICAL", name)
    if s is None:
        return na(f"scenario {name} not found")
    value = s.dscr if s.status not in (A.CONFLICTING, A.NOT_AVAILABLE) else None
    if value is None and s.status not in (A.CONFLICTING, A.NOT_AVAILABLE):
        return na(s.reason or "DSCR not calculable for this scenario")
    hist = ctx.rep_metric("HISTORICAL", "dscr")
    return FeatureValue(value, s.status, hist.period if hist else None, None, s.reason,
                        inputs=[{"source": "repayment_scenarios", "id": str(s.id), "scenario": s.scenario,
                                 "assumption": s.assumption, "cash_available": str(s.cash_available),
                                 "total_debt_service": str(s.total_debt_service)}],
                        provenance={"chain": "risk_feature -> repayment stress scenario -> repayment metrics -> "
                                             "monthly cash-flow aggregates -> classified transactions -> statement rows",
                                    "source_records": [{"table": "repayment_scenarios", "id": str(s.id)}],
                                    "monthly_aggregate_ids": (ctx.analysis.provenance or {}).get(
                                        "monthly_aggregate_ids", [])})


for _name, _scenario in (("stress_dscr_revenue_down", "REVENUE_DOWN"), ("stress_dscr_expense_up", "EXPENSE_UP"),
                         ("stress_dscr_combined", "COMBINED_STRESS")):
    calc(_name)(lambda ctx, done, _s=_scenario: _stress(ctx, _s))


@calc("repayment_data_quality")
def _rq(ctx, done):
    a = ctx.analysis
    if a is None:
        return na("repayment capacity not built")
    warns = [c["check"] for c in a.data_quality or [] if c.get("result") in ("WARN", "FAIL")]
    return FeatureValue(a.status.value, A.AVAILABLE, None,
                        inputs=[{"source": "repayment_analyses", "id": str(a.id), "status": a.status.value,
                                 "checks_not_passed": warns}],
                        provenance={"chain": "risk_feature -> repayment analysis (data-quality checks)",
                                    "source_records": [{"table": "repayment_analyses", "id": str(a.id)}]})


# --------------------------------------------------------------------------- F. document / data quality
@calc("document_completeness")
def _completeness(ctx, done):
    report = evaluate_completeness(ctx.app, ctx.documents)
    s = report["summary"]
    if not s["required_total"]:
        return na("the requirement policy has no required documents for this application")
    return FeatureValue(r6(Decimal(s["required_received"]) / Decimal(s["required_total"])), A.AVAILABLE, None,
                        inputs=[{"required_total": s["required_total"], "required_received": s["required_received"],
                                 "missing_document_types": s["missing_document_types"]}],
                        provenance={"chain": "risk_feature -> requirement checklist -> documents",
                                    "document_ids": [str(d.id) for d in ctx.documents]})


@calc("extraction_confidence")
def _extraction(ctx, done):
    present = [f for f in ctx.fields if not f.is_missing]
    if not present:
        return na("no extracted fields")
    mean = sum((Decimal(str(f.confidence)) for f in present), Decimal(0)) / Decimal(len(present))
    return FeatureValue(r6(mean), A.AVAILABLE, None, inputs=[{"fields": len(present)}],
                        provenance={"chain": "risk_feature -> extracted fields -> document -> page/bbox",
                                    "field_ids": sorted(str(f.id) for f in present)})


@calc("financial_fact_coverage")
def _fact_cov(ctx, done):
    if ctx.latest_fy is None:
        return na("no financial statement / tax facts for any financial year")
    p, core = ctx.latest_fy, FEATURE_CONFIG["core_financial_metrics"]
    states, covered, fact_ids = {}, [], []
    for m in core:
        inp = ctx.s.revenue_input(p) if m == "revenue" else ctx.s.fact_input(p, [m], m, m)
        states[m] = inp.status.value
        if inp.status in (A.AVAILABLE, A.LOW_CONFIDENCE) and inp.value is not None:
            covered.append(m)  # CONFLICTING / PARTIAL / NOT_AVAILABLE are not coverage
        fact_ids += [s["fact_id"] for s in inp.sources]
    return FeatureValue(r6(Decimal(len(covered)) / Decimal(len(core))), A.AVAILABLE, p.period_key,
                        inputs=[{"metric_states": states}],
                        provenance={"chain": "risk_feature -> financial facts -> extracted fields -> document -> "
                                             "page/bbox", "fact_ids": sorted(set(fact_ids))},
                        details={"covered": covered, "not_covered": [m for m in core if m not in covered]})


def _facts_present(ctx) -> FeatureValue | None:
    return None if ctx.s.facts else na("no financial facts")


@calc("conflicting_fact_count")
def _conflicts(ctx, done):
    if (x := _facts_present(ctx)) is not None:
        return x
    ids = sorted(str(c.id) for c in ctx.s.conflicts)
    return FeatureValue(Decimal(len(ids)), A.AVAILABLE, None,
                        inputs=[{"metric": c.metric, "value_a": str(c.value_a), "value_b": str(c.value_b)}
                                for c in sorted(ctx.s.conflicts, key=lambda c: str(c.id))],
                        provenance={"chain": "risk_feature -> financial conflicts -> financial facts -> extracted "
                                             "fields -> document -> page/bbox", "conflict_ids": ids})


@calc("low_confidence_fact_count")
def _low_facts(ctx, done):
    if (x := _facts_present(ctx)) is not None:
        return x
    ids = sorted(str(f.id) for f in ctx.s.facts if f.availability == A.LOW_CONFIDENCE)
    return FeatureValue(Decimal(len(ids)), A.AVAILABLE, None, inputs=[{"low_confidence_facts": len(ids)}],
                        provenance={"chain": "risk_feature -> financial facts -> extracted fields -> document -> "
                                             "page/bbox", "fact_ids": ids})


@calc("missing_required_data_count")
def _missing_required(ctx, done):
    required = [d.name for d in DEFINITIONS if d.required]
    missing = [n for n in required if done[n].status == A.NOT_AVAILABLE]
    return FeatureValue(Decimal(len(missing)), A.AVAILABLE, None,
                        inputs=[{"required_features": required, "not_available": missing}],
                        provenance={"chain": "risk_feature -> other risk features of this snapshot",
                                    "features": missing},
                        details={"reasons": {n: done[n].reason for n in missing}})


# --------------------------------------------------------------------------- registry check
_missing_calc = [d.name for d in DEFINITIONS if d.name not in CALCULATORS]
if _missing_calc:
    raise ValueError(f"feature definitions without a calculator: {_missing_calc}")
# dependent features run last, in definition order
DEPENDENT = {"missing_required_data_count"}


def compute_all(ctx: FeatureContext) -> dict[str, FeatureValue]:
    done: dict[str, FeatureValue] = {}
    for d in DEFINITIONS:
        if d.name not in DEPENDENT:
            done[d.name] = CALCULATORS[d.name](ctx, done)
    for d in DEFINITIONS:
        if d.name in DEPENDENT:
            done[d.name] = CALCULATORS[d.name](ctx, done)
    return {d.name: done[d.name] for d in DEFINITIONS}
