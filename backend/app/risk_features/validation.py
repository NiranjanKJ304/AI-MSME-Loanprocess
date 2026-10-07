"""Validation of a computed feature set. Errors make the snapshot invalid (it is still stored and
returned, with the errors); informational findings (missing / conflicting inputs) are reported, never
resolved."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.models.enums import FactAvailability
from app.risk_features.calculators import FeatureValue
from app.risk_features.context import FeatureContext
from app.risk_features.definitions import BY_NAME, UNITS

A = FactAvailability
FY_KEY = re.compile(r"^FY\d{4}-\d{2}$")
NULL_STATUSES = {A.NOT_AVAILABLE, A.CONFLICTING}
VALUE_STATUSES = {A.AVAILABLE, A.LOW_CONFIDENCE}


def validate_feature(name: str, f: FeatureValue, ctx: FeatureContext | None = None) -> list[str]:
    d = BY_NAME.get(name)
    if d is None:
        return [f"{name}: no feature definition"]
    errors = []
    if not isinstance(f.status, A):
        errors.append(f"{name}: unsupported status {f.status!r}")
        return errors
    if d.unit not in UNITS:
        errors.append(f"{name}: unsupported unit {d.unit}")
    v = f.value
    if v is not None:
        if d.value_type == "TEXT" and not isinstance(v, str):
            errors.append(f"{name}: expected TEXT, got {type(v).__name__}")
        if d.value_type in ("NUMBER", "INTEGER") and not isinstance(v, Decimal):
            errors.append(f"{name}: expected a Decimal number, got {type(v).__name__}")
        if d.value_type == "INTEGER" and isinstance(v, Decimal) and v != v.to_integral_value():
            errors.append(f"{name}: expected an integer, got {v}")
        if d.unit in ("COUNT", "MONTHS") and isinstance(v, Decimal) and v < 0:
            errors.append(f"{name}: a {d.unit.lower()} cannot be negative")
    if f.status in NULL_STATUSES and v is not None:
        errors.append(f"{name}: status {f.status.value} must have a null value (never a substituted value)")
    if f.status in VALUE_STATUSES and v is None:
        errors.append(f"{name}: status {f.status.value} without a value")
    if v is None and not f.reason:
        errors.append(f"{name}: null value without a reason")
    if v is not None and not f.provenance:
        errors.append(f"{name}: value without provenance (origin cannot be explained)")
    if ctx is not None and v is not None:
        if d.period_scope == "LATEST_FY" and ctx.latest_fy is not None and f.period != ctx.latest_fy.period_key:
            errors.append(f"{name}: period {f.period} is not the latest FY {ctx.latest_fy.period_key}")
        if d.period_scope == "LATEST_FY" and f.period and not FY_KEY.match(f.period):
            errors.append(f"{name}: period {f.period} is not a financial year")
        if d.period_scope == "BANK_WINDOW" and d.source_layer == "TRANSACTION_INTELLIGENCE" and \
                f.period != ctx.window_key:
            errors.append(f"{name}: period {f.period} is not the bank window {ctx.window_key}")
    return errors


def validate_set(features: list[tuple[str, FeatureValue]], ctx: FeatureContext | None = None) -> dict[str, Any]:
    names = [n for n, _ in features]
    errors = [f"duplicate feature name: {n}" for n in sorted({n for n in names if names.count(n) > 1})]
    for n, f in features:
        errors += validate_feature(n, f, ctx)
    missing = {n: f.reason for n, f in features if f.status == A.NOT_AVAILABLE}
    conflicting = {n: f.reason for n, f in features if f.status == A.CONFLICTING}
    return {"valid": not errors, "errors": errors,
            "missing_inputs": missing, "conflicting_inputs": conflicting,
            "note": "missing and conflicting inputs are reported as feature statuses; nothing is filled or resolved"}
