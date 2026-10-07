"""Stores proposed loan terms, builds / persists the repayment-capacity analysis (deleted and rebuilt
on every run) and renders it for the API."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.financial_health.formulas import Input
from app.financial_health.inputs import Snapshot
from app.models import (
    Application,
    RepaymentAnalysis,
    RepaymentCapacityMetric,
    RepaymentLoanTerms,
    RepaymentScenario,
)
from app.models.enums import CapacityOutcome, FactAvailability
from app.repayment.config import ENGINE_VERSION, get_config
from app.repayment.engine import MetricOut, RepaymentEngine
from app.repayment.loan import LoanTermsIn
from app.utils.logging import audit

NOTES = [
    "Descriptive repayment-capacity analysis: no risk score, approval, rejection or credit recommendation.",
    "Loan terms are officer / user-provided inputs; repayment is calculated only from explicitly provided terms.",
    "Cash available for debt service uses classified business inflow / outflow only - never total bank credits, "
    "transfers, loan disbursements, personal credits or financing inflows.",
    "Missing values are never treated as zero; conflicting facts are never used in a formula.",
]


@dataclass
class RepaymentReport:
    built: bool = True
    status: str = ""
    outcome: str = ""
    metrics: int = 0
    scenarios: int = 0
    warnings: list[str] = field(default_factory=list)


def save_terms(db: Session, application: Application, terms: LoanTermsIn) -> RepaymentLoanTerms:
    row = db.scalars(select(RepaymentLoanTerms).where(RepaymentLoanTerms.application_id == application.id)).first()
    data = terms.model_dump()
    before = None
    if row is None:
        row = RepaymentLoanTerms(application_id=application.id, source="USER_PROVIDED")
        db.add(row)
    else:
        before = {k: str(getattr(row, k)) for k in data}
    for k, v in data.items():
        setattr(row, k, v)
    db.flush()
    audit(db, action="LOAN_TERMS_PROVIDED", status="UPDATED" if before else "CREATED", application_id=application.id,
          stage="REPAYMENT_CAPACITY", actor=terms.provided_by or "user",
          details={"terms": {k: str(v) for k, v in data.items()}, "previous": before})
    return row


def clear_repayment_analysis(db: Session, application_id) -> None:
    db.execute(delete(RepaymentScenario).where(RepaymentScenario.application_id == application_id))
    db.execute(delete(RepaymentCapacityMetric).where(RepaymentCapacityMetric.application_id == application_id))
    db.execute(delete(RepaymentAnalysis).where(RepaymentAnalysis.application_id == application_id))


def _provenance(m: MetricOut) -> dict[str, Any]:
    facts, months, txns, forecasts, terms = set(), set(), set(), set(), []

    def walk(inputs: list[Input]) -> None:
        for i in inputs:
            for s in i.sources:
                k = s.get("kind")
                if k == "FINANCIAL_FACT":
                    facts.add(s["fact_id"])
                elif k == "MONTHLY_CASHFLOW":
                    months.add(s["monthly_aggregate_id"])
                    txns.update(s.get("transaction_ids", []))
                elif k == "TRANSACTIONS":
                    txns.update(s.get("transaction_ids", []))
                elif k == "FORECAST":
                    forecasts.add(s["forecast_result_id"])
                elif k == "LOAN_TERMS" and s not in terms:
                    terms.append(s)

    walk(m.inputs)
    chain = {"HISTORICAL": "repayment metric -> monthly cash-flow aggregates -> classified transactions -> canonical "
                           "bank transactions -> statement rows -> document -> page/bbox",
             "FORECAST": "repayment metric -> forecast results -> forecast observations -> monthly cash-flow "
                         "aggregates -> classified transactions -> statement rows -> document -> page/bbox",
             "STATEMENT": "repayment metric -> canonical financial facts -> extracted fields -> document -> page/bbox"}
    return {"chain": chain[m.basis], "fact_ids": sorted(facts), "monthly_aggregate_ids": sorted(months),
            "transaction_ids": sorted(txns), "forecast_result_ids": sorted(forecasts),
            "user_provided_inputs": terms,
            "conflicts": [c for i in m.inputs for c in i.conflicts]}


def build_repayment_capacity(db: Session, application: Application) -> RepaymentReport:
    clear_repayment_analysis(db, application.id)
    db.flush()
    cfg = get_config()
    terms = db.scalars(select(RepaymentLoanTerms).where(RepaymentLoanTerms.application_id == application.id)).first()
    snap = Snapshot(db, application.id)
    a = RepaymentEngine(db, snap, terms, cfg).run()
    analysis = RepaymentAnalysis(
        application_id=application.id, loan_terms_id=terms.id if terms else None, engine_version=ENGINE_VERSION,
        config_version=cfg.version, status=a.status, outcome=a.outcome, outcome_reasons=a.reasons,
        loan_terms=a.terms, repayment=a.repayment.as_dict() if a.repayment else None, existing_debt=a.existing_debt,
        by_period=a.by_period, data_quality=a.checks, health_context=a.health_context, assumptions=a.assumptions,
        provenance={"chain": "repayment analysis -> loan terms (user-provided) + monthly cash flow + forecasts + "
                             "financial facts -> transactions / extracted fields -> document -> page/bbox",
                    "loan_terms_id": str(terms.id) if terms else None,
                    "monthly_aggregate_ids": a.monthly_aggregate_ids, "forecast_run_ids": a.forecast_run_ids})
    db.add(analysis)
    db.flush()
    for m in a.metrics:
        db.add(RepaymentCapacityMetric(
            analysis_id=analysis.id, application_id=application.id, basis=m.basis, metric=m.metric, label=m.label,
            period=m.period, value=m.calc.value, unit=m.unit, status=m.calc.status, formula=m.formula,
            inputs=[i.as_dict() for i in m.inputs], reason=m.calc.reason, evidence=snap.evidence_for(m.inputs),
            provenance=_provenance(m)))
    for s in a.scenarios:
        db.add(RepaymentScenario(
            analysis_id=analysis.id, application_id=application.id, scenario=s.scenario, basis=s.basis,
            assumption=s.assumption, business_inflow=s.inflow, business_outflow=s.outflow, cash_available=s.cash,
            existing_debt_service=s.existing, proposed_debt_service=s.proposed, total_debt_service=s.total,
            dscr=s.dscr, post_debt_service_cash_flow=s.post, status=s.status, reason=s.reason))
    db.flush()
    return RepaymentReport(status=a.status.value, outcome=a.outcome.value, metrics=len(a.metrics),
                           scenarios=len(a.scenarios),
                           warnings=[c["detail"] for c in a.checks if c["result"] == "WARN"])


# --------------------------------------------------------------------------- views
def _s(v) -> str | None:
    return None if v is None else str(v)


def _value(v: Decimal | None, unit: str) -> str | None:
    if v is None:
        return None
    return str(v.quantize(Decimal("0.01"))) if unit == "INR" else str(v)


def metric_view(m: RepaymentCapacityMetric) -> dict[str, Any]:
    return {"id": str(m.id), "basis": m.basis, "metric": m.metric, "label": m.label, "period": m.period,
            "value": _value(m.value, m.unit), "unit": m.unit, "status": m.status.value, "formula": m.formula,
            "inputs": m.inputs, "reason": m.reason, "evidence": m.evidence, "provenance": m.provenance}


def scenario_view(s: RepaymentScenario) -> dict[str, Any]:
    return {"scenario": s.scenario, "basis": s.basis, "assumption": s.assumption,
            "business_inflow": _s(s.business_inflow), "business_outflow": _s(s.business_outflow),
            "cash_available": _s(s.cash_available), "existing_debt_service": _s(s.existing_debt_service),
            "proposed_debt_service": _s(s.proposed_debt_service), "total_debt_service": _s(s.total_debt_service),
            "dscr": _s(s.dscr), "post_debt_service_cash_flow": _s(s.post_debt_service_cash_flow),
            "status": s.status.value, "reason": s.reason}


def repayment_view(db: Session, application_id) -> dict[str, Any]:
    a = db.scalars(select(RepaymentAnalysis).where(RepaymentAnalysis.application_id == application_id)).first()
    if a is None:
        return {"application_id": str(application_id), "built": False, "status": FactAvailability.NOT_AVAILABLE.value,
                "outcome": CapacityOutcome.LIMITED_DATA.value,
                "outcome_reasons": ["no repayment-capacity analysis yet - POST proposed loan terms"], "notes": NOTES}
    metrics = list(db.scalars(select(RepaymentCapacityMetric).where(RepaymentCapacityMetric.analysis_id == a.id)))
    scenarios = list(db.scalars(select(RepaymentScenario).where(RepaymentScenario.analysis_id == a.id)))
    order = ["BASE", "REVENUE_DOWN", "EXPENSE_UP", "COMBINED_STRESS"]
    by_basis = {b: [metric_view(m) for m in metrics if m.basis == b] for b in ("HISTORICAL", "FORECAST", "STATEMENT")}

    def pick(basis: str, metric: str) -> dict[str, Any] | None:
        return next((m for m in by_basis[basis] if m["metric"] == metric), None)

    return {
        "application_id": str(application_id), "built": True, "analysis_id": str(a.id),
        "engine_version": a.engine_version, "config_version": a.config_version,
        "status": a.status.value, "outcome": a.outcome.value, "outcome_reasons": a.outcome_reasons, "notes": NOTES,
        "loan_terms": a.loan_terms,
        "repayment": a.repayment,
        "existing_debt_service": {"historical": pick("HISTORICAL", "existing_debt_service"),
                                  "evidence": a.existing_debt},
        "proposed_debt_service": pick("HISTORICAL", "proposed_debt_service"),
        "cash_flow_capacity": by_basis,
        "dscr": {"historical": pick("HISTORICAL", "dscr"), "forecast": pick("FORECAST", "dscr"),
                 "statement": pick("STATEMENT", "statement_dscr")},
        "by_period": a.by_period,
        "stress_scenarios": [scenario_view(s) for s in sorted(scenarios, key=lambda s: (s.basis != "HISTORICAL",
                                                                                     order.index(s.scenario)))],
        "data_quality": a.data_quality,
        "health_context": a.health_context,
        "assumptions": a.assumptions,
        "provenance": a.provenance,
    }
