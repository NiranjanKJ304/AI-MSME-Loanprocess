"""repayment capacity

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def STATUS():
    return sa.Enum('AVAILABLE', 'NOT_AVAILABLE', 'PARTIAL', 'CONFLICTING', 'LOW_CONFIDENCE', name='factavailability',
                   native_enum=False, length=40)


def upgrade() -> None:
    op.create_table('repayment_loan_terms',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('requested_amount', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('annual_interest_rate', sa.Numeric(precision=7, scale=4), nullable=False),
    sa.Column('tenure_months', sa.Integer(), nullable=False),
    sa.Column('repayment_frequency', sa.Enum('MONTHLY', 'QUARTERLY', name='repaymentfrequency', native_enum=False, length=40), nullable=False),
    sa.Column('grace_period_months', sa.Integer(), nullable=False),
    sa.Column('grace_period_treatment', sa.Enum('INTEREST_ONLY', 'CAPITALISED', name='gracetreatment', native_enum=False, length=40), nullable=True),
    sa.Column('revenue_down_pct', sa.Float(), nullable=True),
    sa.Column('expense_up_pct', sa.Float(), nullable=True),
    sa.Column('provided_by', sa.String(length=128), nullable=True),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_repayment_loan_terms_application_id'), 'repayment_loan_terms', ['application_id'], unique=True)
    op.create_table('repayment_analyses',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('loan_terms_id', sa.Uuid(), nullable=True),
    sa.Column('engine_version', sa.String(length=64), nullable=False),
    sa.Column('config_version', sa.String(length=64), nullable=False),
    sa.Column('status', STATUS(), nullable=False),
    sa.Column('outcome', sa.Enum('ADEQUATE_DATA', 'LIMITED_DATA', 'LOW_CAPACITY', 'NEGATIVE_CAPACITY', 'CONFLICTING_DATA', name='capacityoutcome', native_enum=False, length=40), nullable=False),
    sa.Column('outcome_reasons', JSON, nullable=False),
    sa.Column('loan_terms', JSON, nullable=True),
    sa.Column('repayment', JSON, nullable=True),
    sa.Column('existing_debt', JSON, nullable=False),
    sa.Column('by_period', JSON, nullable=False),
    sa.Column('data_quality', JSON, nullable=False),
    sa.Column('health_context', JSON, nullable=False),
    sa.Column('assumptions', JSON, nullable=False),
    sa.Column('provenance', JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['loan_terms_id'], ['repayment_loan_terms.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_repayment_analyses_application_id'), 'repayment_analyses', ['application_id'], unique=False)
    op.create_table('repayment_capacity_metrics',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('analysis_id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('basis', sa.String(length=16), nullable=False),
    sa.Column('metric', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=128), nullable=False),
    sa.Column('period', sa.String(length=64), nullable=True),
    sa.Column('value', sa.Numeric(precision=24, scale=6), nullable=True),
    sa.Column('unit', sa.String(length=8), nullable=False),
    sa.Column('status', STATUS(), nullable=False),
    sa.Column('formula', sa.Text(), nullable=False),
    sa.Column('inputs', JSON, nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('evidence', JSON, nullable=False),
    sa.Column('provenance', JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['analysis_id'], ['repayment_analyses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_repayment_capacity_metrics_analysis_id'), 'repayment_capacity_metrics', ['analysis_id'], unique=False)
    op.create_index(op.f('ix_repayment_capacity_metrics_application_id'), 'repayment_capacity_metrics', ['application_id'], unique=False)
    op.create_table('repayment_scenarios',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('analysis_id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('scenario', sa.String(length=24), nullable=False),
    sa.Column('basis', sa.String(length=16), nullable=False),
    sa.Column('assumption', sa.Text(), nullable=False),
    sa.Column('business_inflow', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('business_outflow', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('cash_available', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('existing_debt_service', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('proposed_debt_service', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('total_debt_service', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('dscr', sa.Numeric(precision=24, scale=6), nullable=True),
    sa.Column('post_debt_service_cash_flow', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('status', STATUS(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['analysis_id'], ['repayment_analyses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_repayment_scenarios_analysis_id'), 'repayment_scenarios', ['analysis_id'], unique=False)
    op.create_index(op.f('ix_repayment_scenarios_application_id'), 'repayment_scenarios', ['application_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_repayment_scenarios_application_id'), table_name='repayment_scenarios')
    op.drop_index(op.f('ix_repayment_scenarios_analysis_id'), table_name='repayment_scenarios')
    op.drop_table('repayment_scenarios')
    op.drop_index(op.f('ix_repayment_capacity_metrics_application_id'), table_name='repayment_capacity_metrics')
    op.drop_index(op.f('ix_repayment_capacity_metrics_analysis_id'), table_name='repayment_capacity_metrics')
    op.drop_table('repayment_capacity_metrics')
    op.drop_index(op.f('ix_repayment_analyses_application_id'), table_name='repayment_analyses')
    op.drop_table('repayment_analyses')
    op.drop_index(op.f('ix_repayment_loan_terms_application_id'), table_name='repayment_loan_terms')
    op.drop_table('repayment_loan_terms')
