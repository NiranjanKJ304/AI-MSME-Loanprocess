"""Builds and persists forecasts for an application (deleted and rebuilt on every run) and renders
them for the API."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.financial_health.inputs import Snapshot
from app.forecasting.config import ENGINE_VERSION, get_config
from app.forecasting.engine import ForecastEngine, Outcome
from app.forecasting.series import TARGETS, Observation, build_series
from app.models import Application, ForecastObservation, ForecastResult, ForecastRun
from app.models.enums import FactAvailability

NOTES = [
    "Forecasts are statistical projections from past observations - not actuals and not guaranteed values.",
    "Historical actuals, forecasts and uncertainty are reported separately.",
    "Revenue uses canonical financial facts only; bank-based forecasts use only transactions classified as "
    "business inflow / outflow - total bank credits are never treated as revenue.",
    "No repayment-capacity, risk or credit-decision logic.",
]


@dataclass
class ForecastReport:
    metrics: int = 0
    forecasts: int = 0
    not_available: int = 0
    low_confidence: int = 0
    engine_version: str = ENGINE_VERSION
    config_version: str = ""
    warnings: list[str] = field(default_factory=list)


def clear_forecasts(db: Session, application_id) -> None:
    db.execute(delete(ForecastResult).where(ForecastResult.application_id == application_id))
    db.execute(delete(ForecastObservation).where(ForecastObservation.application_id == application_id))
    db.execute(delete(ForecastRun).where(ForecastRun.application_id == application_id))


def _source_ids(o: Observation) -> dict[str, Any]:
    facts, months, txns = [], [], []
    for s in o.source.sources:
        if s.get("kind") == "FINANCIAL_FACT":
            facts.append({"fact_id": s["fact_id"], "metric": s.get("metric"), "value": s.get("value"),
                          "document_code": s.get("document_code"), "field_id": s.get("field_id"),
                          "page": s.get("page"), "bbox": s.get("bbox")})
        elif s.get("kind") == "MONTHLY_CASHFLOW":
            months.append(s["monthly_aggregate_id"])
            txns.extend(s.get("transaction_ids", []))
    return {"basis": o.source.basis, "facts": facts, "monthly_aggregate_ids": months, "transaction_ids": txns,
            "conflicts": [{"conflict_id": c["conflict_id"], "value_a": c["value_a"], "value_b": c["value_b"],
                           "source_a": c["source_a"].get("document_code"),
                           "source_b": c["source_b"].get("document_code")} for c in o.source.conflicts]}


def _provenance(out: Outcome, obs_ids: dict[str, uuid.UUID]) -> dict[str, Any]:
    annual = out.series.metric == "revenue"
    train = out.series.training
    ids = [_source_ids(o) for o in train]
    return {
        "chain": ("forecast -> historical observations -> canonical financial facts -> extracted fields -> "
                  "document -> page/bbox") if annual else
                 ("forecast -> historical observations -> monthly cash-flow aggregates -> classified transactions "
                  "-> canonical bank transactions -> statement rows -> document -> page/bbox"),
        "training_observation_ids": [str(obs_ids[o.period_key]) for o in train],
        "excluded_observation_ids": [str(obs_ids[o.period_key]) for o in out.series.observations
                                     if not o.in_training],
        "fact_ids": sorted({f["fact_id"] for i in ids for f in i["facts"]}),
        "monthly_aggregate_ids": sorted({m for i in ids for m in i["monthly_aggregate_ids"]}),
        "transaction_ids": sorted({t for i in ids for t in i["transaction_ids"]}),
        "reproduce": "train the selected model on the training observations' values in period order "
                     "and predict the forecast periods (steps ahead = horizon)",
    }


def build_forecasts(db: Session, application: Application) -> ForecastReport:
    clear_forecasts(db, application.id)
    db.flush()
    cfg = get_config()
    snap = Snapshot(db, application.id)
    engine = ForecastEngine(cfg)
    report = ForecastReport(config_version=cfg.version)
    for series in build_series(snap):
        out = engine.forecast(series)
        run_id = uuid.uuid4()
        obs_ids = {o.period_key: uuid.uuid4() for o in series.observations}
        sel = out.selection
        train = series.training
        db.add(ForecastRun(
            id=run_id, application_id=application.id, metric=series.metric, frequency=series.frequency,
            engine_version=ENGINE_VERSION, config_version=cfg.version, status=out.status, reason=out.reason,
            model=sel.model.name if sel and sel.model else None,
            model_params=sel.model.params if sel and sel.model else None,
            training_start=train[0].period_key if train else None, training_end=train[-1].period_key if train else None,
            observations_used=len(train), observations_total=len(series.observations), basis=series.basis,
            selection={"rule": sel.rule if sel else "not attempted: data-quality check failed",
                       "candidates": [b.as_dict() for b in sel.backtests] if sel else [],
                       "rejected": sel.rejected if sel else [],
                       "selected": sel.model.name if sel and sel.model else None},
            backtest=sel.selected_backtest.as_dict() if sel and sel.selected_backtest else None,
            uncertainty=out.uncertainty, seasonality=out.seasonality, data_quality=out.checks,
            assumptions=out.assumptions, provenance=_provenance(out, obs_ids)))
        db.flush()
        for i, o in enumerate(series.observations):
            db.add(ForecastObservation(
                id=obs_ids[o.period_key], run_id=run_id, application_id=application.id, sequence=i,
                period_key=o.period_key, period_start=o.start, period_end=o.end, value=o.value,
                source_status=o.source_status, used_for_training=o.in_training, exclusion_reason=o.exclusion_reason,
                outlier=o.outlier, sources=_source_ids(o), evidence=snap.evidence_for([o.source])))
        for r in out.results:
            db.add(ForecastResult(
                run_id=run_id, application_id=application.id, metric=series.metric, period_key=r.target.period_key,
                period_start=r.target.start, period_end=r.target.end, horizon=r.target.horizon,
                predicted_value=r.value, lower_bound=r.lower, upper_bound=r.upper,
                interval_level=out.uncertainty.get("level") if r.lower is not None else None, note=r.note,
                model=sel.model.name, status=r.status, explanation=r.explanation))
        report.metrics += 1
        report.forecasts += sum(1 for r in out.results if r.value is not None)
        report.not_available += out.status == FactAvailability.NOT_AVAILABLE
        report.low_confidence += out.status == FactAvailability.LOW_CONFIDENCE
        if out.status == FactAvailability.NOT_AVAILABLE:
            report.warnings.append(f"{series.metric}: no forecast - {out.reason}")
    db.flush()
    return report


# --------------------------------------------------------------------------- views
def _s(v) -> str | None:
    return None if v is None else str(v)


def run_view(db: Session, run: ForecastRun, detail: bool) -> dict[str, Any]:
    results = db.scalars(select(ForecastResult).where(ForecastResult.run_id == run.id)
                         .order_by(ForecastResult.period_start)).all()
    obs = db.scalars(select(ForecastObservation).where(ForecastObservation.run_id == run.id)
                     .order_by(ForecastObservation.sequence)).all()
    out: dict[str, Any] = {
        "metric": run.metric, "label": TARGETS[run.metric]["label"], "frequency": run.frequency.value,
        "status": run.status.value, "reason": run.reason, "model": run.model, "model_params": run.model_params,
        "engine_version": run.engine_version, "config_version": run.config_version,
        "training_period": {"start": run.training_start, "end": run.training_end,
                            "observations": run.observations_used},
        "historical": [{
            "id": str(o.id), "period_key": o.period_key, "period_start": o.period_start.isoformat(),
            "period_end": o.period_end.isoformat(), "actual": _s(o.value), "source_status": o.source_status.value,
            "used_for_training": o.used_for_training, "exclusion_reason": o.exclusion_reason, "outlier": o.outlier,
            **({"sources": o.sources, "evidence": o.evidence} if detail else {})} for o in obs],
        "forecast": [{
            "id": str(r.id), "period_key": r.period_key, "period_start": r.period_start.isoformat(),
            "period_end": r.period_end.isoformat(), "horizon": r.horizon, "predicted_value": _s(r.predicted_value),
            "lower_bound": _s(r.lower_bound), "upper_bound": _s(r.upper_bound), "interval_level": r.interval_level,
            "note": r.note, "model": r.model, "status": r.status.value, "explanation": r.explanation}
            for r in results],
        "uncertainty": run.uncertainty,
        "backtest": run.backtest,
        "seasonality": run.seasonality,
        "data_quality": run.data_quality,
        "assumptions": run.assumptions,
    }
    if detail:
        out["model_selection"] = run.selection
        out["basis"] = run.basis
        out["provenance"] = run.provenance
    else:
        out["backtest"] = None if run.backtest is None else {
            k: v for k, v in run.backtest.items() if k != "predictions"}
    return out


def forecasts_view(db: Session, application_id, metric: str | None = None) -> dict[str, Any] | None:
    stmt = select(ForecastRun).where(ForecastRun.application_id == application_id)
    if metric:
        stmt = stmt.where(ForecastRun.metric == metric)
    runs = sorted(db.scalars(stmt), key=lambda r: list(TARGETS).index(r.metric))
    if metric:
        return run_view(db, runs[0], detail=True) if runs else None
    return {"application_id": str(application_id), "built": bool(runs), "notes": NOTES,
            "engine_version": ENGINE_VERSION, "config_version": runs[0].config_version if runs else get_config().version,
            "forecasts": [run_view(db, r, detail=False) for r in runs]}
