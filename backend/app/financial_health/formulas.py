"""Transparent, pure formulas for financial health metrics.

Every calculation takes `Input`s (value + status + sources) and returns a `Calc` (value + status +
reason). Rules applied everywhere:

  * an input that is CONFLICTING makes the result CONFLICTING - no value is picked;
  * an input that is NOT_AVAILABLE makes the result NOT_AVAILABLE - missing is never zero;
  * an input that is PARTIAL makes the result PARTIAL; a value is produced only when the PARTIAL
    input carries an observed value (e.g. a sum over the months a statement covers), and the
    result stays explicitly PARTIAL;
  * a LOW_CONFIDENCE input gives a LOW_CONFIDENCE result;
  * a zero denominator / zero base is never divided by (NOT_AVAILABLE with the reason), and a
    negative denominator or base is rejected where the ratio would not be meaningful.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.models.enums import FactAvailability

A = FactAvailability
RATIO_Q = Decimal("0.000001")
INR_Q = Decimal("0.01")

# result precedence when inputs differ (first wins)
_PRECEDENCE = [A.CONFLICTING, A.NOT_AVAILABLE, A.PARTIAL, A.LOW_CONFIDENCE, A.AVAILABLE]


@dataclass
class Input:
    name: str  # formula variable, e.g. "pat"
    label: str
    value: Decimal | None
    status: FactAvailability
    period_key: str | None = None
    unit: str = "INR"
    basis: str | None = None  # where the value comes from (e.g. "P&L revenue from operations")
    reason: str | None = None
    confidence: float | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)  # facts / monthly aggregates / transactions
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_value(self) -> bool:
        return self.value is not None and self.status != A.CONFLICTING

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "label": self.label, "value": None if self.value is None else str(self.value),
            "unit": self.unit, "status": self.status.value, "period_key": self.period_key, "basis": self.basis,
            "reason": self.reason, "confidence": self.confidence, "sources": self.sources,
            "conflicts": self.conflicts, "notes": self.notes,
        }


@dataclass
class Calc:
    value: Decimal | None
    status: FactAvailability
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def combined_status(inputs: list[Input]) -> tuple[FactAvailability, str | None]:
    """Worst status among inputs and the reason that goes with it."""
    for s in _PRECEDENCE:
        hit = [i for i in inputs if i.status == s]
        if hit and s != A.AVAILABLE:
            if s == A.CONFLICTING:
                return s, "conflicting sources for " + ", ".join(i.label for i in hit) + " - no value selected"
            if s == A.NOT_AVAILABLE:
                return s, "missing input: " + "; ".join(f"{i.label} ({i.reason or 'not available'})" for i in hit)
            if s == A.PARTIAL:
                return s, "partial input: " + "; ".join(f"{i.label} ({i.reason or 'partial'})" for i in hit)
            return s, "low-confidence input: " + ", ".join(i.label for i in hit)
    return A.AVAILABLE, None


def _gate(inputs: list[Input]) -> Calc | None:
    """Returns a non-calculated result when inputs do not allow a calculation, else None."""
    status, reason = combined_status(inputs)
    if status in (A.CONFLICTING, A.NOT_AVAILABLE):
        return Calc(None, status, reason)
    if any(i.value is None for i in inputs):  # PARTIAL without an observed value
        return Calc(None, status, reason)
    return None


def q_ratio(v: Decimal) -> Decimal:
    return v.quantize(RATIO_Q, rounding=ROUND_HALF_UP)


def q_inr(v: Decimal) -> Decimal:
    return v.quantize(INR_Q, rounding=ROUND_HALF_UP)


def level(x: Input) -> Calc:
    """The value of a single input (e.g. revenue level)."""
    blocked = _gate([x])
    if blocked:
        return blocked
    status, reason = combined_status([x])
    return Calc(x.value, status, reason)


def ratio(num: Input, den: Input, *, positive_denominator: bool = True) -> Calc:
    """num / den. Zero denominator -> not calculated; negative denominator -> not meaningful."""
    blocked = _gate([num, den])
    if blocked:
        return blocked
    if den.value == 0:
        return Calc(None, A.NOT_AVAILABLE, f"division by zero: {den.label} is 0")
    if positive_denominator and den.value < 0:
        return Calc(None, A.NOT_AVAILABLE, f"{den.label} is negative ({den.value}); ratio not meaningful")
    status, reason = combined_status([num, den])
    return Calc(q_ratio(num.value / den.value), status, reason)


def difference(a: Input, b: Input) -> Calc:
    """a - b (same period, same unit)."""
    blocked = _gate([a, b])
    if blocked:
        return blocked
    status, reason = combined_status([a, b])
    return Calc(q_inr(a.value - b.value), status, reason)


def direction_of(delta: Decimal) -> str:
    return "INCREASE" if delta > 0 else "DECREASE" if delta < 0 else "NO_CHANGE"


def change(prev: Input, cur: Input) -> tuple[Calc, Calc]:
    """(absolute change, percentage change as a ratio) from prev to cur.

    The percentage change needs a positive base: a zero base is never divided by and a negative
    base (e.g. a prior-year loss) gives no percentage, only the absolute change."""
    blocked = _gate([prev, cur])
    if blocked:
        return blocked, Calc(None, blocked.status, blocked.reason)
    status, reason = combined_status([prev, cur])
    delta = cur.value - prev.value
    details = {"previous": str(prev.value), "current": str(cur.value), "absolute_change": str(delta),
               "direction": direction_of(delta)}
    absolute = Calc(q_inr(delta) if cur.unit == "INR" else q_ratio(delta), status, reason, dict(details))
    if prev.value == 0:
        pct = Calc(None, A.NOT_AVAILABLE, f"division by zero: {prev.label} ({prev.period_key}) is 0", dict(details))
    elif prev.value < 0:
        pct = Calc(None, A.NOT_AVAILABLE,
                   f"{prev.label} ({prev.period_key}) is negative; a percentage change is not meaningful",
                   dict(details))
    else:
        pct = Calc(q_ratio(delta / prev.value), status, reason, dict(details))
    return absolute, pct


def compound_change(series: list[Input]) -> Calc:
    """Compound annual change over consecutive periods: (last / first) ^ (1 / (n - 1)) - 1."""
    blocked = _gate(series)
    if blocked:
        return blocked
    if len(series) < 2:
        return Calc(None, A.NOT_AVAILABLE, "needs at least two consecutive periods")
    status, reason = combined_status(series)
    values = [s.value for s in series]
    steps = [b - a for a, b in zip(values, values[1:])]
    if all(s > 0 for s in steps):
        trend = "INCREASING"
    elif all(s < 0 for s in steps):
        trend = "DECREASING"
    elif all(s == 0 for s in steps):
        trend = "FLAT"
    else:
        trend = "MIXED"
    details = {"trend": trend, "periods": [s.period_key for s in series], "values": [str(v) for v in values],
               "step_directions": [direction_of(s) for s in steps]}
    first, last = values[0], values[-1]
    if first <= 0 or last <= 0:
        return Calc(None, A.NOT_AVAILABLE, "compound change needs positive first and last values", details)
    n = len(values) - 1
    value = Decimal(str((float(last) / float(first)) ** (1.0 / n) - 1.0))
    return Calc(q_ratio(value), status, reason, details)


def consistency(values: list[Decimal], min_points: int) -> Calc:
    """1 - coefficient of variation (population std dev / mean), clamped to [0, 1]."""
    if len(values) < min_points:
        return Calc(None, A.NOT_AVAILABLE, f"needs at least {min_points} complete periods (have {len(values)})")
    floats = [float(v) for v in values]
    mean = statistics.fmean(floats)
    if mean <= 0:
        return Calc(None, A.NOT_AVAILABLE, "mean is zero or negative; coefficient of variation not meaningful")
    cv = statistics.pstdev(floats) / mean
    return Calc(q_ratio(Decimal(str(max(0.0, min(1.0, 1.0 - cv))))), A.AVAILABLE, None,
                {"mean": round(mean, 2), "std_dev": round(statistics.pstdev(floats), 2),
                 "coefficient_of_variation": round(cv, 6), "points": len(values)})
