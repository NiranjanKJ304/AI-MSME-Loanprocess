"""transaction intelligence

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06 15:32:18.436435
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    op.create_table('cashflow_metrics',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('metric', sa.String(length=64), nullable=False),
    sa.Column('value', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('unit', sa.String(length=8), nullable=False),
    sa.Column('availability', sa.Enum('AVAILABLE', 'NOT_AVAILABLE', 'PARTIAL', 'CONFLICTING', 'LOW_CONFIDENCE', name='factavailability', native_enum=False, length=40), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('months_used', sa.Integer(), nullable=False),
    sa.Column('months_total', sa.Integer(), nullable=False),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('provenance', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cashflow_metrics_application_id'), 'cashflow_metrics', ['application_id'], unique=False)
    op.create_index(op.f('ix_cashflow_metrics_metric'), 'cashflow_metrics', ['metric'], unique=False)
    op.create_table('recurring_patterns',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('group_key', sa.String(length=255), nullable=False),
    sa.Column('direction', sa.String(length=8), nullable=False),
    sa.Column('counterparty', sa.String(length=255), nullable=True),
    sa.Column('pattern_type', sa.String(length=40), nullable=False),
    sa.Column('frequency', sa.Enum('WEEKLY', 'MONTHLY', 'QUARTERLY', 'IRREGULAR', name='recurrencefrequency', native_enum=False, length=40), nullable=False),
    sa.Column('average_amount', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('min_amount', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('max_amount', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('amount_cv', sa.Float(), nullable=False),
    sa.Column('occurrences', sa.Integer(), nullable=False),
    sa.Column('distinct_months', sa.Integer(), nullable=False),
    sa.Column('first_seen', sa.Date(), nullable=False),
    sa.Column('last_seen', sa.Date(), nullable=False),
    sa.Column('median_interval_days', sa.Float(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('transaction_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('document_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_recurring_patterns_application_id'), 'recurring_patterns', ['application_id'], unique=False)
    op.create_table('cashflow_monthly',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('document_id', sa.Uuid(), nullable=True),
    sa.Column('month', sa.String(length=7), nullable=False),
    sa.Column('month_start', sa.Date(), nullable=False),
    sa.Column('month_end', sa.Date(), nullable=False),
    sa.Column('days_in_month', sa.Integer(), nullable=False),
    sa.Column('days_covered', sa.Integer(), nullable=True),
    sa.Column('business_inflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('business_revenue_inflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('business_outflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('financing_inflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('financing_outflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('transfer_inflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('transfer_outflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('cash_deposits', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('cash_withdrawals', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('personal_inflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('personal_outflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('other_inflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('other_outflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('unknown_inflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('unknown_outflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('excluded_inflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('excluded_outflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('total_inflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('total_outflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('net_operating_cash_flow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('low_confidence_business_inflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('low_confidence_business_outflow', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('txn_count', sa.Integer(), nullable=False),
    sa.Column('business_txn_count', sa.Integer(), nullable=False),
    sa.Column('unknown_txn_count', sa.Integer(), nullable=False),
    sa.Column('low_confidence_txn_count', sa.Integer(), nullable=False),
    sa.Column('excluded_txn_count', sa.Integer(), nullable=False),
    sa.Column('classification_coverage_count', sa.Float(), nullable=True),
    sa.Column('classification_coverage_amount', sa.Float(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('availability', sa.Enum('AVAILABLE', 'NOT_AVAILABLE', 'PARTIAL', 'CONFLICTING', 'LOW_CONFIDENCE', name='factavailability', native_enum=False, length=40), nullable=False),
    sa.Column('partial_reasons', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('provenance', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cashflow_monthly_application_id'), 'cashflow_monthly', ['application_id'], unique=False)
    op.create_index(op.f('ix_cashflow_monthly_document_id'), 'cashflow_monthly', ['document_id'], unique=False)
    op.create_table('transaction_classifications',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('transaction_id', sa.Uuid(), nullable=False),
    sa.Column('category', sa.Enum('BUSINESS_REVENUE', 'OTHER_BUSINESS_INCOME', 'INTEREST_INCOME', 'SUPPLIER_PAYMENT', 'SALARY', 'RENT', 'UTILITIES', 'TAX_PAYMENT', 'BANK_CHARGES', 'INSURANCE', 'OPERATING_EXPENSE', 'LOAN_DISBURSEMENT', 'LOAN_REPAYMENT', 'INTEREST_PAYMENT', 'OWN_ACCOUNT_TRANSFER', 'INTERNAL_TRANSFER', 'RELATED_ACCOUNT_TRANSFER', 'CASH_DEPOSIT', 'CASH_WITHDRAWAL', 'INVESTMENT', 'REFUND', 'REVERSAL', 'UNKNOWN', name='txnclass', native_enum=False, length=40), nullable=False),
    sa.Column('category_group', sa.Enum('INCOME', 'EXPENSE', 'FINANCING', 'TRANSFER', 'CASH', 'OTHER', name='txngroup', native_enum=False, length=40), nullable=False),
    sa.Column('nature', sa.Enum('BUSINESS', 'PERSONAL', 'TRANSFER', 'FINANCING', 'UNKNOWN', name='businessnature', native_enum=False, length=40), nullable=False),
    sa.Column('status', sa.Enum('CLASSIFIED', 'LOW_CONFIDENCE', 'UNKNOWN', name='classificationstatus', native_enum=False, length=40), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('raw_counterparty', sa.String(length=255), nullable=True),
    sa.Column('normalized_counterparty', sa.String(length=255), nullable=True),
    sa.Column('counterparty_type', sa.Enum('BUSINESS_ENTITY', 'INDIVIDUAL', 'BANK_OR_LENDER', 'GOVERNMENT', 'OWN_ENTITY', 'UNKNOWN', name='counterpartytype', native_enum=False, length=40), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=True),
    sa.Column('references', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('vpa', sa.String(length=255), nullable=True),
    sa.Column('evidence', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('candidates', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('linked_transaction_id', sa.Uuid(), nullable=True),
    sa.Column('link_type', sa.String(length=24), nullable=True),
    sa.Column('excluded_from_aggregates', sa.Boolean(), nullable=False),
    sa.Column('exclusion_reason', sa.Text(), nullable=True),
    sa.Column('recurring_pattern_id', sa.Uuid(), nullable=True),
    sa.Column('rules_version', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['linked_transaction_id'], ['bank_transactions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['recurring_pattern_id'], ['recurring_patterns.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['transaction_id'], ['bank_transactions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_transaction_classifications_application_id'), 'transaction_classifications', ['application_id'], unique=False)
    op.create_index(op.f('ix_transaction_classifications_category'), 'transaction_classifications', ['category'], unique=False)
    op.create_index(op.f('ix_transaction_classifications_normalized_counterparty'), 'transaction_classifications', ['normalized_counterparty'], unique=False)
    op.create_index(op.f('ix_transaction_classifications_transaction_id'), 'transaction_classifications', ['transaction_id'], unique=True)



def downgrade() -> None:

    op.drop_index(op.f('ix_transaction_classifications_transaction_id'), table_name='transaction_classifications')
    op.drop_index(op.f('ix_transaction_classifications_normalized_counterparty'), table_name='transaction_classifications')
    op.drop_index(op.f('ix_transaction_classifications_category'), table_name='transaction_classifications')
    op.drop_index(op.f('ix_transaction_classifications_application_id'), table_name='transaction_classifications')
    op.drop_table('transaction_classifications')
    op.drop_index(op.f('ix_cashflow_monthly_document_id'), table_name='cashflow_monthly')
    op.drop_index(op.f('ix_cashflow_monthly_application_id'), table_name='cashflow_monthly')
    op.drop_table('cashflow_monthly')
    op.drop_index(op.f('ix_recurring_patterns_application_id'), table_name='recurring_patterns')
    op.drop_table('recurring_patterns')
    op.drop_index(op.f('ix_cashflow_metrics_metric'), table_name='cashflow_metrics')
    op.drop_index(op.f('ix_cashflow_metrics_application_id'), table_name='cashflow_metrics')
    op.drop_table('cashflow_metrics')

