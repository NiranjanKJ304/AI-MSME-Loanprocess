"""financial health

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06 16:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
DIMENSION = sa.Enum('REVENUE', 'PROFITABILITY', 'LIQUIDITY', 'CASH_FLOW', 'LEVERAGE', 'BUSINESS_STABILITY',
                    'TAX_COMPLIANCE', name='healthdimension', native_enum=False, length=40)
PERIOD_KIND = sa.Enum('ANNUAL', 'QUARTERLY', 'MONTHLY', 'PARTIAL_PERIOD', name='healthperiodkind',
                      native_enum=False, length=40)


def upgrade() -> None:
    op.create_table('financial_health_metrics',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('dimension', DIMENSION, nullable=False),
    sa.Column('metric', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=128), nullable=False),
    sa.Column('period_key', sa.String(length=64), nullable=False),
    sa.Column('period_kind', PERIOD_KIND, nullable=False),
    sa.Column('period_start', sa.Date(), nullable=True),
    sa.Column('period_end', sa.Date(), nullable=True),
    sa.Column('compare_period_key', sa.String(length=64), nullable=True),
    sa.Column('scope', sa.String(length=128), nullable=True),
    sa.Column('value', sa.Numeric(precision=24, scale=6), nullable=True),
    sa.Column('unit', sa.String(length=8), nullable=False),
    sa.Column('status', sa.Enum('AVAILABLE', 'NOT_AVAILABLE', 'PARTIAL', 'CONFLICTING', 'LOW_CONFIDENCE', name='factavailability', native_enum=False, length=40), nullable=False),
    sa.Column('formula', sa.Text(), nullable=False),
    sa.Column('inputs', JSON, nullable=False),
    sa.Column('details', JSON, nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('evidence', JSON, nullable=False),
    sa.Column('provenance', JSON, nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_financial_health_metrics_application_id'), 'financial_health_metrics', ['application_id'], unique=False)
    op.create_index(op.f('ix_financial_health_metrics_dimension'), 'financial_health_metrics', ['dimension'], unique=False)
    op.create_index(op.f('ix_financial_health_metrics_metric'), 'financial_health_metrics', ['metric'], unique=False)
    op.create_index(op.f('ix_financial_health_metrics_period_key'), 'financial_health_metrics', ['period_key'], unique=False)
    op.create_table('financial_health_indicators',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('dimension', DIMENSION, nullable=False),
    sa.Column('period_key', sa.String(length=64), nullable=False),
    sa.Column('period_kind', PERIOD_KIND, nullable=False),
    sa.Column('indicator', sa.Enum('STRONG', 'STABLE', 'DECLINING', 'WEAK', 'INSUFFICIENT_DATA', 'CONFLICTING_DATA', name='healthindicator', native_enum=False, length=40), nullable=False),
    sa.Column('rule', sa.Text(), nullable=False),
    sa.Column('evidence', JSON, nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('thresholds_version', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_financial_health_indicators_application_id'), 'financial_health_indicators', ['application_id'], unique=False)
    op.create_index(op.f('ix_financial_health_indicators_period_key'), 'financial_health_indicators', ['period_key'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_financial_health_indicators_period_key'), table_name='financial_health_indicators')
    op.drop_index(op.f('ix_financial_health_indicators_application_id'), table_name='financial_health_indicators')
    op.drop_table('financial_health_indicators')
    op.drop_index(op.f('ix_financial_health_metrics_period_key'), table_name='financial_health_metrics')
    op.drop_index(op.f('ix_financial_health_metrics_metric'), table_name='financial_health_metrics')
    op.drop_index(op.f('ix_financial_health_metrics_dimension'), table_name='financial_health_metrics')
    op.drop_index(op.f('ix_financial_health_metrics_application_id'), table_name='financial_health_metrics')
    op.drop_table('financial_health_metrics')
