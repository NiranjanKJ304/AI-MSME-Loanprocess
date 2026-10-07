"""extraction artefacts: page blocks / text quality / errors, field extraction status

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("document_pages", sa.Column("blocks", JSONType, nullable=True))
    op.add_column("document_pages", sa.Column("text_quality", JSONType, nullable=True))
    op.add_column("document_pages", sa.Column("errors", JSONType, nullable=True))
    op.add_column(
        "extracted_fields",
        sa.Column(
            "extraction_status",
            sa.Enum("EXTRACTED", "NOT_FOUND", "LOW_CONFIDENCE", "AMBIGUOUS", "EXTRACTION_FAILED",
                    "OCR_REQUIRED", "UNSUPPORTED", name="extractionstatus", native_enum=False, length=40),
            nullable=False,
            server_default="EXTRACTED",
        ),
    )


def downgrade() -> None:
    op.drop_column("extracted_fields", "extraction_status")
    op.drop_column("document_pages", "errors")
    op.drop_column("document_pages", "text_quality")
    op.drop_column("document_pages", "blocks")
