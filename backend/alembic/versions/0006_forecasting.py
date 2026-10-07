"""forecasting

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def STATUS():
    return sa.Enum('AVAILABLE', 'NOT_AVAILABLE', 'PARTIAL', 'CONFLICTING', 'LOW_CONFIDENCE', name='factavailability',
                   native_enum=False, length=40)


def upgrade() -> None:
    op.create_table('forecast_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('metric', sa.String(length=48), nullable=False),
    sa.Column('frequency', sa.Enum('ANNUAL', 'MONTHLY', name='forecastfrequency', native_enum=False, length=40), nullable=False),
    sa.Column('engine_version', sa.String(length=64), nullable=False),
    sa.Column('config_version', sa.String(length=64), nullable=False),
    sa.Column('status', STATUS(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('model', sa.String(length=32), nullable=True),
    sa.Column('model_params', JSON, nullable=True),
    sa.Column('training_start', sa.String(length=64), nullable=True),
    sa.Column('training_end', sa.String(length=64), nullable=True),
    sa.Column('observations_used', sa.Integer(), nullable=False),
    sa.Column('observations_total', sa.Integer(), nullable=False),
    sa.Column('basis', sa.Text(), nullable=True),
    sa.Column('selection', JSON, nullable=False),
    sa.Column('backtest', JSON, nullable=True),
    sa.Column('uncertainty', JSON, nullable=False),
    sa.Column('seasonality', JSON, nullable=False),
    sa.Column('data_quality', JSON, nullable=False),
    sa.Column('assumptions', JSON, nullable=False),
    sa.Column('provenance', JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_forecast_runs_application_id'), 'forecast_runs', ['application_id'], unique=False)
    op.create_index(op.f('ix_forecast_runs_metric'), 'forecast_runs', ['metric'], unique=False)
    op.create_table('forecast_observations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('period_key', sa.String(length=64), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('value', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('source_status', STATUS(), nullable=False),
    sa.Column('used_for_training', sa.Boolean(), nullable=False),
    sa.Column('exclusion_reason', sa.Text(), nullable=True),
    sa.Column('outlier', sa.Boolean(), nullable=False),
    sa.Column('sources', JSON, nullable=False),
    sa.Column('evidence', JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['forecast_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_forecast_observations_application_id'), 'forecast_observations', ['application_id'], unique=False)
    op.create_index(op.f('ix_forecast_observations_run_id'), 'forecast_observations', ['run_id'], unique=False)
    op.create_table('forecast_results',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('metric', sa.String(length=48), nullable=False),
    sa.Column('period_key', sa.String(length=64), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('horizon', sa.Integer(), nullable=False),
    sa.Column('predicted_value', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('lower_bound', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('upper_bound', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('interval_level', sa.Float(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('model', sa.String(length=32), nullable=False),
    sa.Column('status', STATUS(), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['forecast_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_forecast_results_application_id'), 'forecast_results', ['application_id'], unique=False)
    op.create_index(op.f('ix_forecast_results_run_id'), 'forecast_results', ['run_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_forecast_results_run_id'), table_name='forecast_results')
    op.drop_index(op.f('ix_forecast_results_application_id'), table_name='forecast_results')
    op.drop_table('forecast_results')
    op.drop_index(op.f('ix_forecast_observations_run_id'), table_name='forecast_observations')
    op.drop_index(op.f('ix_forecast_observations_application_id'), table_name='forecast_observations')
    op.drop_table('forecast_observations')
    op.drop_index(op.f('ix_forecast_runs_metric'), table_name='forecast_runs')
    op.drop_index(op.f('ix_forecast_runs_application_id'), table_name='forecast_runs')
    op.drop_table('forecast_runs')
