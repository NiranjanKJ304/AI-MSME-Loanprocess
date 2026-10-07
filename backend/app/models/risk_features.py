"""Versioned, reproducible risk-feature snapshots (input for FUTURE risk modelling - no model,
score, probability of default or decision is produced here).

  RiskFeatureSet  one snapshot: feature version, definitions hash, source fingerprint, configuration,
                  upstream layer versions, validation report, status / data-quality summary
  RiskFeature     one feature of a snapshot: value, unit, period, status, confidence, source layer,
                  calculation metadata and provenance

Snapshots are content-addressed: the same application + source records + feature definitions give the
same fingerprint and are not stored twice. Older snapshots are kept (is_latest = False) so a model can
always resolve the exact feature set it was given.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, JSONType, utcnow
from app.models._types import enum_column
from app.models.enums import FactAvailability


class RiskFeatureSet(Base):
    __tablename__ = "risk_feature_sets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    feature_version: Mapped[str] = mapped_column(String(16), index=True)
    definitions_hash: Mapped[str] = mapped_column(String(64))
    source_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType)
    definitions: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)  # full definitions used
    upstream_versions: Mapped[dict[str, Any]] = mapped_column(JSONType)
    status_summary: Mapped[dict[str, Any]] = mapped_column(JSONType)
    data_quality_summary: Mapped[dict[str, Any]] = mapped_column(JSONType)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONType)
    valid: Mapped[bool] = mapped_column(Boolean)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True)
    generated_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class RiskFeature(Base):
    __tablename__ = "risk_features"
    __table_args__ = (UniqueConstraint("feature_set_id", "feature_name", name="uq_feature_per_set"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    feature_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("risk_feature_sets.id", ondelete="CASCADE"),
                                                      index=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    feature_name: Mapped[str] = mapped_column(String(64))
    feature_group: Mapped[str] = mapped_column(String(32))
    feature_version: Mapped[str] = mapped_column(String(16))
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)  # NULL = no value
    value_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value_type: Mapped[str] = mapped_column(String(8))  # NUMBER | INTEGER | TEXT
    unit: Mapped[str] = mapped_column(String(16))
    period: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[FactAvailability] = mapped_column(enum_column(FactAvailability))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_layer: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    calculation: Mapped[dict[str, Any]] = mapped_column(JSONType)  # formula, method, inputs, details
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
