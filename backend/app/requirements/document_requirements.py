"""Configurable document-requirement policy + completeness evaluation.

IMPORTANT: DEFAULT_POLICY below is a *prototype* policy written for demonstration. It is
NOT a statement of RBI rules or of any bank's credit policy. A bank replaces it by pointing
REQUIREMENTS_POLICY_FILE at a JSON file with the same structure (see README).
"""

from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import get_settings
from app.models import Application, Document
from app.models.enums import (
    ApplicantType,
    DocumentStatus,
    DocumentType,
    LoanType,
    MandatoryStatus,
    RequirementState,
)

AT, LT, DT, MS = ApplicantType, LoanType, DocumentType, MandatoryStatus


class Condition(BaseModel):
    """Machine-evaluable condition. `officer_confirmation` cannot be evaluated from data."""

    kind: Literal["always", "amount_gt", "amount_lte", "document_present", "officer_confirmation"]
    value: float | None = None
    document_type: DocumentType | None = None
    description: str


class DocumentRequirement(BaseModel):
    id: str
    document_type: DocumentType
    applicant_types: list[ApplicantType] | Literal["*"] = "*"
    loan_types: list[LoanType] | Literal["*"] = "*"
    mandatory_status: MandatoryStatus
    condition: Condition | None = None
    priority: int = Field(default=2, ge=1, le=3, description="1 = high")
    min_count: int = 1
    satisfied_by: list[DocumentType] = Field(default_factory=list)
    description: str = ""

    def specificity(self) -> int:
        return int(self.applicant_types != "*") + int(self.loan_types != "*")

    def applies_to(self, applicant: ApplicantType, loan: LoanType) -> bool:
        return (self.applicant_types == "*" or applicant in self.applicant_types) and (
            self.loan_types == "*" or loan in self.loan_types
        )


class RequirementPolicy(BaseModel):
    name: str
    version: str
    disclaimer: str
    requirements: list[DocumentRequirement]


ALWAYS = Condition(kind="always", description="Always required")
ENTITY_TYPES = [AT.PARTNERSHIP, AT.LLP, AT.PRIVATE_LIMITED, AT.PUBLIC_LIMITED]
COMPANIES = [AT.PRIVATE_LIMITED, AT.PUBLIC_LIMITED]

DEFAULT_POLICY = RequirementPolicy(
    name="PROTOTYPE_DEFAULT_POLICY",
    version="0.1",
    disclaimer=(
        "Prototype document checklist for demonstration only. It is not a regulatory or bank "
        "policy and must be replaced by the lending institution's own requirements."
    ),
    requirements=[
        DocumentRequirement(id="PAN-ALL", document_type=DT.PAN, mandatory_status=MS.MANDATORY, priority=1,
                            description="PAN of the business entity (or proprietor)"),
        DocumentRequirement(id="KYC-ALL", document_type=DT.KYC, mandatory_status=MS.MANDATORY, priority=1,
                            description="KYC of proprietor / partners / directors"),
        DocumentRequirement(id="BANK-ALL", document_type=DT.BANK_STATEMENT, mandatory_status=MS.MANDATORY,
                            priority=1, description="Bank statements of the main operating account(s), "
                            "ideally the last 12 months"),
        DocumentRequirement(id="ITR-ALL", document_type=DT.ITR, mandatory_status=MS.MANDATORY, priority=1,
                            description="Income tax return(s); 2-3 years recommended"),
        DocumentRequirement(id="UDYAM-ALL", document_type=DT.UDYAM, mandatory_status=MS.MANDATORY, priority=2,
                            description="Udyam registration certificate (MSME classification)"),
        DocumentRequirement(
            id="GSTC-ALL", document_type=DT.GST_CERTIFICATE, mandatory_status=MS.CONDITIONAL, priority=2,
            condition=Condition(kind="officer_confirmation",
                                description="Required if the business is GST-registered"),
            description="GST registration certificate (REG-06)"),
        DocumentRequirement(
            id="GSTR-ALL", document_type=DT.GST_RETURN, mandatory_status=MS.CONDITIONAL, priority=2,
            condition=Condition(kind="document_present", document_type=DT.GST_CERTIFICATE,
                                description="Required when a GST certificate is on file"),
            description="Recent GST returns (GSTR-3B / GSTR-1 / GSTR-9)"),
        DocumentRequirement(id="PL-ENT", document_type=DT.PROFIT_LOSS, applicant_types=ENTITY_TYPES,
                            mandatory_status=MS.MANDATORY, priority=1, description="Profit & loss statement"),
        DocumentRequirement(id="BS-ENT", document_type=DT.BALANCE_SHEET, applicant_types=ENTITY_TYPES,
                            mandatory_status=MS.MANDATORY, priority=1, description="Balance sheet"),
        DocumentRequirement(
            id="PL-PROP", document_type=DT.PROFIT_LOSS, applicant_types=[AT.SOLE_PROPRIETOR, AT.OTHER],
            mandatory_status=MS.CONDITIONAL, priority=2,
            condition=Condition(kind="amount_gt", value=1_000_000,
                                description="Required when the requested amount exceeds INR 10 lakh"),
            description="Profit & loss statement"),
        DocumentRequirement(
            id="BS-PROP", document_type=DT.BALANCE_SHEET, applicant_types=[AT.SOLE_PROPRIETOR, AT.OTHER],
            mandatory_status=MS.CONDITIONAL, priority=2,
            condition=Condition(kind="amount_gt", value=1_000_000,
                                description="Required when the requested amount exceeds INR 10 lakh"),
            description="Balance sheet"),
        DocumentRequirement(
            id="CF-TERM", document_type=DT.CASH_FLOW, loan_types=[LT.TERM_LOAN, LT.MACHINERY_LOAN],
            mandatory_status=MS.CONDITIONAL, priority=3,
            condition=Condition(kind="amount_gt", value=5_000_000,
                                description="Required when the requested amount exceeds INR 50 lakh"),
            description="Cash flow statement / projections"),
        DocumentRequirement(id="CF-ALL", document_type=DT.CASH_FLOW, mandatory_status=MS.SUPPORTING, priority=3,
                            description="Cash flow statement"),
        DocumentRequirement(
            id="LOAN-ALL", document_type=DT.LOAN_STATEMENT, mandatory_status=MS.CONDITIONAL, priority=2,
            condition=Condition(kind="officer_confirmation", description="Required if there are existing loans"),
            description="Statements of existing loans"),
        DocumentRequirement(
            id="REG-PROP", document_type=DT.BUSINESS_REGISTRATION, applicant_types=[AT.SOLE_PROPRIETOR, AT.OTHER],
            mandatory_status=MS.MANDATORY, priority=2,
            satisfied_by=[DT.GST_CERTIFICATE, DT.UDYAM],
            description="Proof of business existence (Shop & Establishment / trade licence; GST or Udyam accepted)"),
        DocumentRequirement(id="REG-ENT", document_type=DT.BUSINESS_REGISTRATION,
                            applicant_types=[AT.LLP, AT.PRIVATE_LIMITED, AT.PUBLIC_LIMITED],
                            mandatory_status=MS.MANDATORY, priority=1,
                            description="Certificate of incorporation"),
        DocumentRequirement(id="REG-PART", document_type=DT.BUSINESS_REGISTRATION, applicant_types=[AT.PARTNERSHIP],
                            mandatory_status=MS.SUPPORTING, priority=3,
                            description="Registration certificate of the firm (if registered)"),
        DocumentRequirement(id="PDEED", document_type=DT.PARTNERSHIP_DEED, applicant_types=[AT.PARTNERSHIP],
                            mandatory_status=MS.MANDATORY, priority=1, description="Partnership deed"),
        DocumentRequirement(id="LLPA", document_type=DT.LLP_AGREEMENT, applicant_types=[AT.LLP],
                            mandatory_status=MS.MANDATORY, priority=1, description="LLP agreement"),
        DocumentRequirement(id="MOA", document_type=DT.MOA, applicant_types=COMPANIES,
                            mandatory_status=MS.MANDATORY, priority=1, description="Memorandum of association"),
        DocumentRequirement(id="AOA", document_type=DT.AOA, applicant_types=COMPANIES,
                            mandatory_status=MS.MANDATORY, priority=1, description="Articles of association"),
        DocumentRequirement(id="QUOTE-MACH", document_type=DT.QUOTATION, loan_types=[LT.MACHINERY_LOAN],
                            mandatory_status=MS.MANDATORY, priority=1,
                            description="Quotation / proforma invoice for the machinery"),
        DocumentRequirement(
            id="QUOTE-TERM", document_type=DT.QUOTATION, loan_types=[LT.TERM_LOAN],
            mandatory_status=MS.CONDITIONAL, priority=2,
            condition=Condition(kind="officer_confirmation", description="Required if the loan funds an asset purchase"),
            description="Quotation for the asset being financed"),
        DocumentRequirement(
            id="PLAN-TERM", document_type=DT.BUSINESS_PLAN, loan_types=[LT.TERM_LOAN, LT.MACHINERY_LOAN],
            mandatory_status=MS.CONDITIONAL, priority=2,
            condition=Condition(kind="amount_gt", value=2_500_000,
                                description="Required when the requested amount exceeds INR 25 lakh"),
            description="Project report / business plan"),
        DocumentRequirement(id="PLAN-ALL", document_type=DT.BUSINESS_PLAN, mandatory_status=MS.SUPPORTING,
                            priority=3, description="Business plan"),
    ],
)


@lru_cache
def _load_policy(path: str) -> RequirementPolicy:
    return RequirementPolicy.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def get_policy() -> RequirementPolicy:
    path = get_settings().requirements_policy_file
    return _load_policy(path) if path else DEFAULT_POLICY


_STRICTNESS = {MS.MANDATORY: 3, MS.CONDITIONAL: 2, MS.SUPPORTING: 1}
_RECEIVED_STATES = {RequirementState.RECEIVED, RequirementState.RECEIVED_NEEDS_REVIEW}


def applicable_requirements(policy: RequirementPolicy, applicant: ApplicantType, loan: LoanType) -> list[DocumentRequirement]:
    chosen: dict[DocumentType, DocumentRequirement] = {}
    for r in policy.requirements:
        if not r.applies_to(applicant, loan):
            continue
        cur = chosen.get(r.document_type)
        if cur is None or (r.specificity(), _STRICTNESS[r.mandatory_status]) > (
            cur.specificity(), _STRICTNESS[cur.mandatory_status]
        ):
            chosen[r.document_type] = r
    return sorted(chosen.values(), key=lambda r: (-_STRICTNESS[r.mandatory_status], r.priority, r.document_type.value))


def _doc_state(docs: list[Document], min_count: int) -> tuple[RequirementState, list[Document]]:
    if not docs:
        return RequirementState.MISSING, []
    good = [d for d in docs if d.document_status == DocumentStatus.PROCESSED]
    review = [d for d in docs if d.document_status == DocumentStatus.NEEDS_REVIEW]
    pending = [d for d in docs if d.document_status in (
        DocumentStatus.UPLOADED, DocumentStatus.VALIDATING, DocumentStatus.VALID, DocumentStatus.PROCESSING)]
    if len(good) >= min_count:
        return RequirementState.RECEIVED, docs
    if len(good) + len(review) >= min_count:
        return RequirementState.RECEIVED_NEEDS_REVIEW, docs
    if pending:
        return RequirementState.PENDING_PROCESSING, docs
    return RequirementState.RECEIVED_INVALID, docs


def evaluate_completeness(application: Application, documents: list[Document]) -> dict[str, Any]:
    policy = get_policy()
    usable = [d for d in documents if d.duplicate_of_id is None]
    by_type: dict[DocumentType, list[Document]] = {}
    for d in usable:
        by_type.setdefault(d.document_type, []).append(d)
    present_types = {t for t, ds in by_type.items() if any(
        d.document_status in (DocumentStatus.PROCESSED, DocumentStatus.NEEDS_REVIEW) for d in ds)}

    items = []
    for req in applicable_requirements(policy, application.applicant_type, application.loan_type):
        docs = list(by_type.get(req.document_type, []))
        alt_docs = [d for t in req.satisfied_by for d in by_type.get(t, [])]
        state, matched = _doc_state(docs, req.min_count)
        satisfied_via = None
        if state not in _RECEIVED_STATES and alt_docs:
            alt_state, alt_matched = _doc_state(alt_docs, req.min_count)
            if alt_state in _RECEIVED_STATES:
                state, matched, satisfied_via = alt_state, alt_matched, "alternative"

        effective_required = req.mandatory_status == MS.MANDATORY
        condition_result: bool | None = None
        if req.mandatory_status == MS.CONDITIONAL and req.condition:
            c = req.condition
            if c.kind == "always":
                condition_result = True
            elif c.kind == "amount_gt":
                condition_result = Decimal(application.requested_amount) > Decimal(str(c.value))
            elif c.kind == "amount_lte":
                condition_result = Decimal(application.requested_amount) <= Decimal(str(c.value))
            elif c.kind == "document_present":
                condition_result = c.document_type in present_types
            else:
                condition_result = None  # officer must confirm
            effective_required = condition_result is True
            if state == RequirementState.MISSING:
                if condition_result is False:
                    state = RequirementState.NOT_APPLICABLE
                elif condition_result is None:
                    state = RequirementState.OFFICER_TO_CONFIRM
        items.append({
            "requirement_id": req.id,
            "document_type": req.document_type.value,
            "mandatory_status": req.mandatory_status.value,
            "effective_required": effective_required,
            "condition": req.condition.description if req.condition else None,
            "condition_met": condition_result,
            "priority": req.priority,
            "min_count": req.min_count,
            "description": req.description,
            "state": state.value,
            "satisfied_via": satisfied_via,
            "documents": [
                {"id": str(d.id), "document_code": d.document_code, "filename": d.filename,
                 "status": d.document_status.value, "document_type": d.document_type.value}
                for d in matched
            ],
        })

    required = [i for i in items if i["effective_required"]]
    received = [i for i in required if i["state"] in {s.value for s in _RECEIVED_STATES}]
    missing = [i for i in required if i["state"] in (RequirementState.MISSING.value, RequirementState.RECEIVED_INVALID.value)]
    unclassified = [d for d in usable if d.document_type == DocumentType.UNKNOWN]
    return {
        "policy": {"name": policy.name, "version": policy.version, "disclaimer": policy.disclaimer},
        "items": items,
        "summary": {
            "required_total": len(required),
            "required_received": len(received),
            "required_missing": len(missing),
            "required_needs_review": sum(1 for i in required if i["state"] == RequirementState.RECEIVED_NEEDS_REVIEW.value),
            "pending_processing": sum(1 for i in items if i["state"] == RequirementState.PENDING_PROCESSING.value),
            "officer_to_confirm": sum(1 for i in items if i["state"] == RequirementState.OFFICER_TO_CONFIRM.value),
            "supporting_received": sum(1 for i in items if i["mandatory_status"] == MS.SUPPORTING.value
                                       and i["state"] in {s.value for s in _RECEIVED_STATES}),
            "completeness_pct": round(100 * len(received) / len(required), 1) if required else 100.0,
            "unclassified_documents": len(unclassified),
            "missing_document_types": [i["document_type"] for i in missing],
        },
    }
