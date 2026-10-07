"""risk features

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table('risk_feature_sets',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('feature_version', sa.String(length=16), nullable=False),
    sa.Column('definitions_hash', sa.String(length=64), nullable=False),
    sa.Column('source_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('config', JSON, nullable=False),
    sa.Column('definitions', JSON, nullable=False),
    sa.Column('upstream_versions', JSON, nullable=False),
    sa.Column('status_summary', JSON, nullable=False),
    sa.Column('data_quality_summary', JSON, nullable=False),
    sa.Column('validation', JSON, nullable=False),
    sa.Column('valid', sa.Boolean(), nullable=False),
    sa.Column('is_latest', sa.Boolean(), nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_risk_feature_sets_application_id'), 'risk_feature_sets', ['application_id'], unique=False)
    op.create_index(op.f('ix_risk_feature_sets_feature_version'), 'risk_feature_sets', ['feature_version'], unique=False)
    op.create_index(op.f('ix_risk_feature_sets_source_fingerprint'), 'risk_feature_sets', ['source_fingerprint'], unique=False)
    op.create_table('risk_features',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('feature_set_id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('feature_name', sa.String(length=64), nullable=False),
    sa.Column('feature_group', sa.String(length=32), nullable=False),
    sa.Column('feature_version', sa.String(length=16), nullable=False),
    sa.Column('value_numeric', sa.Numeric(precision=24, scale=6), nullable=True),
    sa.Column('value_text', sa.String(length=64), nullable=True),
    sa.Column('value_type', sa.String(length=8), nullable=False),
    sa.Column('unit', sa.String(length=16), nullable=False),
    sa.Column('period', sa.String(length=64), nullable=True),
    sa.Column('status', sa.Enum('AVAILABLE', 'NOT_AVAILABLE', 'PARTIAL', 'CONFLICTING', 'LOW_CONFIDENCE', name='factavailability', native_enum=False, length=40), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('source_layer', sa.String(length=32), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('calculation', JSON, nullable=False),
    sa.Column('provenance', JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['feature_set_id'], ['risk_feature_sets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('feature_set_id', 'feature_name', name='uq_feature_per_set')
    )
    op.create_index(op.f('ix_risk_features_application_id'), 'risk_features', ['application_id'], unique=False)
    op.create_index(op.f('ix_risk_features_feature_set_id'), 'risk_features', ['feature_set_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_risk_features_feature_set_id'), table_name='risk_features')
    op.drop_index(op.f('ix_risk_features_application_id'), table_name='risk_features')
    op.drop_table('risk_features')
    op.drop_index(op.f('ix_risk_feature_sets_source_fingerprint'), table_name='risk_feature_sets')
    op.drop_index(op.f('ix_risk_feature_sets_feature_version'), table_name='risk_feature_sets')
    op.drop_index(op.f('ix_risk_feature_sets_application_id'), table_name='risk_feature_sets')
    op.drop_table('risk_feature_sets')
