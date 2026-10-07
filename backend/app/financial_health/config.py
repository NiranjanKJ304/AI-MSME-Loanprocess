"""Thresholds for the descriptive health indicators.

Defaults live in default_thresholds.json next to this module; HEALTH_THRESHOLDS_FILE replaces
them. Validated on load. Thresholds only turn calculated metrics into descriptive indicators -
they never change a calculated value.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings

DEFAULT_THRESHOLDS_PATH = Path(__file__).with_name("default_thresholds.json")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Calculation(_Strict):
    min_months_for_consistency: int = Field(ge=2)
    min_years_for_consistency: int = Field(ge=2)
    min_classification_coverage: float = Field(ge=0, le=1)
    unidentified_counterparty_share_low_confidence: float = Field(ge=0, le=1)


class Revenue(_Strict):
    declining_growth_at_most: float
    strong_growth_at_least: float


class Profitability(_Strict):
    weak_net_margin_below: float
    strong_net_margin_at_least: float
    declining_margin_drop_at_least: float = Field(ge=0)


class Liquidity(_Strict):
    weak_current_ratio_below: float = Field(ge=0)
    strong_current_ratio_at_least: float = Field(ge=0)


class CashFlow(_Strict):
    weak_cash_flow_margin_below: float
    strong_cash_flow_margin_at_least: float
    strong_positive_month_share_at_least: float = Field(ge=0, le=1)


class Leverage(_Strict):
    strong_debt_to_revenue_at_most: float = Field(ge=0)
    weak_debt_to_revenue_above: float = Field(ge=0)


class Stability(_Strict):
    weak_consistency_below: float = Field(ge=0, le=1)
    strong_consistency_at_least: float = Field(ge=0, le=1)
    high_concentration_at_least: float = Field(ge=0, le=1)


class HealthThresholds(_Strict):
    version: str
    note: str = ""
    calculation: Calculation
    revenue: Revenue
    profitability: Profitability
    liquidity: Liquidity
    cash_flow: CashFlow
    leverage: Leverage
    stability: Stability


def load_thresholds(path: str | Path | None = None) -> HealthThresholds:
    p = Path(path) if path else DEFAULT_THRESHOLDS_PATH
    return HealthThresholds.model_validate(json.loads(p.read_text(encoding="utf-8")))


@lru_cache
def _cached(path: str) -> HealthThresholds:
    return load_thresholds(path or None)


_override: HealthThresholds | None = None


def get_thresholds() -> HealthThresholds:
    if _override is not None:
        return _override
    return _cached(get_settings().health_thresholds_file or "")


def set_thresholds(thresholds: HealthThresholds | None) -> None:
    """Override the active thresholds (tests / per-bank configuration)."""
    global _override
    _override = thresholds
