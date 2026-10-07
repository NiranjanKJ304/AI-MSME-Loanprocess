from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class PANSchema(BaseModel):
    """PAN card. entity_type is derived deterministically from the 4th PAN character."""

    pan: str | None = Field(default=None, description="10-character PAN, e.g. ABCDE1234F")
    name: str | None = None
    entity_type: str | None = Field(default=None, description="Individual / Firm / Company / ...")
    date_of_birth: date | None = Field(default=None, description="Date of birth / incorporation")
