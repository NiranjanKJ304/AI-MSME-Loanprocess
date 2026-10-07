"""Proposed loan terms (officer / user provided - never assumed) and the repayment calculation.

Formulas (shown with every result):
  periodic rate        i = annual_interest_rate / 100 / periods_per_year     (12 monthly, 4 quarterly)
  installments         n = (tenure_months - grace_period_months) / months_per_period
  installment          P x i x (1 + i)^n / ((1 + i)^n - 1)                     if i > 0
                       P / n                                                    if i = 0
  grace period         INTEREST_ONLY: interest P x i paid each grace period, principal unchanged
                       CAPITALISED:   nothing paid, P grows to P x (1 + i)^(grace periods)
Schedule: interest = balance x i (rounded to paise); principal = installment - interest; the last
installment clears the remaining balance exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import GraceTreatment, RepaymentFrequency

PAISE = Decimal("0.01")
MONTHS_PER_PERIOD = {RepaymentFrequency.MONTHLY: 1, RepaymentFrequency.QUARTERLY: 3}


def q(v: Decimal) -> Decimal:
    return v.quantize(PAISE, rounding=ROUND_HALF_UP)


class LoanTermsIn(BaseModel):
    """Proposed loan terms. Every field that drives the repayment must be provided explicitly."""

    model_config = ConfigDict(extra="forbid")

    requested_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    annual_interest_rate: Decimal = Field(ge=0, le=100, description="Percent per year, e.g. 12.5; 0 allowed")
    tenure_months: int = Field(gt=0, le=600)
    repayment_frequency: RepaymentFrequency
    grace_period_months: int = Field(0, ge=0)
    grace_period_treatment: GraceTreatment | None = None
    provided_by: str | None = Field(None, max_length=128, description="Officer / user who supplied the terms")
    revenue_down_pct: float | None = Field(None, ge=0, lt=1, description="Stress override, e.g. 0.2")
    expense_up_pct: float | None = Field(None, ge=0, le=5, description="Stress override, e.g. 0.1")

    @model_validator(mode="after")
    def _consistent(self):
        validate_terms(self.tenure_months, self.repayment_frequency, self.grace_period_months,
                       self.grace_period_treatment)
        return self


def validate_terms(tenure: int, frequency: RepaymentFrequency, grace: int, treatment: GraceTreatment | None) -> None:
    pm = MONTHS_PER_PERIOD[frequency]
    if tenure <= 0:
        raise ValueError("tenure_months must be greater than 0")
    if grace < 0 or grace >= tenure:
        raise ValueError("grace_period_months must be >= 0 and shorter than the tenure")
    if tenure % pm or grace % pm:
        raise ValueError(f"tenure and grace period must be whole {frequency.value.lower()} periods "
                         f"(multiples of {pm} months)")
    if grace and treatment is None:
        raise ValueError("grace_period_treatment (INTEREST_ONLY or CAPITALISED) is required when there is a grace "
                         "period - it is never assumed")
    if not grace and treatment is not None:
        raise ValueError("grace_period_treatment given without a grace period")


@dataclass
class Repayment:
    frequency: RepaymentFrequency
    months_per_period: int
    periods_per_year: int
    periodic_rate: Decimal
    grace_periods: int
    installments: int
    principal: Decimal
    amortised_principal: Decimal  # principal after any capitalised grace interest
    installment: Decimal  # amortising installment (after grace)
    monthly_equivalent: Decimal  # installment / months per period (used for monthly debt service)
    total_repayment: Decimal
    total_interest: Decimal
    schedule: list[dict[str, Any]] = field(default_factory=list)
    formula: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"frequency": self.frequency.value, "months_per_period": self.months_per_period,
                "periods_per_year": self.periods_per_year, "periodic_rate": str(self.periodic_rate),
                "grace_periods": self.grace_periods, "installments": self.installments,
                "principal": str(self.principal), "amortised_principal": str(self.amortised_principal),
                "installment": str(self.installment), "monthly_equivalent": str(self.monthly_equivalent),
                "total_repayment": str(self.total_repayment), "total_interest": str(self.total_interest),
                "formula": self.formula, "inputs": self.inputs, "schedule": self.schedule}


def compute_repayment(amount: Decimal, annual_rate_pct: Decimal, tenure_months: int, frequency: RepaymentFrequency,
                      grace_months: int = 0, grace_treatment: GraceTreatment | None = None) -> Repayment:
    if amount is None or Decimal(amount) <= 0:
        raise ValueError("requested_amount must be greater than 0")
    if annual_rate_pct is None or Decimal(annual_rate_pct) < 0:
        raise ValueError("annual_interest_rate must be >= 0")
    if frequency not in MONTHS_PER_PERIOD:
        raise ValueError(f"unsupported repayment frequency {frequency!r}")
    validate_terms(tenure_months, frequency, grace_months, grace_treatment)
    amount, rate = Decimal(amount), Decimal(annual_rate_pct)
    pm = MONTHS_PER_PERIOD[frequency]
    ppy = 12 // pm
    i = rate / Decimal(100) / Decimal(ppy)
    g = grace_months // pm
    n = (tenure_months - grace_months) // pm

    schedule: list[dict[str, Any]] = []
    balance = amount
    for k in range(1, g + 1):
        interest = q(balance * i)
        if grace_treatment == GraceTreatment.CAPITALISED:
            closing = balance + interest
            schedule.append({"number": k, "month": k * pm, "type": "GRACE_CAPITALISED", "opening": str(balance),
                             "interest": str(interest), "principal": "0.00", "payment": "0.00", "closing": str(closing)})
            balance = closing
        else:
            schedule.append({"number": k, "month": k * pm, "type": "GRACE_INTEREST_ONLY", "opening": str(balance),
                             "interest": str(interest), "principal": "0.00", "payment": str(interest),
                             "closing": str(balance)})
    amortised = balance
    if i == 0:
        installment = q(amortised / n)
    else:
        f = (1 + i) ** n
        installment = q(amortised * i * f / (f - 1))
    for k in range(1, n + 1):
        interest = q(balance * i)
        principal = installment - interest if k < n else balance
        payment = principal + interest
        closing = balance - principal
        schedule.append({"number": g + k, "month": (g + k) * pm, "type": "AMORTISING", "opening": str(balance),
                         "interest": str(interest), "principal": str(principal), "payment": str(payment),
                         "closing": str(closing)})
        balance = closing
    total = sum((Decimal(r["payment"]) for r in schedule), Decimal(0))
    formula = ("installment = P x i x (1 + i)^n / ((1 + i)^n - 1)" if i else "installment = P / n (zero interest)")
    formula += f"; i = {rate}% / 100 / {ppy}; n = ({tenure_months} - {grace_months}) / {pm}"
    if g:
        formula += (f"; grace: {g} period(s) {grace_treatment.value}"
                    + (" (P grows by the unpaid interest)" if grace_treatment == GraceTreatment.CAPITALISED else
                       " (interest P x i paid each period)"))
    return Repayment(
        frequency=frequency, months_per_period=pm, periods_per_year=ppy, periodic_rate=i, grace_periods=g,
        installments=n, principal=amount, amortised_principal=amortised, installment=installment,
        monthly_equivalent=q(installment / pm), total_repayment=total, total_interest=total - amount,
        schedule=schedule, formula=formula,
        inputs={"requested_amount": str(amount), "annual_interest_rate_pct": str(rate),
                "tenure_months": tenure_months, "repayment_frequency": frequency.value,
                "grace_period_months": grace_months,
                "grace_period_treatment": grace_treatment.value if grace_treatment else None})
