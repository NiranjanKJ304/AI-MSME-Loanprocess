// Types mirroring the FastAPI response models (backend/app/schemas).

export type ApplicantType =
  | "SOLE_PROPRIETOR"
  | "PARTNERSHIP"
  | "LLP"
  | "PRIVATE_LIMITED"
  | "PUBLIC_LIMITED"
  | "OTHER";

export type LoanType = "TERM_LOAN" | "WORKING_CAPITAL" | "PERSONAL_BUSINESS_LOAN" | "MACHINERY_LOAN" | "OTHER";

export type DocumentStatus =
  | "UPLOADED"
  | "VALIDATING"
  | "VALID"
  | "INVALID"
  | "PROCESSING"
  | "PROCESSED"
  | "NEEDS_REVIEW"
  | "FAILED";

export type StageStatus = "PENDING" | "RUNNING" | "COMPLETED" | "COMPLETED_WITH_WARNINGS" | "FAILED" | "SKIPPED";
export type CheckStatus = "PASS" | "FAIL" | "INCONSISTENCY" | "NEEDS_REVIEW" | "INSUFFICIENT_DATA" | "SKIPPED";
export type Severity = "INFO" | "WARNING" | "ERROR";

export interface Application {
  id: string;
  application_number: string;
  business_name: string;
  applicant_type: ApplicantType;
  loan_type: LoanType;
  requested_amount: string;
  loan_purpose: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ApplicationListItem extends Application {
  document_count: number;
  documents_needing_review: number;
  documents_failed: number;
}

export interface PipelineStage {
  stage: string;
  status: StageStatus;
  records_processed: number;
  records_failed: number;
  warnings: number;
  error: string | null;
}

export interface DocumentSummary {
  id: string;
  application_id: string;
  document_code: string;
  sequence_no: number;
  filename: string;
  mime_type: string | null;
  detected_mime_type: string | null;
  file_size: number;
  sha256: string;
  document_type: string;
  classification_confidence: number | null;
  classification_method: string | null;
  type_overridden: boolean;
  document_status: DocumentStatus;
  page_count: number | null;
  duplicate_of_id: string | null;
  is_duplicate: boolean;
  extraction_confidence: number | null;
  processing_run: number;
  status_reasons: string[] | null;
  uploaded_at: string;
  processed_at: string | null;
  error_message: string | null;
  pipeline?: PipelineStage[];
}

export interface FileCheck {
  code: string;
  status: "PASS" | "FAIL" | "WARNING";
  message: string;
}

export interface DocumentDetail extends DocumentSummary {
  file_validation: {
    is_valid: boolean;
    checks: FileCheck[];
    pages_without_text?: number[];
    image_quality?: Record<string, unknown> | null;
  } | null;
  classification_evidence: {
    document_type: string;
    confidence: number;
    method: string;
    evidence: { signal: string; weight: number; detail: string }[];
    candidates: { document_type: string; score: number }[];
    warnings: string[];
  } | null;
  raw_extraction_key: string | null;
}

export interface StageRecord {
  stage: string;
  status: StageStatus;
  records_processed: number;
  records_failed: number;
  warnings_count: number;
  warnings: string[] | null;
  error: string | null;
  details: Record<string, unknown> | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Job {
  id: string;
  job_type: string;
  status: string;
  run_number: number;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  stages: StageRecord[];
}

export interface DocumentStatusResponse {
  document_id: string;
  document_code: string;
  document_status: DocumentStatus;
  processing_run: number;
  error_message: string | null;
  status_reasons: string[] | null;
  pipeline: PipelineStage[];
  latest_job: Job | null;
  upload_validation: Job | null;
}

export type ExtractionStatus =
  | "EXTRACTED"
  | "NOT_FOUND"
  | "LOW_CONFIDENCE"
  | "AMBIGUOUS"
  | "EXTRACTION_FAILED"
  | "OCR_REQUIRED"
  | "UNSUPPORTED";

export interface ExtractedField {
  id: string;
  document_id?: string;
  extraction_status?: ExtractionStatus;
  field_name: string;
  label: string | null;
  value_type: string;
  raw_value: string | null;
  normalized_value: string | null;
  confidence: number;
  is_missing: boolean;
  is_required: boolean;
  source_page: number | null;
  source_location: {
    bbox?: number[];
    page_width?: number;
    page_height?: number;
    components?: { label: string; value: string; page: number; bbox: number[] | null }[];
  } | null;
  source_snippet: string | null;
  source_table_id: string | null;
  source_row_index: number | null;
  extraction_method: string;
  validation_status: "PENDING" | "VALID" | "INVALID" | "NEEDS_REVIEW" | "MISSING";
  warnings: string[] | null;
  alternatives: Record<string, unknown>[] | null;
  processing_run: number;
}

export interface TableRow {
  id: string;
  row_index: number;
  page_number: number;
  bbox: number[] | null;
  row_kind: string;
  raw_cells: (string | null)[];
  parsed: Record<string, string | null> | null;
  status: "EXTRACTED" | "VALIDATED" | "NEEDS_REVIEW" | "FAILED";
  confidence: number | null;
  errors: string[] | null;
}

export interface ExtractedTable {
  id: string;
  table_index: number;
  page_number: number;
  page_end: number | null;
  table_type: string;
  title: string | null;
  extraction_method: string;
  header: (string | null)[] | null;
  column_mapping: Record<string, unknown> | null;
  row_count: number;
  rows_failed: number;
  rows_needs_review: number;
  confidence: number | null;
  warnings: string[] | null;
  rows: TableRow[];
}

export interface PageInfo {
  page_number: number;
  width: number | null;
  height: number | null;
  text_source: string;
  char_count: number;
  is_scanned: boolean;
  ocr_confidence: number | null;
  image_quality: Record<string, unknown> | null;
  warnings: { code: string; message: string }[] | null;
  raw_text?: string;
}

export interface Extraction {
  document_id: string;
  document_code: string;
  document_type: string;
  processing_run: number;
  confidence: number | null;
  fields: ExtractedField[];
  tables: ExtractedTable[];
  pages: PageInfo[];
  warnings: string[];
  summary: Record<string, unknown> & {
    fields_total: number;
    fields_extracted: number;
    fields_missing: number;
    required_missing: string[];
    transactions: number;
    rows_total: number;
    rows_failed: number;
    rows_by_status: Record<string, number>;
  };
}

export interface ValidationResult {
  id: string;
  document_id: string;
  field_id: string | null;
  row_id: string | null;
  scope: string;
  rule_code: string;
  field_name: string | null;
  status: CheckStatus;
  severity: Severity;
  message: string;
  expected: string | null;
  actual: string | null;
  source_page: number | null;
  details: Record<string, unknown> | null;
  created_at: string;
  document_code?: string;
  filename?: string;
  document_type?: string;
}

export interface ReconSource {
  document_id?: string;
  document_code: string;
  filename: string | null;
  document_type: string;
  field: string;
  value: string | null;
  normalized_value?: string | null;
  page: number | null;
  confidence: number | null;
}

export interface ReconciliationResult {
  id: string;
  check_code: string;
  check_group: string;
  status: CheckStatus;
  severity: Severity;
  confidence: number | null;
  message: string;
  sources: ReconSource[];
  details: Record<string, unknown> | null;
  created_at: string;
}

export interface RequirementItem {
  requirement_id: string;
  document_type: string;
  mandatory_status: "MANDATORY" | "CONDITIONAL" | "SUPPORTING";
  effective_required: boolean;
  condition: string | null;
  condition_met: boolean | null;
  priority: number;
  description: string;
  state: string;
  satisfied_via: string | null;
  documents: { id: string; document_code: string; filename: string; status: string; document_type: string }[];
}

export interface Completeness {
  policy: { name: string; version: string; disclaimer: string };
  items: RequirementItem[];
  summary: {
    required_total: number;
    required_received: number;
    required_missing: number;
    required_needs_review: number;
    pending_processing: number;
    officer_to_confirm: number;
    supporting_received: number;
    completeness_pct: number;
    unclassified_documents: number;
    missing_document_types: string[];
  };
}

export interface Overview {
  application: Application;
  documents_received: {
    total_uploaded: number;
    unique: number;
    duplicates: number;
    by_status: Record<string, number>;
    by_type: Record<string, number>;
  };
  documents_missing: { required_missing: string[]; officer_to_confirm: string[]; completeness_pct: number };
  extraction_quality: {
    average_document_confidence: number;
    fields_total: number;
    fields_extracted: number;
    fields_missing: number;
    required_fields_missing: number;
    low_confidence_fields: number;
    fields_by_status: Record<string, number>;
    table_rows_total: number;
    transactions: number;
    rows_failed: number;
    rows_needs_review: number;
  };
  validation_issues: {
    total: number;
    by_severity: Record<string, number>;
    by_rule: Record<string, number>;
    invalid_fields: number;
  };
  cross_document: {
    checks: number;
    inconsistencies: number;
    passed: number;
    insufficient_data: number;
    items: ReconciliationResult[];
  };
  processing_errors: {
    document_id: string;
    document_code: string;
    filename: string;
    status: string;
    error: string | null;
    reasons: string[] | null;
  }[];
  evidence_coverage: {
    fields_with_source_page_pct: number | null;
    fields_with_location_pct: number | null;
    reconciliation_checks_with_data_pct: number | null;
  };
  completeness: Completeness;
  documents: DocumentSummary[];
}

export interface AuditEntry {
  id: string;
  application_id: string | null;
  document_id: string | null;
  stage: string | null;
  action: string;
  status: string;
  actor: string;
  timestamp: string;
  error: string | null;
  source: Record<string, unknown> | null;
  details: Record<string, unknown> | null;
}

export interface Provenance {
  field: ExtractedField;
  document: { id: string; document_code: string; filename: string; document_type: string; sha256: string };
  page: { page_number: number; width: number; height: number; text_source: string } | null;
  table: { id: string; table_index: number; page_number: number; table_type: string; title: string | null } | null;
  row: TableRow | null;
  validation_results: ValidationResult[];
  explanation: string;
  extracted_at: string;
}

export interface Meta {
  disclaimer: string;
  enums: {
    applicant_types: ApplicantType[];
    loan_types: LoanType[];
    document_types: string[];
    pipeline_stages: string[];
  };
}
