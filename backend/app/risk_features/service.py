"""Generates, stores and renders risk-feature snapshots.

generate (POST)  compute the features; if the latest snapshot already has the same feature version,
                 definitions hash and source fingerprint, return it (no duplicate) - otherwise store a
                 new snapshot and mark older ones is_latest = False.
rebuild          recompute and compare with the stored snapshot of the same fingerprint: identical
                 values => verified (idempotent, same snapshot id); any difference => stored as a new
                 snapshot and reported (it would indicate non-determinism).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, RiskFeature, RiskFeatureSet
from app.models.enums import FactAvailability
from app.risk_features.calculators import FeatureValue, compute_all
from app.risk_features.context import FeatureContext
from app.risk_features.definitions import (
    BY_NAME,
    DEFINITIONS,
    FEATURE_CONFIG,
    FEATURE_VERSION,
    GROUPS,
    definitions_hash,
)
from app.risk_features.validation import validate_set

A = FactAvailability
NOTES = [
    "Risk features are an auditable input vector for FUTURE risk modelling. No model is run: no risk score, "
    "probability of default, approval, rejection or recommendation.",
    "Missing data stays null with status NOT_AVAILABLE (never 0); conflicting data stays CONFLICTING (never resolved).",
    "Data-quality features describe evidence quality, not risk.",
]


@dataclass
class Generated:
    feature_set: RiskFeatureSet
    reused: bool = False
    verified_identical: bool | None = None
    differences: list[str] | None = None


def _q(v: Decimal | None, unit: str) -> Decimal | None:
    if v is None or not isinstance(v, Decimal):
        return v
    return v.quantize(Decimal("0.01")) if unit in ("INR", "INR_PER_MONTH") else v


def _compute(db: Session, application: Application):
    ctx = FeatureContext(db, application)
    values = compute_all(ctx)
    validation = validate_set(list(values.items()), ctx)
    return ctx, values, validation


def _signature(values: dict[str, FeatureValue]) -> dict[str, tuple]:
    out = {}
    for name, f in values.items():
        d = BY_NAME[name]
        v = f.value if d.value_type == "TEXT" else _q(f.value, d.unit)
        out[name] = (None if v is None else str(v), f.status.value, f.period)
    return out


def _stored_signature(fs: RiskFeatureSet, db: Session) -> dict[str, tuple]:
    out = {}
    for r in db.scalars(select(RiskFeature).where(RiskFeature.feature_set_id == fs.id)):
        v = r.value_text if r.value_type == "TEXT" else r.value_numeric
        out[r.feature_name] = (None if v is None else str(v), r.status.value, r.period)
    return out


def _norm(sig: dict[str, tuple]) -> dict[str, tuple]:
    # compare numbers at the stored precision (6 dp; INR 2 dp)
    out = {}
    for n, (v, s, p) in sig.items():
        d = BY_NAME[n]
        if v is not None and d.value_type != "TEXT":
            dv = Decimal(v)
            v = str(dv.quantize(Decimal("0.01")) if d.unit in ("INR", "INR_PER_MONTH") else dv.quantize(Decimal("0.000001")))
        out[n] = (v, s, p)
    return out


def _store(db: Session, application: Application, ctx: FeatureContext, values: dict[str, FeatureValue],
           validation: dict[str, Any], fingerprint: str) -> RiskFeatureSet:
    for old in db.scalars(select(RiskFeatureSet).where(RiskFeatureSet.application_id == application.id,
                                                       RiskFeatureSet.is_latest.is_(True))):
        old.is_latest = False
    statuses = Counter(f.status.value for f in values.values())
    dq = {n: (None if values[n].value is None else str(values[n].value), values[n].status.value)
          for n in (d.name for d in DEFINITIONS if d.group == "DATA_QUALITY")}
    fs = RiskFeatureSet(
        application_id=application.id, feature_version=FEATURE_VERSION, definitions_hash=definitions_hash(),
        source_fingerprint=fingerprint, config=FEATURE_CONFIG, definitions=[d.as_dict() for d in DEFINITIONS],
        upstream_versions=ctx.upstream_versions(),
        status_summary={"features": len(values), **{s.value: statuses.get(s.value, 0) for s in A}},
        data_quality_summary={"features": dq, "latest_fy": ctx.latest_fy.period_key if ctx.latest_fy else None,
                              "bank_window": ctx.window_key, "missing_inputs": len(validation["missing_inputs"]),
                              "conflicting_inputs": len(validation["conflicting_inputs"])},
        validation=validation, valid=validation["valid"], is_latest=True)
    db.add(fs)
    db.flush()
    for name, f in values.items():
        d = BY_NAME[name]
        text = d.value_type == "TEXT"
        db.add(RiskFeature(
            feature_set_id=fs.id, application_id=application.id, feature_name=name, feature_group=d.group,
            feature_version=d.version, value_numeric=None if text else _q(f.value, d.unit),
            value_text=f.value if text else None, value_type=d.value_type, unit=d.unit, period=f.period,
            status=f.status, confidence=f.confidence, source_layer=d.source_layer, reason=f.reason,
            calculation={"formula": d.formula, "method": "copied from source layer" if f.inputs and len(f.inputs) == 1
                         and f.inputs[0].get("source") in ("financial_health_metrics", "repayment_capacity_metrics",
                                                           "forecast_results") else "calculated",
                         "required_inputs": list(d.required_inputs), "inputs": f.inputs, "details": f.details},
            provenance=f.provenance))
    db.flush()
    return fs


def generate(db: Session, application: Application, *, rebuild: bool = False) -> Generated:
    ctx, values, validation = _compute(db, application)
    fingerprint = ctx.fingerprint()
    latest = db.scalars(select(RiskFeatureSet).where(
        RiskFeatureSet.application_id == application.id, RiskFeatureSet.is_latest.is_(True))).first()
    same = latest is not None and latest.feature_version == FEATURE_VERSION and \
        latest.definitions_hash == definitions_hash() and latest.source_fingerprint == fingerprint
    if same and not rebuild:
        return Generated(latest, reused=True)
    if same and rebuild:
        stored, fresh = _norm(_stored_signature(latest, db)), _norm(_signature(values))
        diffs = [f"{n}: stored {stored.get(n)} vs recomputed {fresh.get(n)}" for n in sorted(set(stored) | set(fresh))
                 if stored.get(n) != fresh.get(n)]
        if not diffs:
            return Generated(latest, reused=True, verified_identical=True, differences=[])
        fs = _store(db, application, ctx, values, validation, fingerprint)
        return Generated(fs, verified_identical=False, differences=diffs)
    return Generated(_store(db, application, ctx, values, validation, fingerprint))


# --------------------------------------------------------------------------- views
def feature_view(r: RiskFeature) -> dict[str, Any]:
    v: Any = r.value_text if r.value_type == "TEXT" else r.value_numeric
    if v is not None and r.value_type == "INTEGER":
        v = int(v)  # counts / months as JSON integers
    elif v is not None and r.value_type == "NUMBER":
        v = str(v.quantize(Decimal("0.01")) if r.unit in ("INR", "INR_PER_MONTH") else v)
    return {"feature_name": r.feature_name, "group": r.feature_group, "value": v, "value_type": r.value_type,
            "unit": r.unit, "period": r.period, "status": r.status.value, "confidence": r.confidence,
            "source_layer": r.source_layer, "feature_version": r.feature_version, "reason": r.reason,
            "description": BY_NAME[r.feature_name].description if r.feature_name in BY_NAME else None,
            "calculation": r.calculation, "provenance": r.provenance}


def set_view(db: Session, fs: RiskFeatureSet, gen: Generated | None = None) -> dict[str, Any]:
    rows = list(db.scalars(select(RiskFeature).where(RiskFeature.feature_set_id == fs.id)))
    order = {d.name: i for i, d in enumerate(DEFINITIONS)}
    rows.sort(key=lambda r: order.get(r.feature_name, 999))
    current = FeatureContext(db, db.get(Application, fs.application_id)).fingerprint()
    out = {
        "feature_set_id": str(fs.id), "application_id": str(fs.application_id), "built": True,
        "feature_version": fs.feature_version, "valid": fs.valid, "is_latest": fs.is_latest,
        "stale": current != fs.source_fingerprint, "notes": NOTES,
        "calculation_metadata": {
            "feature_version": fs.feature_version, "definitions_hash": fs.definitions_hash,
            "source_fingerprint": fs.source_fingerprint, "current_source_fingerprint": current,
            "config": fs.config, "upstream_versions": fs.upstream_versions,
            "generated_at": fs.generated_at.isoformat() if fs.generated_at else None},
        "status_summary": fs.status_summary,
        "data_quality_summary": fs.data_quality_summary,
        "validation": fs.validation,
        "groups": {g: [feature_view(r) for r in rows if r.feature_group == g] for g in GROUPS},
        "definitions": fs.definitions,
    }
    if gen is not None:
        out["reused"] = gen.reused
        out["verified_identical"] = gen.verified_identical
        out["differences"] = gen.differences
    return out


def latest_view(db: Session, application_id) -> dict[str, Any]:
    fs = db.scalars(select(RiskFeatureSet).where(RiskFeatureSet.application_id == application_id,
                                                 RiskFeatureSet.is_latest.is_(True))).first()
    if fs is None:
        return {"application_id": str(application_id), "built": False, "feature_version": FEATURE_VERSION,
                "notes": NOTES, "reason": "no feature snapshot yet - POST /risk-features to generate one"}
    return set_view(db, fs)
