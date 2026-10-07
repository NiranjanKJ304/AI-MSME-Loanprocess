from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class UdyamSchema(BaseModel):
    udyam_number: str | None = None
    enterprise_name: str | None = None
    registration_date: date | None = None
    enterprise_type: str | None = None  # Micro / Small / Medium
    major_activity: str | None = None  # Manufacturing / Services / Trading
    nic_code: str | None = None
