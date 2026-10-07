"""Deterministic, template-based explanations (no LLM)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.models.enums import HealthPeriodKind


def inr(v: Decimal) -> str:
    """Indian digit grouping: 6820000 -> ₹68,20,000.00"""
    neg = v < 0
    whole, frac = f"{abs(v):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    return f"{'-' if neg else ''}₹{whole}.{frac}"


def fmt(value: Decimal | str | None, unit: str) -> str:
    if unit == "SERIES":
        return "series (see sources)"
    if value is None:
        return "not available"
    v = Decimal(str(value))
    if unit == "INR":
        return inr(v)
    if unit == "RATIO":
        return f"{v * 100:.2f}%"
    if unit == "TIMES":
        return f"{v:.2f}x"
    if unit == "MONTHS":
        return f"{int(v)} month(s)"
    return f"{int(v)}" if v == v.to_integral_value() else f"{v}"


def period_description(label: str, kind: HealthPeriodKind, compare_label: str | None = None,
                       window: str | None = None) -> str:
    if compare_label:
        return f"{label} compared with {compare_label} (annual comparison)"
    if kind == HealthPeriodKind.PARTIAL_PERIOD:
        return f"{label}, partial period{f' {window}' if window else ''}"
    return f"{label} ({kind.value.lower()})"


def _sources_short(sources: list[dict[str, Any]]) -> str:
    refs = []
    for s in sources:
        if s.get("kind") == "FINANCIAL_FACT":
            page = s.get("page") or s.get("pages")
            refs.append(f"{s.get('document_code')}{f' p.{page}' if page else ''}")
        elif s.get("kind") == "MONTHLY_CASHFLOW":
            refs.append(s.get("month"))
    refs = list(dict.fromkeys(r for r in refs if r))
    if len(refs) > 6:
        refs = refs[:3] + ["..."] + refs[-2:]
    return ", ".join(refs)


def explain_metric(label: str, period_desc: str, formula: str, inputs: list[dict[str, Any]], value, unit: str,
                   status: str, reason: str | None, evidence: list[dict[str, Any]]) -> str:
    parts = [f"{label} - {period_desc}.", f"Formula: {formula}."]
    if inputs:
        described = []
        for i in inputs:
            src = _sources_short(i.get("sources") or [])
            extra = "; ".join(x for x in (i["status"], i.get("basis"), src) if x)
            described.append(f"{i['label']} = {fmt(i.get('value'), i.get('unit', 'INR'))} [{extra}]")
        parts.append("Inputs: " + "; ".join(described) + ".")
    if value is None:
        parts.append(f"Not calculated: {reason or 'required inputs are not available'}.")
    else:
        parts.append(f"Result: {fmt(value, unit)}.")
        if reason:
            parts.append(f"Limitation: {reason}.")
    parts.append(f"Status: {status}.")
    if evidence:
        parts.append("Evidence: " + "; ".join(
            f"{e.get('document_code')} ({e.get('document_type')})"
            + (f" p.{','.join(str(p) for p in e['pages'])}" if e.get("pages") else "") for e in evidence) + ".")
    return " ".join(parts)
