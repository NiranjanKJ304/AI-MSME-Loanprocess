from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow
from app.models._types import enum_column
from app.models.enums import ApplicantType, ApplicationStatus, LoanType

if TYPE_CHECKING:
    from app.models.document import Document


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    business_name: Mapped[str] = mapped_column(String(255))
    applicant_type: Mapped[ApplicantType] = mapped_column(enum_column(ApplicantType))
    loan_type: Mapped[LoanType] = mapped_column(enum_column(LoanType))
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    loan_purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ApplicationStatus] = mapped_column(
        enum_column(ApplicationStatus), default=ApplicationStatus.CREATED
    )
    # Monotonic counter used to give each uploaded document a stable sequence number.
    document_sequence: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    documents: Mapped[list[Document]] = relationship(
        back_populates="application", order_by="Document.sequence_no"
    )
