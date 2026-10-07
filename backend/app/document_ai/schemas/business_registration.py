from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class BusinessRegistrationSchema(BaseModel):
    """Certificate of incorporation / Shop & Establishment / Trade licence."""

    certificate_type: str | None = None
    registration_number: str | None = None  # CIN / LLPIN / licence number
    entity_name: str | None = None
    registration_date: date | None = None
    registering_authority: str | None = None
    address: str | None = None
