"""ORM models. Importing this package registers every table on Base.metadata."""

from app.models.application import Application
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_page import DocumentPage
from app.models.extracted_field import ExtractedField
from app.models.extracted_table import ExtractedTable, ExtractedTableRow
from app.models.financial import BankTransaction, FinancialConflict, FinancialFact, FinancialPeriod
from app.models.forecast import ForecastObservation, ForecastResult, ForecastRun
from app.models.financial_health import FinancialHealthIndicator, FinancialHealthMetric
from app.models.repayment import (
    RepaymentAnalysis,
    RepaymentCapacityMetric,
    RepaymentLoanTerms,
    RepaymentScenario,
)
from app.models.risk_features import RiskFeature, RiskFeatureSet
from app.models.processing_job import ProcessingJob, ProcessingStageRecord
from app.models.transaction_intel import (
    CashflowMetric,
    CashflowMonthly,
    RecurringPattern,
    TransactionClassification,
)
from app.models.validation_result import ReconciliationResult, ValidationResult

__all__ = [
    "Application",
    "AuditLog",
    "BankTransaction",
    "CashflowMetric",
    "CashflowMonthly",
    "Document",
    "DocumentPage",
    "ExtractedField",
    "ExtractedTable",
    "ExtractedTableRow",
    "FinancialConflict",
    "FinancialFact",
    "FinancialHealthIndicator",
    "FinancialHealthMetric",
    "FinancialPeriod",
    "ForecastObservation",
    "ForecastResult",
    "ForecastRun",
    "ProcessingJob",
    "ProcessingStageRecord",
    "RecurringPattern",
    "ReconciliationResult",
    "RepaymentAnalysis",
    "RepaymentCapacityMetric",
    "RepaymentLoanTerms",
    "RepaymentScenario",
    "RiskFeature",
    "RiskFeatureSet",
    "TransactionClassification",
    "ValidationResult",
]
