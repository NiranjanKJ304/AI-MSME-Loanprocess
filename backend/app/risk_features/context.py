"""Loads every source layer of one application once (read-only) and fingerprints it.

The source fingerprint is a SHA-256 over the canonical content AND record ids of everything the
features read (facts, conflicts, monthly cash flow, classifications, patterns, health metrics,
forecasts, repayment analysis, documents, extracted fields). Same application + same source records
+ same feature definitions => same fingerprint => same feature values.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.financial_health.inputs import Snapshot
from app.models import (
    Application,
    Document,
    ExtractedField,
    FinancialHealthIndicator,
    FinancialHealthMetric,
    ForecastResult,
    ForecastRun,
    RepaymentAnalysis,
    RepaymentCapacityMetric,
    RepaymentLoanTerms,
    RepaymentScenario,
)
from app.models.enums import FactAvailability
from app.risk_features.definitions import FEATURE_CONFIG

A = FactAvailability


def _s(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if hasattr(v, "value") and not hasattr(v, "quantize"):  # enums
        return v.value
    return str(v)


class FeatureContext:
    def __init__(self, db: Session, application: Application):
        self.db, self.app = db, application
        aid = application.id
        self.s = Snapshot(db, aid)
        self.health = list(db.scalars(select(FinancialHealthMetric).where(FinancialHealthMetric.application_id == aid)))
        self.health_indicators = list(db.scalars(select(FinancialHealthIndicator)
                                                 .where(FinancialHealthIndicator.application_id == aid)))
        self.runs = {r.metric: r for r in db.scalars(select(ForecastRun).where(ForecastRun.application_id == aid))}
        self.results = {}
        for r in self.runs.values():
            self.results[r.metric] = list(db.scalars(select(ForecastResult).where(ForecastResult.run_id == r.id)
                                                     .order_by(ForecastResult.period_start)))
        self.analysis = db.scalars(select(RepaymentAnalysis).where(RepaymentAnalysis.application_id == aid)).first()
        self.terms = db.scalars(select(RepaymentLoanTerms).where(RepaymentLoanTerms.application_id == aid)).first()
        self.rep_metrics = list(db.scalars(select(RepaymentCapacityMetric).where(
            RepaymentCapacityMetric.analysis_id == self.analysis.id))) if self.analysis else []
        self.scenarios = list(db.scalars(select(RepaymentScenario).where(
            RepaymentScenario.analysis_id == self.analysis.id))) if self.analysis else []
        self.documents = list(db.scalars(select(Document).where(Document.application_id == aid)
                                         .order_by(Document.sequence_no)))
        doc_ids = [d.id for d in self.documents if d.duplicate_of_id is None]
        self.fields = list(db.scalars(select(ExtractedField).where(ExtractedField.document_id.in_(doc_ids)))) \
            if doc_ids else []

        # latest financial year with any non-banking fact
        fys = [p for p in self.s.fy_periods() if any(f.category != "BANKING" for f in self.s.facts_in(p))]
        self.latest_fy = fys[-1] if fys else None

        # bank window
        self.window = self.s.months[-FEATURE_CONFIG["bank_window_months"]:]
        self.complete = [m for m in self.window if m.availability != A.PARTIAL]
        self.partial = [m for m in self.window if m.availability == A.PARTIAL]
        self.missing: list[str] = []
        if self.window:
            idx = {m.month_start.year * 12 + m.month_start.month - 1 for m in self.window}
            self.missing = [f"{i // 12}-{i % 12 + 1:02d}" for i in range(min(idx), max(idx) + 1) if i not in idx]
        self.window_key = f"{self.window[0].month}..{self.window[-1].month}" if self.window else None

    # ------------------------------------------------------------------ lookups
    def health_metric(self, metric: str, period_key: str) -> FinancialHealthMetric | None:
        return next((m for m in self.health if m.metric == metric and m.period_key == period_key and m.scope is None),
                    None)

    def rep_metric(self, basis: str, metric: str) -> RepaymentCapacityMetric | None:
        return next((m for m in self.rep_metrics if m.basis == basis and m.metric == metric), None)

    def scenario(self, basis: str, name: str) -> RepaymentScenario | None:
        return next((s for s in self.scenarios if s.basis == basis and s.scenario == name), None)

    # ------------------------------------------------------------------ reproducibility
    def upstream_versions(self) -> dict[str, Any]:
        rules = sorted({c.rules_version for c in self.s.classifications.values()})
        return {
            "transaction_rules_version": rules or None,
            "health_thresholds_version": sorted({i.thresholds_version for i in self.health_indicators}) or None,
            "forecast_engine_version": sorted({r.engine_version for r in self.runs.values()}) or None,
            "forecast_config_version": sorted({r.config_version for r in self.runs.values()}) or None,
            "repayment_engine_version": self.analysis.engine_version if self.analysis else None,
            "repayment_config_version": self.analysis.config_version if self.analysis else None,
        }

    def fingerprint(self) -> str:
        s = self.s

        def rows(items, *cols):
            return sorted([[str(getattr(x, "id", ""))] + [_s(getattr(x, c)) for c in cols] for x in items])

        payload = {
            "application_id": str(self.app.id),
            "facts": rows(s.facts, "metric", "period_id", "value", "availability", "confidence"),
            "conflicts": rows(s.conflicts, "metric", "value_a", "value_b"),
            "months": rows(s.months, "month", "availability", "business_inflow", "business_outflow",
                           "financing_inflow", "financing_outflow", "transfer_inflow", "transfer_outflow",
                           "unknown_inflow", "unknown_outflow", "total_inflow", "total_outflow",
                           "net_operating_cash_flow", "confidence"),
            "classifications": rows(s.classifications.values(), "transaction_id", "category", "status", "nature",
                                    "link_type", "recurring_pattern_id", "excluded_from_aggregates"),
            "patterns": rows(s.patterns.values(), "pattern_type", "direction", "occurrences"),
            "health": rows(self.health, "metric", "period_key", "scope", "value", "status", "confidence"),
            "forecast_runs": rows(self.runs.values(), "metric", "status", "model", "engine_version", "config_version"),
            "forecast_results": rows([x for v in self.results.values() for x in v], "metric", "period_key",
                                     "predicted_value", "lower_bound", "upper_bound", "status"),
            "repayment": rows([self.analysis] if self.analysis else [], "status", "outcome", "engine_version",
                              "config_version"),
            "loan_terms": rows([self.terms] if self.terms else [], "requested_amount", "annual_interest_rate",
                               "tenure_months", "repayment_frequency", "grace_period_months", "updated_at"),
            "repayment_metrics": rows(self.rep_metrics, "basis", "metric", "value", "status"),
            "scenarios": rows(self.scenarios, "basis", "scenario", "dscr", "status"),
            "documents": rows(self.documents, "document_type", "document_status", "duplicate_of_id"),
            "fields": rows(self.fields, "field_name", "confidence", "is_missing", "extraction_status"),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
