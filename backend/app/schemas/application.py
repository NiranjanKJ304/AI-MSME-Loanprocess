from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ApplicantType, ApplicationStatus, LoanType


class ApplicationCreate(BaseModel):
    business_name: str = Field(min_length=2, max_length=255)
    applicant_type: ApplicantType
    loan_type: LoanType
    requested_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    loan_purpose: str | None = Field(default=None, max_length=4000)


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_number: str
    business_name: str
    applicant_type: ApplicantType
    loan_type: LoanType
    requested_amount: Decimal
    loan_purpose: str | None
    status: ApplicationStatus
    created_at: datetime
    updated_at: datetime


class ApplicationListItem(ApplicationOut):
    document_count: int = 0
    documents_needing_review: int = 0
    documents_failed: int = 0
