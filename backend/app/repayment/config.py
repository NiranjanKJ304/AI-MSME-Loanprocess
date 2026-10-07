"""Repayment-capacity settings: history window, data-quality thresholds, stress percentages and the
descriptive LOW_CAPACITY threshold. Defaults in default_config.json; REPAYMENT_CONFIG_FILE replaces
them. Validated on load."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import get_settings

DEFAULT_CONFIG_PATH = Path(__file__).with_name("default_config.json")
ENGINE_VERSION = "repayment-engine-1.0"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Stress(_Strict):
    revenue_down_pct: float = Field(ge=0, lt=1)
    expense_up_pct: float = Field(ge=0, le=5)


class RepaymentConfig(_Strict):
    version: str
    note: str = ""
    history_months: int = Field(ge=3, le=60)
    min_history_months: int = Field(ge=1)
    min_classification_coverage: float = Field(ge=0, le=1)
    max_unknown_share: float = Field(ge=0, le=1)
    low_capacity_dscr_below: float = Field(gt=0)
    stress: Stress

    @model_validator(mode="after")
    def _window(self):
        if self.min_history_months > self.history_months:
            raise ValueError("min_history_months must be <= history_months")
        return self


def load_config(path: str | Path | None = None) -> RepaymentConfig:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    return RepaymentConfig.model_validate(json.loads(p.read_text(encoding="utf-8")))


@lru_cache
def _cached(path: str) -> RepaymentConfig:
    return load_config(path or None)


_override: RepaymentConfig | None = None


def get_config() -> RepaymentConfig:
    if _override is not None:
        return _override
    return _cached(get_settings().repayment_config_file or "")


def set_config(config: RepaymentConfig | None) -> None:
    """Override the active settings (tests / per-bank configuration)."""
    global _override
    _override = config
