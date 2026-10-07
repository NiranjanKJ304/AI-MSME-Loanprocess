"""Loading and validating the configurable classification rules.

Rules live in JSON (default: default_rules.json next to this module; override with TXN_RULES_FILE).
The structure is validated with Pydantic and every regex is compiled up front, so a broken rule
file fails loudly instead of silently misclassifying.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.config import get_settings
from app.models.enums import BusinessNature, CounterpartyType, TxnClass, TxnGroup

DEFAULT_RULES_PATH = Path(__file__).with_name("default_rules.json")
Direction = Literal["CREDIT", "DEBIT", "ANY"]


class CategoryDef(BaseModel):
    group: TxnGroup
    nature: BusinessNature
    direction: Direction


class Rule(BaseModel):
    id: str
    category: TxnClass
    direction: Direction = "ANY"
    weight: float = Field(gt=0, le=1)
    patterns: list[str]
    exclude: list[str] = []
    min_amount: float | None = None
    max_amount: float | None = None
    channels: list[str] = []
    # the matched keyword names the counterparty (e.g. 'TNEB', 'LIC'): keep it as counterparty text
    keyword_is_counterparty: bool = False

    def compiled(self) -> tuple[list[re.Pattern[str]], list[re.Pattern[str]]]:
        return [re.compile(p, re.I) for p in self.patterns], [re.compile(p, re.I) for p in self.exclude]


class Signal(BaseModel):
    category: TxnClass | None = None
    weight: float = Field(gt=0, le=1)
    channels: list[str] = []
    counterparty_types: list[CounterpartyType] = []
    min_amount: float | None = None
    max_days: int | None = None


class Thresholds(BaseModel):
    classify_min_score: float = 0.45
    low_confidence_below: float = 0.65
    monthly_coverage_ok: float = 0.8
    own_name_similarity: float = 0.85


class PersonalCfg(BaseModel):
    weight: float = 0.6
    patterns: list[str] = []
    exclude: list[str] = []


class CounterpartyCfg(BaseModel):
    stopwords: list[str] = []
    month_tokens: list[str] = []
    business_suffixes: list[str] = []
    lender_words: list[str] = []
    government_words: list[str] = []


class ReversalCfg(BaseModel):
    window_days: int = 10
    refund_patterns: list[str] = []


class RecurringCfg(BaseModel):
    min_occurrences: int = 3
    min_distinct_months: int = 2
    intervals: dict[str, tuple[int, int]] = {"WEEKLY": (5, 9), "MONTHLY": (25, 35), "QUARTERLY": (80, 100)}
    regular_share: float = 0.6
    stable_amount_cv: float = 0.15
    group_by_category_without_counterparty: list[TxnClass] = []


class RuleSet(BaseModel):
    version: str
    description: str = ""
    thresholds: Thresholds = Thresholds()
    categories: dict[TxnClass, CategoryDef]
    rules: list[Rule]
    signals: dict[str, Signal] = {}
    personal: PersonalCfg = PersonalCfg()
    channels: dict[str, list[str]] = {}
    counterparty: CounterpartyCfg = CounterpartyCfg()
    reversal: ReversalCfg = ReversalCfg()
    recurring: RecurringCfg = RecurringCfg()
    related_party_names: list[str] = []

    @model_validator(mode="after")
    def _check(self) -> RuleSet:
        missing = [c for c in TxnClass if c not in self.categories]
        if missing:
            raise ValueError(f"categories missing from rules: {missing}")
        for r in self.rules:
            cdir = self.categories[r.category].direction
            if cdir != "ANY" and r.direction not in (cdir, "ANY"):
                raise ValueError(f"rule {r.id}: direction {r.direction} contradicts category {r.category} ({cdir})")
            r.compiled()  # raises re.error for a bad pattern
        for p in self.personal.patterns + self.personal.exclude + self.reversal.refund_patterns:
            re.compile(p)
        return self

    # compiled helpers -------------------------------------------------------
    def compiled_rules(self) -> list[tuple[Rule, list[re.Pattern[str]], list[re.Pattern[str]]]]:
        return [(r, *r.compiled()) for r in self.rules]

    def group_of(self, c: TxnClass) -> TxnGroup:
        return self.categories[c].group

    def nature_of(self, c: TxnClass) -> BusinessNature:
        return self.categories[c].nature

    def direction_of(self, c: TxnClass) -> Direction:
        return self.categories[c].direction


def load_rules(path: str | Path | None = None) -> RuleSet:
    p = Path(path) if path else DEFAULT_RULES_PATH
    return RuleSet.model_validate(json.loads(p.read_text(encoding="utf-8")))


@lru_cache
def _cached(path: str) -> RuleSet:
    return load_rules(path or None)


_override: RuleSet | None = None


def get_rules() -> RuleSet:
    if _override is not None:
        return _override
    return _cached(get_settings().txn_rules_file or "")


def set_rules(rules: RuleSet | None) -> None:
    """Override the active rule set (tests / per-bank configuration)."""
    global _override
    _override = rules
