"""Forecasting settings: minimum history, model eligibility, backtest and interval rules.

Defaults live in default_config.json next to this module; FORECAST_CONFIG_FILE replaces them.
Validated on load.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import get_settings

DEFAULT_CONFIG_PATH = Path(__file__).with_name("default_config.json")
ENGINE_VERSION = "forecast-engine-1.0"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SeriesConfig(_Strict):
    min_observations: int = Field(ge=2)
    low_confidence_below: int = Field(ge=2)
    moving_average_window: int = Field(ge=2)
    linear_trend_min_train: int = Field(ge=2)
    min_backtest_points: int = Field(ge=1)
    interval_min_backtest_points: int = Field(ge=2)
    horizon: int = Field(ge=1, le=24)

    @model_validator(mode="after")
    def _backtest_always_possible(self):
        # the naive baseline needs 1 training point; every forecast must be backtested at least this often
        if self.min_observations < 1 + self.min_backtest_points:
            raise ValueError("min_observations must be >= 1 + min_backtest_points")
        return self


class MonthlyConfig(SeriesConfig):
    seasonality_min_months: int = Field(ge=24)  # at least two full yearly cycles
    seasonality_strength_threshold: float = Field(gt=0, le=1)
    min_classification_coverage: float = Field(ge=0, le=1)
    max_unknown_share: float = Field(ge=0, le=1)


class ForecastConfig(_Strict):
    version: str
    note: str = ""
    outlier_robust_z: float = Field(gt=0)
    interval_level: float = Field(gt=0, lt=1)
    interval_z: float = Field(gt=0)
    annual: SeriesConfig
    monthly: MonthlyConfig


def load_config(path: str | Path | None = None) -> ForecastConfig:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    return ForecastConfig.model_validate(json.loads(p.read_text(encoding="utf-8")))


@lru_cache
def _cached(path: str) -> ForecastConfig:
    return load_config(path or None)


_override: ForecastConfig | None = None


def get_config() -> ForecastConfig:
    if _override is not None:
        return _override
    return _cached(get_settings().forecast_config_file or "")


def set_config(config: ForecastConfig | None) -> None:
    """Override the active settings (tests / per-bank configuration)."""
    global _override
    _override = config
