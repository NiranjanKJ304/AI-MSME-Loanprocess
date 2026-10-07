"""Deterministic forecasting models, rolling-origin backtesting and model selection (pure Python,
Decimal arithmetic so hand-calculated values match exactly).

Models (the explicit, simple set - no machine learning):
  naive            next value = latest observed value
  moving_average   next value = mean of the last k observations
  linear_trend     ordinary least squares line y = a + b*t through the training points, extrapolated
  seasonal_naive   next value = the value 12 months earlier (monthly; only when seasonality is detected)

Backtest: rolling origin, one step ahead. For each test point t, every candidate is trained on
observations [0, t) only and predicts t - no future observation is ever used. All candidates are
scored on the same test points; the lowest MAE wins, ties go to the simpler model.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

INR_Q = Decimal("0.01")
RATIO_Q = Decimal("0.000001")
ORDER = ["naive", "moving_average", "linear_trend", "seasonal_naive"]  # simplest first (tie-break)


def q_inr(v: Decimal) -> Decimal:
    return v.quantize(INR_Q, rounding=ROUND_HALF_UP)


def q_ratio(v: Decimal) -> Decimal:
    return v.quantize(RATIO_Q, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Model:
    name: str
    min_train: int
    params: dict
    description: str
    assumption: str
    predict: Callable[[list[Decimal], int], Decimal]  # (training values, steps ahead >= 1) -> forecast


def _naive(y: list[Decimal], h: int) -> Decimal:
    return y[-1]


def _moving_average(k: int) -> Callable[[list[Decimal], int], Decimal]:
    def f(y: list[Decimal], h: int) -> Decimal:
        window = y[-k:]
        return sum(window, Decimal(0)) / Decimal(len(window))
    return f


def linear_fit(y: list[Decimal]) -> tuple[Decimal, Decimal]:
    """OLS intercept a and slope b for t = 0..n-1."""
    n = len(y)
    t_mean = Decimal(n - 1) / 2
    y_mean = sum(y, Decimal(0)) / n
    sxx = sum(((Decimal(t) - t_mean) ** 2 for t in range(n)), Decimal(0))
    sxy = sum(((Decimal(t) - t_mean) * (v - y_mean) for t, v in enumerate(y)), Decimal(0))
    b = sxy / sxx if sxx else Decimal(0)
    return y_mean - b * t_mean, b


def _linear(y: list[Decimal], h: int) -> Decimal:
    a, b = linear_fit(y)
    return a + b * Decimal(len(y) - 1 + h)


def _seasonal_naive(y: list[Decimal], h: int) -> Decimal:
    return y[len(y) - 12 + (h - 1) % 12]


def candidate_models(ma_window: int, linear_min_train: int, seasonal: bool) -> list[Model]:
    models = [
        Model("naive", 1, {}, "latest observed value",
              "the next period equals the latest complete observation", _naive),
        Model("moving_average", ma_window, {"window": ma_window}, f"mean of the last {ma_window} observations",
              f"the next period equals the average of the last {ma_window} complete observations",
              _moving_average(ma_window)),
        Model("linear_trend", linear_min_train, {"min_train": linear_min_train},
              "ordinary least squares straight line through the training observations",
              "the straight-line trend of the training period continues unchanged", _linear),
    ]
    if seasonal:
        models.append(Model("seasonal_naive", 12, {"season_length": 12}, "value of the same month one year earlier",
                            "each month repeats the same month of the previous year", _seasonal_naive))
    return models


# --------------------------------------------------------------------------- errors
@dataclass
class Errors:
    mae: Decimal | None
    rmse: Decimal | None
    mape: Decimal | None
    mape_note: str | None
    points: int


def error_metrics(actual: list[Decimal], predicted: list[Decimal]) -> Errors:
    if not actual:
        return Errors(None, None, None, "no test points", 0)
    errs = [a - p for a, p in zip(actual, predicted)]
    n = Decimal(len(errs))
    mae = sum((abs(e) for e in errs), Decimal(0)) / n
    rmse = (sum((e * e for e in errs), Decimal(0)) / n).sqrt()
    if all(a > 0 for a in actual):
        mape, note = q_ratio(sum((abs(e) / a for e, a in zip(errs, actual)), Decimal(0)) / n), None
    else:
        mape, note = None, "MAPE not valid: a test actual is zero or negative"
    return Errors(q_inr(mae), q_inr(rmse), mape, note, len(errs))


# --------------------------------------------------------------------------- backtest / selection
@dataclass
class Backtest:
    model: str
    params: dict
    training_period: dict  # first and last training window
    test_period: dict
    errors: Errors
    predictions: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        e = self.errors
        return {"model": self.model, "params": self.params, "training_period": self.training_period,
                "test_period": self.test_period, "test_points": e.points,
                "error_metrics": {"mae": _s(e.mae), "rmse": _s(e.rmse), "mape": _s(e.mape), "mape_note": e.mape_note},
                "predictions": self.predictions}


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


@dataclass
class Selection:
    model: Model | None
    backtests: list[Backtest]
    rejected: list[dict]
    rule: str
    selected_backtest: Backtest | None


def backtest(model: Model, y: list[Decimal], keys: list[str], start: int) -> Backtest:
    actual, pred, rows = [], [], []
    for t in range(start, len(y)):
        train = y[:t]  # strictly earlier observations only
        p = q_inr(model.predict(train, 1))
        actual.append(y[t])
        pred.append(p)
        rows.append({"period": keys[t], "actual": str(y[t]), "predicted": str(p), "error": str(y[t] - p),
                     "trained_on": {"start": keys[0], "end": keys[t - 1], "observations": t}})
    return Backtest(model.name, model.params,
                    {"start": keys[0], "end": keys[start - 1], "first_window_observations": start,
                     "last_window_end": keys[len(y) - 2]},
                    {"start": keys[start], "end": keys[-1]}, error_metrics(actual, pred), rows)


def select_model(models: list[Model], y: list[Decimal], keys: list[str], min_backtest_points: int) -> Selection:
    n = len(y)
    eligible = [m for m in models if m.min_train + min_backtest_points <= n]
    rejected = [{"model": m.name, "reason": f"needs {m.min_train} training + {min_backtest_points} backtest "
                                            f"observation(s); have {n}"} for m in models if m not in eligible]
    if not eligible:
        return Selection(None, [], rejected, "no model has enough history", None)
    start = max(m.min_train for m in eligible)  # same test points for every candidate
    tests = [backtest(m, y, keys, start) for m in eligible]
    best = min(tests, key=lambda b: (b.errors.mae, ORDER.index(b.model)))
    rule = (f"lowest backtest MAE over the same {n - start} one-step-ahead test point(s) "
            f"({keys[start]} - {keys[-1]}); ties go to the simpler model ({' < '.join(ORDER)})")
    return Selection(next(m for m in eligible if m.name == best.model), tests, rejected, rule, best)


# --------------------------------------------------------------------------- data checks
def robust_outliers(y: list[Decimal], z: float) -> list[int]:
    """Indexes with |x - median| / (1.4826 * MAD) > z. Flagged only - never removed or changed."""
    if len(y) < 4:
        return []
    vals = [float(v) for v in y]
    med = statistics.median(vals)
    mad = statistics.median(abs(v - med) for v in vals)
    if mad == 0:
        return []
    return [i for i, v in enumerate(vals) if abs(v - med) / (1.4826 * mad) > z]


def seasonal_strength(y: list[Decimal], months: list[int]) -> float:
    """Share of the detrended variance explained by month-of-year means (0..1)."""
    a, b = linear_fit(y)
    r = [float(v - (a + b * Decimal(t))) for t, v in enumerate(y)]
    var_r = statistics.pvariance(r)
    if var_r == 0:
        return 0.0
    by_month: dict[int, list[float]] = {}
    for m, v in zip(months, r):
        by_month.setdefault(m, []).append(v)
    means = {m: statistics.fmean(v) for m, v in by_month.items()}
    remainder = [v - means[m] for m, v in zip(months, r)]
    return max(0.0, 1.0 - statistics.pvariance(remainder) / var_r)
