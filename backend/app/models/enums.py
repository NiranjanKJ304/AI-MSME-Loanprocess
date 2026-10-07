"""All domain enumerations. Stored as VARCHAR (non-native enums) for portable migrations."""

from __future__ import annotations

from enum import StrEnum


class ApplicantType(StrEnum):
    SOLE_PROPRIETOR = "SOLE_PROPRIETOR"
    PARTNERSHIP = "PARTNERSHIP"
    LLP = "LLP"
    PRIVATE_LIMITED = "PRIVATE_LIMITED"
    PUBLIC_LIMITED = "PUBLIC_LIMITED"
    OTHER = "OTHER"


class LoanType(StrEnum):
    TERM_LOAN = "TERM_LOAN"
    WORKING_CAPITAL = "WORKING_CAPITAL"
    PERSONAL_BUSINESS_LOAN = "PERSONAL_BUSINESS_LOAN"
    MACHINERY_LOAN = "MACHINERY_LOAN"
    OTHER = "OTHER"


class ApplicationStatus(StrEnum):
    """Document-processing status of an application. There is deliberately no approval state."""

    CREATED = "CREATED"
    DOCUMENTS_RECEIVED = "DOCUMENTS_RECEIVED"
    PROCESSING = "PROCESSING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    PROCESSED = "PROCESSED"


class DocumentStatus(StrEnum):
    UPLOADED = "UPLOADED"
    VALIDATING = "VALIDATING"
    VALID = "VALID"
    INVALID = "INVALID"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class DocumentType(StrEnum):
    PAN = "PAN"
    KYC = "KYC"
    GST_CERTIFICATE = "GST_CERTIFICATE"
    GST_RETURN = "GST_RETURN"
    UDYAM = "UDYAM"
    BANK_STATEMENT = "BANK_STATEMENT"
    ITR = "ITR"
    PROFIT_LOSS = "PROFIT_LOSS"
    BALANCE_SHEET = "BALANCE_SHEET"
    CASH_FLOW = "CASH_FLOW"
    LOAN_STATEMENT = "LOAN_STATEMENT"
    BUSINESS_REGISTRATION = "BUSINESS_REGISTRATION"
    PARTNERSHIP_DEED = "PARTNERSHIP_DEED"
    LLP_AGREEMENT = "LLP_AGREEMENT"
    MOA = "MOA"
    AOA = "AOA"
    QUOTATION = "QUOTATION"
    BUSINESS_PLAN = "BUSINESS_PLAN"
    UNKNOWN = "UNKNOWN"


# Short codes used to build human-readable document references such as "GST-001".
DOCUMENT_TYPE_CODES: dict[DocumentType, str] = {
    DocumentType.PAN: "PAN",
    DocumentType.KYC: "KYC",
    DocumentType.GST_CERTIFICATE: "GST",
    DocumentType.GST_RETURN: "GSTR",
    DocumentType.UDYAM: "UDYAM",
    DocumentType.BANK_STATEMENT: "BANK",
    DocumentType.ITR: "ITR",
    DocumentType.PROFIT_LOSS: "PL",
    DocumentType.BALANCE_SHEET: "BS",
    DocumentType.CASH_FLOW: "CF",
    DocumentType.LOAN_STATEMENT: "LOAN",
    DocumentType.BUSINESS_REGISTRATION: "REG",
    DocumentType.PARTNERSHIP_DEED: "PDEED",
    DocumentType.LLP_AGREEMENT: "LLPA",
    DocumentType.MOA: "MOA",
    DocumentType.AOA: "AOA",
    DocumentType.QUOTATION: "QUOTE",
    DocumentType.BUSINESS_PLAN: "PLAN",
    DocumentType.UNKNOWN: "DOC",
}


class ClassificationMethod(StrEnum):
    FILENAME = "filename"
    RULE = "rule"
    LAYOUT = "layout"
    LLM = "llm"
    MANUAL = "manual"
    NONE = "none"


class TextSource(StrEnum):
    TEXT_LAYER = "TEXT_LAYER"  # native PDF text only
    OCR = "OCR"  # page rendered and OCR'd (no usable native text)
    HYBRID = "HYBRID"  # native text kept + OCR of image regions (mixed/scanned page with a thin text layer)
    SPREADSHEET = "SPREADSHEET"
    NONE = "NONE"  # no text could be obtained (see page warnings/errors)


class ExtractionStatus(StrEnum):
    """Per-field extraction outcome (distinct from validation status)."""

    EXTRACTED = "EXTRACTED"
    NOT_FOUND = "NOT_FOUND"  # text fully available, no candidate found
    LOW_CONFIDENCE = "LOW_CONFIDENCE"  # found, confidence below threshold
    AMBIGUOUS = "AMBIGUOUS"  # conflicting candidates of similar strength
    EXTRACTION_FAILED = "EXTRACTION_FAILED"  # labelled value present but could not be read/normalised
    OCR_REQUIRED = "OCR_REQUIRED"  # not found and part of the document has no text (scanned, OCR off/failed)
    UNSUPPORTED = "UNSUPPORTED"  # no parser for this document/field


class ExtractionMethod(StrEnum):
    REGEX = "REGEX"  # pattern match on text layer, with/without a label
    LABEL = "LABEL"  # label/value pair on the text layer
    TABLE = "TABLE"  # parsed from an extracted table
    LAYOUT = "LAYOUT"  # positional heuristics
    DERIVED = "DERIVED"  # deterministically derived from other extracted values (e.g. PAN 4th char)
    LLM = "LLM"  # LLM fallback, verified against source text
    MANUAL = "MANUAL"
    NONE = "NONE"  # nothing found (missing-field record)


class FieldValidationStatus(StrEnum):
    PENDING = "PENDING"
    VALID = "VALID"
    INVALID = "INVALID"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    MISSING = "MISSING"


class RowStatus(StrEnum):
    EXTRACTED = "EXTRACTED"
    VALIDATED = "VALIDATED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class RowKind(StrEnum):
    TRANSACTION = "TRANSACTION"
    OPENING_BALANCE = "OPENING_BALANCE"
    CLOSING_BALANCE = "CLOSING_BALANCE"
    SUMMARY = "SUMMARY"
    HEADER = "HEADER"
    CONTINUATION = "CONTINUATION"  # wrapped narration line, merged into the previous transaction
    LINE_ITEM = "LINE_ITEM"
    UNPARSED = "UNPARSED"


class TableType(StrEnum):
    TRANSACTIONS = "TRANSACTIONS"
    FINANCIAL_STATEMENT = "FINANCIAL_STATEMENT"
    GENERIC = "GENERIC"


class ProcessingStage(StrEnum):
    FILE_VALIDATION = "FILE_VALIDATION"
    CLASSIFICATION = "CLASSIFICATION"
    TEXT_EXTRACTION = "TEXT_EXTRACTION"
    TABLE_EXTRACTION = "TABLE_EXTRACTION"
    FIELD_EXTRACTION = "FIELD_EXTRACTION"
    FIELD_VALIDATION = "FIELD_VALIDATION"
    DOCUMENT_VALIDATION = "DOCUMENT_VALIDATION"
    CROSS_DOCUMENT_RECONCILIATION = "CROSS_DOCUMENT_RECONCILIATION"
    FINANCIAL_NORMALIZATION = "FINANCIAL_NORMALIZATION"
    TRANSACTION_INTELLIGENCE = "TRANSACTION_INTELLIGENCE"
    FINANCIAL_HEALTH = "FINANCIAL_HEALTH"
    FINANCIAL_FORECAST = "FINANCIAL_FORECAST"
    REPAYMENT_CAPACITY = "REPAYMENT_CAPACITY"
    COMPLETENESS_CHECK = "COMPLETENESS_CHECK"
    FINAL_DOCUMENT_STATUS = "FINAL_DOCUMENT_STATUS"


PIPELINE_STAGES: list[ProcessingStage] = list(ProcessingStage)


class StageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class JobStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    FAILED = "FAILED"


class JobType(StrEnum):
    UPLOAD_VALIDATION = "UPLOAD_VALIDATION"
    DOCUMENT_PROCESSING = "DOCUMENT_PROCESSING"
    APPLICATION_RECONCILIATION = "APPLICATION_RECONCILIATION"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class CheckStatus(StrEnum):
    """Outcome of a validation rule or reconciliation check.

    INCONSISTENCY is used for data that does not agree. It is never labelled fraud.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    INCONSISTENCY = "INCONSISTENCY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    SKIPPED = "SKIPPED"


class ValidationScope(StrEnum):
    FILE = "FILE"
    FIELD = "FIELD"
    ROW = "ROW"
    DOCUMENT = "DOCUMENT"


class MandatoryStatus(StrEnum):
    MANDATORY = "MANDATORY"
    CONDITIONAL = "CONDITIONAL"
    SUPPORTING = "SUPPORTING"


class RequirementState(StrEnum):
    RECEIVED = "RECEIVED"
    RECEIVED_NEEDS_REVIEW = "RECEIVED_NEEDS_REVIEW"
    RECEIVED_INVALID = "RECEIVED_INVALID"
    PENDING_PROCESSING = "PENDING_PROCESSING"
    MISSING = "MISSING"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    OFFICER_TO_CONFIRM = "OFFICER_TO_CONFIRM"


# --------------------------------------------------------------------------- canonical financial layer
class PeriodType(StrEnum):
    FY = "FY"  # Indian financial year 1 Apr - 31 Mar
    QUARTER = "QUARTER"  # FY quarter
    MONTH = "MONTH"
    CUSTOM = "CUSTOM"  # any other date range (e.g. a 7-month bank statement)


class MeasureType(StrEnum):
    FLOW = "FLOW"  # accumulated over the period (revenue, credits)
    STOCK = "STOCK"  # position at the end of the period (assets, balances)


class FactAvailability(StrEnum):
    """State of a value; missing values are never represented as zero."""

    AVAILABLE = "AVAILABLE"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    PARTIAL = "PARTIAL"
    CONFLICTING = "CONFLICTING"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


class FactSourceKind(StrEnum):
    EXTRACTED_FIELD = "EXTRACTED_FIELD"  # one extracted field
    DERIVED = "DERIVED"  # arithmetic over other facts (components recorded)
    BANK_TRANSACTIONS = "BANK_TRANSACTIONS"  # aggregate over canonical bank transactions


class TxnDirection(StrEnum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    UNKNOWN = "UNKNOWN"


class TxnCategory(StrEnum):
    CASH_DEPOSIT = "CASH_DEPOSIT"
    CASH_WITHDRAWAL = "CASH_WITHDRAWAL"
    BANK_CHARGES = "BANK_CHARGES"
    DEBT_PAYMENT = "DEBT_PAYMENT"
    OTHER = "OTHER"


class ConflictStatus(StrEnum):
    CONFLICTING = "CONFLICTING"  # recorded, deliberately not resolved in this phase


# --------------------------------------------------------------------------- transaction intelligence
class TxnGroup(StrEnum):
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"
    FINANCING = "FINANCING"
    TRANSFER = "TRANSFER"
    CASH = "CASH"
    OTHER = "OTHER"


class TxnClass(StrEnum):
    """Fine-grained transaction category (group in parentheses)."""

    BUSINESS_REVENUE = "BUSINESS_REVENUE"  # INCOME
    OTHER_BUSINESS_INCOME = "OTHER_BUSINESS_INCOME"  # INCOME
    INTEREST_INCOME = "INTEREST_INCOME"  # INCOME
    SUPPLIER_PAYMENT = "SUPPLIER_PAYMENT"  # EXPENSE
    SALARY = "SALARY"  # EXPENSE
    RENT = "RENT"  # EXPENSE
    UTILITIES = "UTILITIES"  # EXPENSE
    TAX_PAYMENT = "TAX_PAYMENT"  # EXPENSE
    BANK_CHARGES = "BANK_CHARGES"  # EXPENSE
    INSURANCE = "INSURANCE"  # EXPENSE
    OPERATING_EXPENSE = "OPERATING_EXPENSE"  # EXPENSE
    LOAN_DISBURSEMENT = "LOAN_DISBURSEMENT"  # FINANCING
    LOAN_REPAYMENT = "LOAN_REPAYMENT"  # FINANCING (EMI)
    INTEREST_PAYMENT = "INTEREST_PAYMENT"  # FINANCING
    OWN_ACCOUNT_TRANSFER = "OWN_ACCOUNT_TRANSFER"  # TRANSFER
    INTERNAL_TRANSFER = "INTERNAL_TRANSFER"  # TRANSFER
    RELATED_ACCOUNT_TRANSFER = "RELATED_ACCOUNT_TRANSFER"  # TRANSFER
    CASH_DEPOSIT = "CASH_DEPOSIT"  # CASH
    CASH_WITHDRAWAL = "CASH_WITHDRAWAL"  # CASH
    INVESTMENT = "INVESTMENT"  # OTHER
    REFUND = "REFUND"  # OTHER
    REVERSAL = "REVERSAL"  # OTHER
    UNKNOWN = "UNKNOWN"  # OTHER


class BusinessNature(StrEnum):
    """Separate dimension from the category: whose money movement is it?"""

    BUSINESS = "BUSINESS"
    PERSONAL = "PERSONAL"
    TRANSFER = "TRANSFER"
    FINANCING = "FINANCING"
    UNKNOWN = "UNKNOWN"


class ClassificationStatus(StrEnum):
    CLASSIFIED = "CLASSIFIED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"  # best category kept, evidence weak
    UNKNOWN = "UNKNOWN"  # evidence insufficient - not forced into a category


class CounterpartyType(StrEnum):
    BUSINESS_ENTITY = "BUSINESS_ENTITY"
    INDIVIDUAL = "INDIVIDUAL"
    BANK_OR_LENDER = "BANK_OR_LENDER"
    GOVERNMENT = "GOVERNMENT"
    OWN_ENTITY = "OWN_ENTITY"
    UNKNOWN = "UNKNOWN"


class RecurrenceFrequency(StrEnum):
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    IRREGULAR = "IRREGULAR"  # repeated counterparty without a regular interval


# --------------------------------------------------------------------------- financial health
class HealthDimension(StrEnum):
    REVENUE = "REVENUE"
    PROFITABILITY = "PROFITABILITY"
    LIQUIDITY = "LIQUIDITY"
    CASH_FLOW = "CASH_FLOW"
    LEVERAGE = "LEVERAGE"
    BUSINESS_STABILITY = "BUSINESS_STABILITY"
    TAX_COMPLIANCE = "TAX_COMPLIANCE"


class HealthIndicator(StrEnum):
    """Descriptive condition of one dimension in one period. Not a score and not a credit decision."""

    STRONG = "STRONG"
    STABLE = "STABLE"
    DECLINING = "DECLINING"
    WEAK = "WEAK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    CONFLICTING_DATA = "CONFLICTING_DATA"


class HealthPeriodKind(StrEnum):
    ANNUAL = "ANNUAL"  # a complete financial year
    QUARTERLY = "QUARTERLY"
    MONTHLY = "MONTHLY"
    PARTIAL_PERIOD = "PARTIAL_PERIOD"  # part of a financial year (e.g. a 7-month statement)


# --------------------------------------------------------------------------- forecasting
class ForecastFrequency(StrEnum):
    ANNUAL = "ANNUAL"
    MONTHLY = "MONTHLY"


# --------------------------------------------------------------------------- repayment capacity
class RepaymentFrequency(StrEnum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"


class GraceTreatment(StrEnum):
    """What happens to interest during a grace (moratorium) period - must be stated, never assumed."""

    INTEREST_ONLY = "INTEREST_ONLY"  # interest paid every period, principal deferred
    CAPITALISED = "CAPITALISED"  # nothing paid; interest added to the principal


class CapacityOutcome(StrEnum):
    """Descriptive outcome of a repayment-capacity analysis. Not a risk grade and not a decision."""

    ADEQUATE_DATA = "ADEQUATE_DATA"
    LIMITED_DATA = "LIMITED_DATA"
    LOW_CAPACITY = "LOW_CAPACITY"
    NEGATIVE_CAPACITY = "NEGATIVE_CAPACITY"
    CONFLICTING_DATA = "CONFLICTING_DATA"
