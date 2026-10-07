"""Shared validation primitives (pure functions, deterministic)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.models.enums import CheckStatus, Severity

PAN_FULL = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
GSTIN_FULL = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
IFSC_FULL = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
UDYAM_FULL = re.compile(r"^UDYAM-[A-Z]{2}-\d{2}-\d{7}$")
CIN_FULL = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")

_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# GST state codes 01-38 (+97 other territory, 99 centre jurisdiction)
VALID_GST_STATE_CODES = {f"{i:02d}" for i in range(1, 39)} | {"97", "99"}


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        product = _GST_CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _GST_CHARS[(36 - total % 36) % 36]


def gstin_checksum_ok(gstin: str) -> bool:
    if not GSTIN_FULL.match(gstin):
        return False
    return gstin_check_char(gstin[:14]) == gstin[14]


@dataclass
class RuleOutcome:
    rule_code: str
    status: CheckStatus
    severity: Severity
    message: str
    expected: str | None = None
    actual: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_problem(self) -> bool:
        return self.status not in (CheckStatus.PASS, CheckStatus.SKIPPED)


def approx_equal(a: Decimal, b: Decimal, abs_tol: Decimal, rel_tol: Decimal = Decimal("0")) -> bool:
    diff = abs(a - b)
    return diff <= abs_tol or (rel_tol > 0 and diff <= rel_tol * max(abs(a), abs(b)))


def dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None
