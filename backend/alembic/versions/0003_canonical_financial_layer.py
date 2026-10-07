"""canonical financial layer

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06 14:50:42.054816
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    op.create_table('financial_periods',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('period_key', sa.String(length=64), nullable=False),
    sa.Column('period_type', sa.Enum('FY', 'QUARTER', 'MONTH', 'CUSTOM', name='periodtype', native_enum=False, length=40), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('fiscal_year', sa.String(length=16), nullable=True),
    sa.Column('label', sa.String(length=64), nullable=False),
    sa.Column('source_expressions', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('application_id', 'period_key', name='uq_period_per_application')
    )
    op.create_index(op.f('ix_financial_periods_application_id'), 'financial_periods', ['application_id'], unique=False)
    op.create_table('bank_transactions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('source_document_id', sa.Uuid(), nullable=False),
    sa.Column('source_row_id', sa.Uuid(), nullable=True),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('account_number', sa.String(length=64), nullable=True),
    sa.Column('transaction_date', sa.Date(), nullable=True),
    sa.Column('value_date', sa.Date(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('reference', sa.String(length=255), nullable=True),
    sa.Column('debit', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('credit', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('amount', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('balance', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('direction', sa.Enum('CREDIT', 'DEBIT', 'UNKNOWN', name='txndirection', native_enum=False, length=40), nullable=False),
    sa.Column('category', sa.Enum('CASH_DEPOSIT', 'CASH_WITHDRAWAL', 'BANK_CHARGES', 'DEBT_PAYMENT', 'OTHER', name='txncategory', native_enum=False, length=40), nullable=False),
    sa.Column('source_page', sa.Integer(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('row_status', sa.Enum('EXTRACTED', 'VALIDATED', 'NEEDS_REVIEW', 'FAILED', name='rowstatus', native_enum=False, length=40), nullable=True),
    sa.Column('provenance', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_row_id'], ['extracted_table_rows.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_bank_transactions_application_id'), 'bank_transactions', ['application_id'], unique=False)
    op.create_index(op.f('ix_bank_transactions_source_document_id'), 'bank_transactions', ['source_document_id'], unique=False)
    op.create_index(op.f('ix_bank_transactions_transaction_date'), 'bank_transactions', ['transaction_date'], unique=False)
    op.create_table('financial_facts',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('metric', sa.String(length=64), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('measure_type', sa.Enum('FLOW', 'STOCK', name='measuretype', native_enum=False, length=40), nullable=False),
    sa.Column('value', sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column('raw_value', sa.Text(), nullable=True),
    sa.Column('unit', sa.String(length=16), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('scale_applied', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('period_id', sa.Uuid(), nullable=True),
    sa.Column('period_type', sa.Enum('FY', 'QUARTER', 'MONTH', 'CUSTOM', name='periodtype', native_enum=False, length=40), nullable=True),
    sa.Column('period_start', sa.Date(), nullable=True),
    sa.Column('period_end', sa.Date(), nullable=True),
    sa.Column('as_of_date', sa.Date(), nullable=True),
    sa.Column('source_kind', sa.Enum('EXTRACTED_FIELD', 'DERIVED', 'BANK_TRANSACTIONS', name='factsourcekind', native_enum=False, length=40), nullable=False),
    sa.Column('source_document_id', sa.Uuid(), nullable=True),
    sa.Column('source_document_type', sa.Enum('PAN', 'KYC', 'GST_CERTIFICATE', 'GST_RETURN', 'UDYAM', 'BANK_STATEMENT', 'ITR', 'PROFIT_LOSS', 'BALANCE_SHEET', 'CASH_FLOW', 'LOAN_STATEMENT', 'BUSINESS_REGISTRATION', 'PARTNERSHIP_DEED', 'LLP_AGREEMENT', 'MOA', 'AOA', 'QUOTATION', 'BUSINESS_PLAN', 'UNKNOWN', name='documenttype', native_enum=False, length=40), nullable=True),
    sa.Column('source_field_id', sa.Uuid(), nullable=True),
    sa.Column('source_field_name', sa.String(length=128), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('extraction_status', sa.Enum('EXTRACTED', 'NOT_FOUND', 'LOW_CONFIDENCE', 'AMBIGUOUS', 'EXTRACTION_FAILED', 'OCR_REQUIRED', 'UNSUPPORTED', name='extractionstatus', native_enum=False, length=40), nullable=True),
    sa.Column('availability', sa.Enum('AVAILABLE', 'NOT_AVAILABLE', 'PARTIAL', 'CONFLICTING', 'LOW_CONFIDENCE', name='factavailability', native_enum=False, length=40), nullable=False),
    sa.Column('provenance', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('derivation', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('notes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['period_id'], ['financial_periods.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_field_id'], ['extracted_fields.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_financial_facts_application_id'), 'financial_facts', ['application_id'], unique=False)
    op.create_index(op.f('ix_financial_facts_metric'), 'financial_facts', ['metric'], unique=False)
    op.create_index(op.f('ix_financial_facts_period_id'), 'financial_facts', ['period_id'], unique=False)
    op.create_index(op.f('ix_financial_facts_source_document_id'), 'financial_facts', ['source_document_id'], unique=False)
    op.create_table('financial_conflicts',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('metric', sa.String(length=64), nullable=False),
    sa.Column('period_id', sa.Uuid(), nullable=True),
    sa.Column('fact_a_id', sa.Uuid(), nullable=False),
    sa.Column('fact_b_id', sa.Uuid(), nullable=False),
    sa.Column('value_a', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('value_b', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('source_a', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('source_b', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('difference', sa.Numeric(precision=20, scale=2), nullable=False),
    sa.Column('difference_pct', sa.Float(), nullable=False),
    sa.Column('tolerance', sa.Float(), nullable=False),
    sa.Column('status', sa.Enum('CONFLICTING', name='conflictstatus', native_enum=False, length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['fact_a_id'], ['financial_facts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['fact_b_id'], ['financial_facts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['period_id'], ['financial_periods.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_financial_conflicts_application_id'), 'financial_conflicts', ['application_id'], unique=False)
    op.create_index(op.f('ix_financial_conflicts_metric'), 'financial_conflicts', ['metric'], unique=False)



def downgrade() -> None:

    op.drop_index(op.f('ix_financial_conflicts_metric'), table_name='financial_conflicts')
    op.drop_index(op.f('ix_financial_conflicts_application_id'), table_name='financial_conflicts')
    op.drop_table('financial_conflicts')
    op.drop_index(op.f('ix_financial_facts_source_document_id'), table_name='financial_facts')
    op.drop_index(op.f('ix_financial_facts_period_id'), table_name='financial_facts')
    op.drop_index(op.f('ix_financial_facts_metric'), table_name='financial_facts')
    op.drop_index(op.f('ix_financial_facts_application_id'), table_name='financial_facts')
    op.drop_table('financial_facts')
    op.drop_index(op.f('ix_bank_transactions_transaction_date'), table_name='bank_transactions')
    op.drop_index(op.f('ix_bank_transactions_source_document_id'), table_name='bank_transactions')
    op.drop_index(op.f('ix_bank_transactions_application_id'), table_name='bank_transactions')
    op.drop_table('bank_transactions')
    op.drop_index(op.f('ix_financial_periods_application_id'), table_name='financial_periods')
    op.drop_table('financial_periods')

