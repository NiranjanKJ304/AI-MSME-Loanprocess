"""Forecast one series: data-quality checks -> seasonality -> model selection with backtest ->
forecast with (optional) uncertainty interval, assumptions and deterministic explanations.

Status of a forecast
  NOT_AVAILABLE   a quality check FAILED (e.g. too few consecutive complete observations) - no
                  forecast is manufactured
  LOW_CONFIDENCE  produced, but a check WARNED (short history, excluded conflicting / partial /
                  missing periods, outliers, low classification coverage, horizon gap ...)
  AVAILABLE       produced and every check passed
A forecast is a projection, never an actual: actuals, forecasts and uncertainty are kept apart.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.financial_health.explain import inr
from app.financials.periods import fiscal_year
from app.forecasting.config import ForecastConfig, SeriesConfig
from app.forecasting.models import (
    Selection,
    candidate_models,
    q_inr,
    robust_outliers,
    seasonal_strength,
    select_model,
)
from app.forecasting.series import Series
from app.models.enums import FactAvailability, ForecastFrequency

A = FactAvailability
PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

GENERAL_ASSUMPTIONS = [
    "A forecast is a statistical projection of past observations, not an actual and not a guaranteed value.",
    "No structural change (new contracts, lost customers, price or capacity changes) beyond what the history shows.",
    "Missing and PARTIAL periods were not filled in; only complete, non-conflicting observations were used.",
]
MONTHLY_ASSUMPTIONS = [
    "Bank transactions are classified correctly; UNKNOWN, transfer, loan, refund and reversal amounts are not "
    "business inflow / outflow, and bank credits in total are never treated as revenue.",
]


@dataclass
class TargetPeriod:
    period_key: str
    label: str
    start: date
    end: date
    horizon: int  # steps after the last training observation


@dataclass
class ResultRow:
    target: TargetPeriod
    value: Decimal | None
    lower: Decimal | None
    upper: Decimal | None
    status: FactAvailability
    note: str | None
    explanation: str = ""


@dataclass
class Outcome:
    series: Series
    status: FactAvailability
    reason: str | None
    checks: list[dict[str, Any]]
    seasonality: dict[str, Any]
    selection: Selection | None
    results: list[ResultRow] = field(default_factory=list)
    uncertainty: dict[str, Any] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)


def _targets(s: Series, cfg: SeriesConfig) -> list[TargetPeriod]:
    last, train_end = s.last_observed, s.training[-1] if s.training else None
    if last is None or train_end is None:
        return []
    out = []
    for step in range(1, cfg.horizon + 1):
        idx = last.index + step
        if s.frequency == ForecastFrequency.ANNUAL:
            fy = fiscal_year(idx)
            out.append(TargetPeriod(fy.key, fy.label, fy.start, fy.end, idx - train_end.index))
        else:
            y, m = divmod(idx, 12)
            m += 1
            out.append(TargetPeriod(f"{y}-{m:02d}", date(y, m, 1).strftime("%b %Y"), date(y, m, 1),
                                    date(y, m, calendar.monthrange(y, m)[1]), idx - train_end.index))
    return out


def _fmt(v: Decimal | None) -> str:
    return "not available" if v is None else inr(v)


class ForecastEngine:
    def __init__(self, config: ForecastConfig):
        self.cfg = config

    def series_config(self, s: Series) -> SeriesConfig:
        return self.cfg.annual if s.frequency == ForecastFrequency.ANNUAL else self.cfg.monthly

    # ------------------------------------------------------------------ quality
    def quality(self, s: Series, cfg: SeriesConfig) -> list[dict[str, Any]]:
        train = s.training
        n = len(train)
        unit = "annual" if s.frequency == ForecastFrequency.ANNUAL else "monthly"
        checks: list[dict[str, Any]] = []

        def chk(name: str, result: str, detail: str, **extra) -> None:
            checks.append({"check": name, "result": result, "detail": detail, **extra})

        chk("observations", FAIL if n < cfg.min_observations else PASS,
            f"{n} consecutive complete {unit} observation(s) available for training; at least "
            f"{cfg.min_observations} required", observations=n, periods_with_data=len(s.observations))
        if n >= cfg.min_observations:
            chk("history_length", WARN if n < cfg.low_confidence_below else PASS,
                f"only {n} historical {unit} observations (fewer than {cfg.low_confidence_below})"
                if n < cfg.low_confidence_below else f"{n} historical {unit} observations")
        gaps = [o.period_key for o in s.observations if o.exclusion_reason and o.exclusion_reason.startswith("not consecutive")]
        chk("consecutive_periods", WARN if gaps else PASS,
            f"earlier observation(s) {', '.join(gaps)} not used: not consecutive with the training run" if gaps
            else "training observations are consecutive", excluded=gaps)
        chk("missing_periods", WARN if s.missing_periods else PASS,
            f"no data for {', '.join(s.missing_periods)} (not filled in)" if s.missing_periods
            else "no missing periods between the first and last observation", periods=s.missing_periods)
        partial = [o.period_key for o in s.observations if o.source_status == A.PARTIAL]
        chk("partial_periods", WARN if partial else PASS,
            f"PARTIAL period(s) {', '.join(partial)} excluded (not complete observations)" if partial
            else "no partial periods", periods=partial)
        conflicting = [o.period_key for o in s.observations if o.source_status == A.CONFLICTING]
        chk("conflicting_facts", WARN if conflicting else PASS,
            f"CONFLICTING source facts for {', '.join(conflicting)}: observation(s) excluded, no value selected"
            if conflicting else "no conflicting source facts", periods=conflicting)
        if s.frequency == ForecastFrequency.ANNUAL:
            basis = [o.period_key for o in s.observations if o.exclusion_reason and "basis" in o.exclusion_reason]
            chk("comparable_basis", WARN if basis else PASS,
                f"{', '.join(basis)} excluded: revenue basis differs from '{s.basis}'" if basis
                else f"all observations on one basis ({s.basis})" if s.basis else "no basis", periods=basis)
        low = [o.period_key for o in train if o.source_status == A.LOW_CONFIDENCE]
        chk("low_confidence_observations", WARN if low else PASS,
            f"training includes LOW_CONFIDENCE observation(s): {', '.join(low)}" if low
            else "no low-confidence observations in training", periods=low)
        if s.frequency == ForecastFrequency.MONTHLY and train:
            m = self.cfg.monthly
            covs = [o.coverage for o in train if o.coverage is not None]
            cov = round(sum(covs) / len(covs), 4) if covs else None
            chk("classification_coverage", WARN if cov is not None and cov < m.min_classification_coverage else PASS,
                f"average classification coverage (by value) {cov:.1%} over training months"
                if cov is not None else "no transactions to classify", value=cov,
                threshold=m.min_classification_coverage)
            total = sum((o.total_value for o in train), Decimal(0))
            share = float(sum((o.unknown_value for o in train), Decimal(0)) / total) if total else None
            chk("unknown_transaction_share", WARN if share is not None and share > m.max_unknown_share else PASS,
                f"{share:.1%} of the relevant transaction value is UNKNOWN (could hide business activity)"
                if share is not None else "no transactions", value=None if share is None else round(share, 4),
                threshold=m.max_unknown_share)
        out_idx = robust_outliers([o.value for o in train], self.cfg.outlier_robust_z)
        for i in out_idx:
            train[i].outlier = True
        outliers = [train[i].period_key for i in out_idx]
        chk("outliers", WARN if outliers else PASS,
            f"outlier(s) {', '.join(outliers)} (robust z > {self.cfg.outlier_robust_z}); flagged, kept unchanged"
            if outliers else "no outliers (robust z-score)", periods=outliers)
        return checks

    def seasonality(self, s: Series) -> dict[str, Any]:
        if s.frequency == ForecastFrequency.ANNUAL:
            return {"status": "NOT_AVAILABLE", "reason": "annual series: seasonality applies to sub-annual data only"}
        m, train = self.cfg.monthly, s.training
        if len(train) < m.seasonality_min_months:
            return {"status": "NOT_AVAILABLE",
                    "reason": f"needs at least {m.seasonality_min_months} consecutive complete months (two yearly "
                              f"cycles); have {len(train)}"}
        strength = seasonal_strength([o.value for o in train], [o.month for o in train])
        detected = strength >= m.seasonality_strength_threshold
        return {"status": "DETECTED" if detected else "NOT_DETECTED", "strength": round(strength, 4),
                "threshold": m.seasonality_strength_threshold, "months_used": len(train),
                "method": "share of linearly detrended variance explained by month-of-year means"}

    # ------------------------------------------------------------------ forecast
    def forecast(self, s: Series) -> Outcome:
        cfg = self.series_config(s)
        checks = self.quality(s, cfg)
        seasonality = self.seasonality(s)
        failed = [c for c in checks if c["result"] == FAIL]
        if failed:
            reason = "; ".join(c["detail"] for c in failed)
            if not s.observations:
                reason = "no historical observations" + (
                    "" if s.frequency == ForecastFrequency.ANNUAL else " (no classified bank cash flow)")
            return Outcome(s, A.NOT_AVAILABLE, reason, checks, seasonality, None,
                           uncertainty={"interval": None, "reason": "no forecast"},
                           assumptions=[])

        train = s.training
        y, keys = [o.value for o in train], [o.period_key for o in train]
        models = candidate_models(cfg.moving_average_window, cfg.linear_trend_min_train,
                                  seasonality.get("status") == "DETECTED")
        selection = select_model(models, y, keys, cfg.min_backtest_points)
        model, bt = selection.model, selection.selected_backtest

        warns = [c for c in checks if c["result"] == WARN]
        targets = _targets(s, cfg)
        if targets and targets[0].horizon > 1:
            warns.append({"check": "forecast_horizon", "result": WARN,
                          "detail": f"latest period(s) not usable: {s.training[-1].period_key} is the last training "
                                    f"observation, so {targets[0].period_key} is {targets[0].horizon} steps ahead"})
            checks.append(warns[-1])
        status = A.LOW_CONFIDENCE if warns else A.AVAILABLE
        reason = "; ".join(c["detail"] for c in warns) or None

        points = bt.errors.points
        z = Decimal(str(self.cfg.interval_z))
        if points >= cfg.interval_min_backtest_points:
            uncertainty = {"interval": "approximate", "level": self.cfg.interval_level,
                           "method": f"forecast +/- {self.cfg.interval_z} x backtest RMSE x sqrt(steps ahead) "
                                     "(assumes roughly normal, stable errors)",
                           "backtest_rmse": str(bt.errors.rmse), "backtest_points": points}
        else:
            uncertainty = {"interval": None, "level": None,
                           "reason": f"only {points} backtest error(s); at least {cfg.interval_min_backtest_points} "
                                     "are needed for a meaningful interval - no interval is given"}
        results = []
        for t in targets:
            value = q_inr(model.predict(y, t.horizon))
            lower = upper = None
            note = None
            if s.non_negative and value < 0:
                results.append(ResultRow(t, None, None, None, A.NOT_AVAILABLE,
                                         f"{model.name} projects a negative {s.label.lower()} ({inr(value)}), which is "
                                         "not meaningful - no forecast given for this period"))
                continue
            if uncertainty["interval"]:
                half = q_inr(z * bt.errors.rmse * Decimal(t.horizon).sqrt())
                lower, upper = value - half, value + half
                if s.non_negative and lower < 0:
                    lower = Decimal("0.00")
                    note = "lower bound floored at 0 (the metric cannot be negative)"
            results.append(ResultRow(t, value, lower, upper, status, note))

        assumptions = [model.assumption] + GENERAL_ASSUMPTIONS + (
            MONTHLY_ASSUMPTIONS if s.frequency == ForecastFrequency.MONTHLY else
            [f"Revenue basis: {s.basis}." if s.basis else ""])
        out = Outcome(s, status, reason, checks, seasonality, selection, results, uncertainty,
                      [a for a in assumptions if a])
        for r in results:
            r.explanation = self.explain(out, r)
        return out

    def explain(self, o: Outcome, r: ResultRow) -> str:
        s, sel = o.series, o.selection
        train = s.training
        bt = sel.selected_backtest
        parts = [f"{s.label} forecast for {r.target.label} ({s.frequency.value.lower()}, {r.target.horizon} step(s) "
                 f"after the last observation): {_fmt(r.value)} - a projection, not an actual."]
        if r.value is None:
            parts.append(f"Not given: {r.note}.")
        parts.append(f"Model: {sel.model.name} ({sel.model.description}), selected by {sel.rule}.")
        parts.append(f"Trained on {len(train)} {s.frequency.value.lower()} observation(s) "
                     f"{train[0].period_key} - {train[-1].period_key}.")
        e = bt.errors
        parts.append(f"Backtest ({bt.test_period['start']} - {bt.test_period['end']}, {e.points} point(s)): "
                     f"MAE {_fmt(e.mae)}, RMSE {_fmt(e.rmse)}, "
                     + (f"MAPE {e.mape * 100:.2f}%." if e.mape is not None else f"{e.mape_note}."))
        if r.lower is not None:
            parts.append(f"Approximate {o.uncertainty['level']:.0%} interval: {_fmt(r.lower)} - {_fmt(r.upper)}"
                         + (f" ({r.note})" if r.note else "") + ".")
        elif r.value is not None:
            parts.append(f"No interval: {o.uncertainty.get('reason')}.")
        parts.append(f"Status: {r.status.value}" + (f" - {o.reason}" if o.reason else "") + ".")
        return " ".join(parts)
