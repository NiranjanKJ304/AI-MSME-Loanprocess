"""Historical observation series for each forecast target.

  revenue                  ANNUAL   one observation per financial year, resolved like the health layer
                                    (P&L revenue, else declared ITR / GST turnover; conflicts never resolved)
  business_inflow          MONTHLY  combined monthly cash flow: credits classified INCOME with BUSINESS
                                    nature only (never total bank credits)
  business_outflow         MONTHLY  debits classified EXPENSE with BUSINESS nature
  net_business_cash_flow   MONTHLY  business inflow - business outflow

Every period with data becomes an observation; only complete, non-conflicting observations of the
same basis can be used. The model is trained on the most recent run of consecutive usable
observations - missing periods are never filled and PARTIAL periods are never treated as complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.financial_health.formulas import Input
from app.financial_health.inputs import Snapshot
from app.financials.periods import fiscal_year
from app.models.enums import FactAvailability, ForecastFrequency

A = FactAvailability
USABLE = (A.AVAILABLE, A.LOW_CONFIDENCE)

TARGETS: dict[str, dict[str, Any]] = {
    "revenue": {"label": "Revenue", "frequency": ForecastFrequency.ANNUAL, "non_negative": True},
    "business_inflow": {"label": "Business inflow", "frequency": ForecastFrequency.MONTHLY, "non_negative": True,
                        "buckets": ["business_inflow"], "column": "business_inflow",
                        "unknown": ["unknown_inflow"], "total": ["total_inflow"]},
    "business_outflow": {"label": "Business outflow", "frequency": ForecastFrequency.MONTHLY, "non_negative": True,
                         "buckets": ["business_outflow"], "column": "business_outflow",
                         "unknown": ["unknown_outflow"], "total": ["total_outflow"]},
    "net_business_cash_flow": {"label": "Net business cash flow", "frequency": ForecastFrequency.MONTHLY,
                               "non_negative": False, "buckets": ["business_inflow", "business_outflow"],
                               "column": "net_operating_cash_flow", "unknown": ["unknown_inflow", "unknown_outflow"],
                               "total": ["total_inflow", "total_outflow"]},
}


@dataclass
class Observation:
    index: int  # FY start year or (year * 12 + month) - consecutive periods differ by 1
    period_key: str
    label: str
    start: date
    end: date
    value: Decimal | None
    source_status: FactAvailability
    used: bool
    exclusion_reason: str | None
    source: Input  # sources / conflicts / basis for provenance and evidence
    month: int | None = None
    coverage: float | None = None
    unknown_value: Decimal = Decimal(0)
    total_value: Decimal = Decimal(0)
    outlier: bool = False
    in_training: bool = False


@dataclass
class Series:
    metric: str
    label: str
    frequency: ForecastFrequency
    non_negative: bool
    observations: list[Observation]
    missing_periods: list[str] = field(default_factory=list)
    basis: str | None = None

    @property
    def training(self) -> list[Observation]:
        return [o for o in self.observations if o.in_training]

    @property
    def last_observed(self) -> Observation | None:
        return self.observations[-1] if self.observations else None


def _mark_training_run(obs: list[Observation]) -> None:
    """Most recent run of consecutive usable observations; earlier usable ones are reported, not trained on."""
    usable = [o for o in obs if o.used]
    if not usable:
        return
    run = [usable[-1]]
    for o in reversed(usable[:-1]):
        if o.index == run[0].index - 1:
            run.insert(0, o)
        else:
            break
    for o in run:
        o.in_training = True
    first = run[0]
    for o in usable:
        if not o.in_training:
            o.used = False
            o.exclusion_reason = (f"not consecutive with the most recent observations (the run used for training "
                                  f"starts at {first.period_key}; earlier history is broken by a missing, partial "
                                  "or excluded period)")


def _missing(indexes: list[int], key_of) -> list[str]:
    if not indexes:
        return []
    present = set(indexes)
    return [key_of(i) for i in range(min(indexes), max(indexes) + 1) if i not in present]


def revenue_series(snap: Snapshot) -> Series:
    meta = TARGETS["revenue"]
    obs: list[Observation] = []
    for p in snap.fy_periods():
        inp = snap.revenue_input(p)
        if inp.status == A.NOT_AVAILABLE:
            continue  # no revenue for this year: a missing period, never a zero
        used = inp.status in USABLE and inp.value is not None
        reason = None
        if inp.status == A.CONFLICTING:
            reason = f"CONFLICTING: {inp.reason} - excluded, no value selected"
        elif not used:
            reason = f"{inp.status.value}: {inp.reason or 'no usable value'} - not a complete observation"
        obs.append(Observation(index=p.start_date.year, period_key=p.period_key, label=p.label, start=p.start_date,
                               end=p.end_date, value=inp.value if used else None, source_status=inp.status, used=used,
                               exclusion_reason=reason, source=inp))
    series = Series("revenue", meta["label"], meta["frequency"], True, obs,
                    _missing([o.index for o in obs], lambda y: fiscal_year(y).key))
    used = [o for o in obs if o.used]
    if used:
        series.basis = used[-1].source.basis
        for o in used:
            if o.source.basis != series.basis:
                o.used = False
                o.exclusion_reason = (f"different revenue basis ({o.source.basis}) from the latest observation "
                                      f"({series.basis}) - not comparable")
    _mark_training_run(obs)
    return series


def monthly_series(snap: Snapshot, metric: str) -> Series:
    meta = TARGETS[metric]
    obs: list[Observation] = []
    for m in snap.months:
        value = getattr(m, meta["column"])
        partial = m.availability == A.PARTIAL
        src = Input(name=metric, label=f"{meta['label']} {m.month}", value=value, status=m.availability,
                    period_key=m.month, confidence=m.confidence,
                    reason="; ".join(m.partial_reasons or []) or None,
                    sources=[snap.month_source(m, meta["buckets"])])
        obs.append(Observation(
            index=m.month_start.year * 12 + m.month_start.month - 1, period_key=m.month,
            label=m.month_start.strftime("%b %Y"), start=m.month_start, end=m.month_end,
            value=None if partial else value, source_status=m.availability, used=not partial,
            exclusion_reason=(f"PARTIAL month ({'; '.join(m.partial_reasons or ['incomplete'])}) - not a complete "
                              "observation") if partial else None,
            source=src, month=m.month_start.month, coverage=m.classification_coverage_amount,
            unknown_value=sum((getattr(m, b) for b in meta["unknown"]), Decimal(0)),
            total_value=sum((getattr(m, b) for b in meta["total"]), Decimal(0))))
    series = Series(metric, meta["label"], meta["frequency"], meta["non_negative"], obs,
                    _missing([o.index for o in obs], lambda i: f"{i // 12}-{i % 12 + 1:02d}"),
                    basis="classified bank transactions (combined monthly cash flow, all statements)")
    _mark_training_run(obs)
    return series


def build_series(snap: Snapshot) -> list[Series]:
    return [revenue_series(snap)] + [monthly_series(snap, m) for m in TARGETS if m != "revenue"]
