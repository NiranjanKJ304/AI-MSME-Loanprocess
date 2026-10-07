from __future__ import annotations

from enum import Enum

from sqlalchemy import Enum as SAEnum


def enum_column(enum_cls: type[Enum], length: int = 40) -> SAEnum:
    """Portable VARCHAR-backed enum (no native PG enum => simple, additive migrations)."""
    return SAEnum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=length,
        values_callable=lambda e: [m.value for m in e],
    )
