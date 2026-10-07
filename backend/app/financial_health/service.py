"""Builds and persists the financial health profile of an application (deleted and rebuilt on
every run), and renders it for the API."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.financial_health.config import get_thresholds
from app.financial_health.engine import HealthEngine, MetricResult
from app.financial_health.explain import explain_metric, period_description
from app.financial_health.inputs import Snapshot
from app.models import Application, FinancialHealthIndicator, FinancialHealthMetric
from app.models.enums import FactAvailability, HealthDimension, HealthPeriodKind

NOTES = [
    "Descriptive financial condition only: no risk score, repayment capacity, forecast or loan decision.",
    "Missing inputs stay missing (NOT_AVAILABLE) - never zero. Conflicting facts are never used in a formula.",
    "Bank credits are not revenue: bank metrics use only transactions classified INCOME / EXPENSE with BUSINESS "
    "nature, and are never combined with financial-statement values in one formula.",
    "Trends compare consecutive financial years only; partial periods are labelled PARTIAL_PERIOD.",
]


@dataclass
class HealthReport:
    metrics: int = 0
    indicators: int = 0
    periods: int = 0
    not_calculated: int = 0
    conflicting: int = 0
    partial: int = 0
    thresholds_version: str = ""
    warnings: list[str] = field(default_factory=list)


def clear_financial_health(db: Session, application_id) -> None:
    db.execute(delete(FinancialHealthIndicator).where(FinancialHealthIndicator.application_id == application_id))
    db.execute(delete(FinancialHealthMetric).where(FinancialHealthMetric.application_id == application_id))


def _provenance(m: MetricResult) -> dict[str, Any]:
    fact_ids, month_ids, txn_ids, conflict_ids, doc_ids = set(), set(), set(), set(), set()
    for i in m.inputs:
        for s in i.sources:
            kind = s.get("kind")
            if kind == "FINANCIAL_FACT":
                fact_ids.add(s["fact_id"])
            elif kind == "MONTHLY_CASHFLOW":
                month_ids.add(s["monthly_aggregate_id"])
                txn_ids.update(s.get("transaction_ids", []))
            elif kind == "TRANSACTIONS":
                txn_ids.update(s.get("transaction_ids", []))
            elif kind == "DOCUMENT":
                doc_ids.add(s["document_id"])
        for c in i.conflicts:
            conflict_ids.add(c["conflict_id"])
    if fact_ids or conflict_ids:
        chain = "health metric -> calculation -> canonical financial facts -> extracted fields -> document -> page/bbox"
    elif month_ids or txn_ids:
        chain = ("health metric -> calculation -> monthly cash-flow aggregates -> classified transactions -> "
                 "canonical bank transactions -> statement rows -> document -> page/bbox")
    else:
        chain = "health metric -> calculation -> documents"
    return {"chain": chain, "fact_ids": sorted(fact_ids), "conflict_ids": sorted(conflict_ids),
            "monthly_aggregate_ids": sorted(month_ids), "transaction_ids": sorted(txn_ids),
            "document_ids": sorted(doc_ids),
            "calculation": {"formula": m.formula, "inputs": [
                {"name": i.name, "value": None if i.value is None else str(i.value), "status": i.status.value}
                for i in m.inputs]}}


def build_financial_health(db: Session, application: Application) -> HealthReport:
    clear_financial_health(db, application.id)
    db.flush()
    th = get_thresholds()
    snap = Snapshot(db, application.id)
    engine = HealthEngine(snap, th)
    metrics, indicators = engine.run()
    report = HealthReport(thresholds_version=th.version, warnings=list(engine.warnings))
    for m in metrics:
        inputs = [i.as_dict() for i in m.inputs]
        evidence = snap.evidence_for(m.inputs)
        desc = period_description(m.period_label, m.period_kind, m.compare_label, m.window)
        if m.scope:
            desc += f", {m.scope}"
        details = dict(m.calc.details or {})
        if m.window:
            details.setdefault("window", m.window)
        db.add(FinancialHealthMetric(
            id=m.id, application_id=application.id, dimension=m.dimension, metric=m.metric, label=m.label,
            period_key=m.period_key, period_kind=m.period_kind, period_start=m.period_start, period_end=m.period_end,
            compare_period_key=m.compare_period_key, scope=m.scope, value=m.value, unit=m.unit, status=m.status,
            formula=m.formula, inputs=inputs, details=details or None, reason=m.calc.reason,
            explanation=explain_metric(m.label, desc, m.formula, inputs, m.value, m.unit, m.status.value,
                                       m.calc.reason, evidence),
            evidence=evidence, provenance=_provenance(m), confidence=m.confidence))
        report.not_calculated += m.value is None
        report.conflicting += m.status == FactAvailability.CONFLICTING
        report.partial += m.status == FactAvailability.PARTIAL
    for ind in indicators:
        db.add(FinancialHealthIndicator(
            application_id=application.id, dimension=ind.dimension, period_key=ind.period_key,
            period_kind=ind.period_kind, indicator=ind.indicator, rule=ind.rule, evidence=ind.evidence,
            explanation=ind.explanation, thresholds_version=th.version))
    db.flush()
    report.metrics = len(metrics)
    report.indicators = len(indicators)
    report.periods = len({m.period_key for m in metrics})
    return report


# --------------------------------------------------------------------------- views
def _out(value, unit: str) -> str | None:
    if value is None:
        return None
    if unit == "INR":
        return str(value.quantize(Decimal("0.01")))
    if unit in ("COUNT", "MONTHS") and value == value.to_integral_value():
        return str(int(value))
    return str(value)


def metric_view(m: FinancialHealthMetric) -> dict[str, Any]:
    return {
        "id": str(m.id), "dimension": m.dimension.value, "metric": m.metric, "label": m.label,
        "period_key": m.period_key, "period_kind": m.period_kind.value,
        "period_start": m.period_start.isoformat() if m.period_start else None,
        "period_end": m.period_end.isoformat() if m.period_end else None,
        "compare_period_key": m.compare_period_key, "scope": m.scope,
        "value": _out(m.value, m.unit), "unit": m.unit, "status": m.status.value,
        "formula": m.formula, "inputs": m.inputs, "details": m.details, "reason": m.reason,
        "explanation": m.explanation, "evidence": m.evidence, "provenance": m.provenance, "confidence": m.confidence,
    }


def indicator_view(i: FinancialHealthIndicator) -> dict[str, Any]:
    return {"id": str(i.id), "dimension": i.dimension.value, "period_key": i.period_key,
            "period_kind": i.period_kind.value, "indicator": i.indicator.value, "rule": i.rule,
            "evidence": i.evidence, "explanation": i.explanation, "thresholds_version": i.thresholds_version}


def _period_block(key: str, metrics: list[FinancialHealthMetric],
                  indicators: list[FinancialHealthIndicator]) -> dict[str, Any]:
    dims: dict[str, dict[str, Any]] = {}
    for d in HealthDimension:
        ms = [m for m in metrics if m.dimension == d]
        ind = next((i for i in indicators if i.dimension == d), None)
        if not ms and ind is None:
            continue
        dims[d.value] = {"indicator": indicator_view(ind) if ind else None, "metrics": [metric_view(m) for m in ms]}
    starts = [m.period_start for m in metrics if m.period_start]
    ends = [m.period_end for m in metrics if m.period_end]
    return {
        "period_key": key, "period_kinds": sorted({m.period_kind.value for m in metrics}),
        "start_date": min(starts).isoformat() if starts else None, "end_date": max(ends).isoformat() if ends else None,
        "summary": {s.value: sum(1 for m in metrics if m.status == s) for s in FactAvailability},
        "dimensions": dims,
    }


def health_profile(db: Session, application_id, period_key: str | None = None) -> dict[str, Any] | None:
    stmt = select(FinancialHealthMetric).where(FinancialHealthMetric.application_id == application_id)
    istmt = select(FinancialHealthIndicator).where(FinancialHealthIndicator.application_id == application_id)
    if period_key:
        stmt = stmt.where(FinancialHealthMetric.period_key == period_key)
        istmt = istmt.where(FinancialHealthIndicator.period_key == period_key)
    metrics = list(db.scalars(stmt.order_by(FinancialHealthMetric.period_start, FinancialHealthMetric.dimension,
                                            FinancialHealthMetric.metric)))
    indicators = list(db.scalars(istmt))
    if period_key and not metrics:
        return None
    by_key: dict[str, list[FinancialHealthMetric]] = defaultdict(list)
    for m in metrics:
        by_key[m.period_key].append(m)
    ind_by_key: dict[str, list[FinancialHealthIndicator]] = defaultdict(list)
    for i in indicators:
        ind_by_key[i.period_key].append(i)

    keys = sorted(by_key, key=lambda k: (str(min((m.period_start for m in by_key[k] if m.period_start),
                                                  default="")), k))
    periods = [_period_block(k, by_key[k], ind_by_key[k]) for k in keys
               if not all(m.period_kind == HealthPeriodKind.MONTHLY for m in by_key[k])]
    monthly = [_period_block(k, by_key[k], ind_by_key[k]) for k in keys
               if all(m.period_kind == HealthPeriodKind.MONTHLY for m in by_key[k])]
    return {
        "application_id": str(application_id),
        "period_key": period_key,
        "built": bool(metrics),
        "thresholds_version": indicators[0].thresholds_version if indicators else get_thresholds().version,
        "notes": NOTES,
        "periods": periods,
        "monthly": monthly,
    }
