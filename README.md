# AI-MSME Loan Decision Intelligence Platform — Prototype Foundation

> **This is a prototype decision-support system, not a production banking underwriting system.**
> It ingests, classifies, extracts, validates and reconciles MSME loan documents and shows an
> officer exactly where every value came from. It does **not** score, approve or reject loans.
> Flagged items are *inconsistencies for review* — the system never labels anything as fraud.

Priority of this phase: **reliable document ingestion → classification → extraction → validation → traceability.**

---

## Contents

1. [Architecture](#architecture)
2. [Setup](#setup)
3. [Environment variables](#environment-variables)
4. [Database setup](#database-setup)
5. [Running the backend](#running-the-backend)
6. [Running the frontend](#running-the-frontend)
7. [Uploading documents](#uploading-documents)
8. [Processing pipeline](#processing-pipeline)
9. [Document schemas](#document-schemas)
10. [Validation rules](#validation-rules)
11. [Cross-document reconciliation](#cross-document-reconciliation)
12. [Document requirement policy](#document-requirement-policy)
13. [Financial health analysis](#financial-health-analysis)
14. [Financial forecasting](#financial-forecasting)
15. [Repayment capacity](#repayment-capacity)
16. [Risk feature engineering](#risk-feature-engineering)
17. [API endpoints](#api-endpoints)
18. [Testing](#testing)
19. [Known limitations](#known-limitations)

---

## Architecture

```text
SME-loanprocessor/                 (monorepo root)
├── backend/                       FastAPI + SQLAlchemy + Alembic (Python 3.12+ recommended; 3.11 works)
│   ├── app/
│   │   ├── main.py, config.py, database.py
│   │   ├── api/                   applications.py · documents.py · processing.py · deps.py
│   │   ├── models/                ORM: application, document, document_page, extracted_field,
│   │   │                          extracted_table (+rows), processing_job (+stages), validation_result,
│   │   │                          audit_log, enums
│   │   ├── schemas/               Pydantic API models
│   │   ├── ingestion/             file_validator · storage (local, write-once) · registry
│   │   ├── document_ai/           pdf_parser · ocr · image_quality · table_extractor · classifier ·
│   │   │                          extractor (common interface) · extractors/ · schemas/ (per doc type)
│   │   ├── llm/                   provider abstraction (none | anthropic), schema-validated JSON
│   │   ├── validation/            field_validation · document_validation · reconciliation · rules
│   │   ├── requirements/          configurable document requirement policy + completeness
│   │   ├── pipeline/              orchestrator · recorder (stage records, failure capture)
│   │   └── utils/                 hashing · logging (structured logs + audit writer)
│   ├── alembic/                   migrations (0001_initial_schema)
│   ├── scripts/                   synthetic_docs.py (fixture generator) · generate_samples.py · check_db.py
│   └── tests/                     pytest suite (79 tests)
├── frontend/                      Next.js 16 (App Router) + TypeScript + Tailwind CSS 4
│   ├── app/                       / · /applications/new · /applications/[id]/{,upload,processing,validation,audit}
│   │                              · /documents/[id]
│   ├── components/  lib/  types/
└── storage/                       local document storage (originals + raw extraction JSON)
```

Everything runs locally: a locally installed PostgreSQL server, the FastAPI backend and the Next.js
frontend. No Docker or other infrastructure is required.

**Design principles (enforced in code)**

| Rule | Where |
|---|---|
| Never invent missing values | Extractors emit `is_missing=True, value=null` records; schemas are all-nullable; LLM values must be found verbatim in the source text or are rejected |
| Never silently discard failures | `pipeline/recorder.py` wraps every stage; exceptions → stage `FAILED` + error text + traceback, document `FAILED`, later stages recorded as `SKIPPED` |
| Never silently drop table rows | Every raw row is persisted in `extracted_table_rows` with `row_kind` and `status` (`EXTRACTED / VALIDATED / NEEDS_REVIEW / FAILED`) |
| Provenance for every value | `extracted_fields` stores raw value, normalised value, confidence, source page, bounding box, snippet, table/row, extraction method |
| Raw layer separate from structured layer | `document_pages` (+ raw JSON artefact in storage, + `extracted_tables.raw_rows`) vs. `extracted_fields` |
| Preserve originals | Content-addressed, write-once storage (`storage/applications/<id>/originals/<sha256>.<ext>`) |
| Idempotent re-processing | Each run replaces the document's derived data; job/stage history and audit log are append-only |
| Deterministic before LLM | Rules + regex + tables first; LLM only as fallback, output validated against Pydantic schemas |
| No fraud labels / no approval | Mismatches are `INCONSISTENCY`; there are no approval endpoints, scores or states |

---

## Setup

Prerequisites: Python 3.12+ (3.11 also works), Node.js 20+, PostgreSQL 14+ installed locally
(developed against PostgreSQL 18 running as a Windows service on port 5432).
Optional: [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) for scanned documents.

```bash
# 1. Database (once) - create an empty database on the local PostgreSQL server
psql -U postgres -c "CREATE DATABASE msme_loans"

# 2. Backend
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                  # set DATABASE_URL user/password
python -m scripts.check_db            # verify the connection
alembic upgrade head                  # create the tables
python -m scripts.check_db            # should now report 11/11 tables, status OK

# 3. Frontend
cd ../frontend
npm install
cp .env.example .env.local
```

> **OneDrive note:** this folder lives inside OneDrive. `backend/.venv` and `frontend/node_modules`
> are large; consider excluding them from sync or creating the venv outside the synced folder.

---

## Environment variables

Backend (`backend/.env`, see `backend/.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://postgres@localhost:5432/msme_loans` | SQLAlchemy URL of the local PostgreSQL database |
| `STORAGE_DIR` | `../storage` | Local document storage root |
| `CORS_ORIGINS` | `http://localhost:3000` | Allowed frontend origins (comma-separated) |
| `MAX_FILE_SIZE_MB` | `25` | Upload size limit |
| `MAX_PDF_PAGES` | `300` | Page limit |
| `MIN_IMAGE_DIMENSION_PX` | `300` | Smallest acceptable image side |
| `CLASSIFICATION_CONFIDENCE_THRESHOLD` | `0.60` | Below → document `NEEDS_REVIEW` |
| `FIELD_CONFIDENCE_THRESHOLD` | `0.70` | Below → field `NEEDS_REVIEW` |
| `BALANCE_TOLERANCE` | `1.00` | INR tolerance for bank balance continuity |
| `NAME_MATCH_THRESHOLD` | `0.85` | Business-name similarity threshold |
| `RECONCILIATION_THRESHOLDS_FILE` | *(empty)* | JSON overriding per-check variance thresholds |
| `REQUIREMENTS_POLICY_FILE` | *(empty)* | JSON replacing the prototype document policy |
| `REPAYMENT_CONFIG_FILE` | *(empty)* | JSON replacing the repayment-capacity settings (`app/repayment/default_config.json`) |
| `FORECAST_CONFIG_FILE` | *(empty)* | JSON replacing the forecasting settings (`app/forecasting/default_config.json`) |
| `HEALTH_THRESHOLDS_FILE` | *(empty)* | JSON replacing the financial-health indicator thresholds (`app/financial_health/default_thresholds.json`) |
| `OCR_ENGINE` | `none` | `none` \| `tesseract` (`paddleocr` adapter is a stub) |
| `TESSERACT_CMD` | *(empty)* | Explicit tesseract binary path (Windows) |
| `OCR_LANGUAGES` / `OCR_DPI` | `eng` / `300` | OCR settings |
| `LLM_PROVIDER` | `none` | `none` \| `anthropic` |
| `LLM_MODEL` | `claude-opus-5-5` | Model id for the provider |
| `LLM_API_KEY` | *(empty)* | Provider key (Anthropic SDK can also use its own env/profile) |
| `LLM_MAX_INPUT_CHARS` | `12000` | Max document text sent to the LLM (tables never sent) |

Frontend (`frontend/.env.local`): `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`.

Tests only: `TEST_DATABASE_URL` — run the suite against a dedicated, disposable PostgreSQL database
(see [Testing](#testing)). Never point it at the application database.

---

## Database setup

The application uses a locally installed PostgreSQL server (no Docker).

```bash
psql -U postgres -c "CREATE DATABASE msme_loans"   # once
cd backend
python -m scripts.check_db                          # connection check (password hidden in output)
alembic upgrade head                                # creates all tables (migration 0001)
```

`scripts/check_db.py` prints the target, server version, Alembic revision and table count, and exits
`0` (OK), `1` (cannot connect — service stopped or wrong credentials) or `2` (connected, not migrated).
On Windows the server runs as the `postgresql-x64-18` service (`Get-Service postgresql*`).

* Enums are stored as `VARCHAR` (non-native) so future enum values need no `ALTER TYPE`.
* JSON columns are `JSONB` on PostgreSQL (portable `JSON` elsewhere — the test-suite uses SQLite).
* New migration after model changes: `alembic revision --autogenerate -m "..."`.

Tables: `applications`, `documents`, `document_pages`, `extracted_tables`, `extracted_table_rows`,
`extracted_fields`, `validation_results`, `reconciliation_results`, `processing_jobs`,
`processing_stages`, `audit_logs`.

---

## Running the backend

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

OpenAPI docs: <http://localhost:8000/docs> · health: <http://localhost:8000/health>

## Running the frontend

```bash
cd frontend
npm run dev            # http://localhost:3000
# production: npm run build && npm start
```

Pages:

| Page | Route | Shows |
|---|---|---|
| Applications | `/` | All applications with document/review counts |
| Application creation | `/applications/new` | Business name, applicant type, loan type, amount, purpose |
| Application overview | `/applications/[id]` | Documents received / missing, extraction quality, validation issues, cross-document inconsistencies, processing errors, evidence coverage |
| Document upload | `/applications/[id]/upload` | Multi-file drag & drop; Required / Conditional / Supporting checklist with live status |
| Processing dashboard | `/applications/[id]/processing` | Per-document pipeline (✓ / ! / ✕ / ⏳ per stage), counts, errors; re-process |
| Validation & reconciliation | `/applications/[id]/validation` | All findings, invalid files, cross-document checks with sources |
| Audit log | `/applications/[id]/audit` | Append-only trail |
| Document detail | `/documents/[id]` | Pipeline, file checks, classification evidence, fields (confidence, page, method, validation), tables/transactions with row status, raw page text, **evidence viewer**: click a field/row to outline its exact location on the rendered page and show its provenance |

---

## Uploading documents

1. Create an application (UI or `POST /api/applications`).
2. Upload any number of files (`POST /api/applications/{id}/documents`, multipart field `files`).
   Supported: PDF (text or scanned), PNG, JPG, TIFF, XLSX, CSV.
3. Every file gets a `documents` row immediately — including invalid and duplicate files, which are
   marked `INVALID` with an explicit reason (`DUPLICATE_DOCUMENT`, `PDF_PASSWORD`, `PDF_READABLE`, …).
4. Valid files are processed in the background (`auto_process=true`, default). The UI polls status.

**Sample application (synthetic, fictitious data):**

```bash
cd backend
python -m scripts.generate_samples                                   # writes PDFs to samples/generated/
python -m scripts.generate_samples --upload http://localhost:8000    # + creates an application and uploads
python -m scripts.generate_samples --upload http://localhost:8000 --with-negative   # + negative cases
```

The sample set is a Private Limited company (PAN, GST REG-06, GSTR-9, Udyam, Certificate of
Incorporation, ITR-6, P&L, Balance Sheet, 12-month 3-page bank statement) whose figures reconcile.
KYC / MOA / AOA are intentionally absent so the completeness engine has something to report.
The negative set covers balance mismatch, unparseable rows, other account holder, turnover mismatch,
invalid PAN, missing GST legal name, invalid GSTIN checksum, scanned/low-quality PDFs,
password-protected, corrupted, unknown and duplicate documents.

---

## Processing pipeline

```text
UPLOAD (registry: hash, store original, create record)
 ↓ FILE_VALIDATION            exists · size · extension · MIME sniffing · PDF readability/pages/password/
 ↓                            corruption · image decode/resolution/quality · SHA-256 duplicate check
 ↓ CLASSIFICATION             filename hints → text patterns → keywords → layout → LLM fallback
 ↓ TEXT_EXTRACTION            PyMuPDF text layer; OCR for pages without text; raw JSON artefact saved
 ↓ TABLE_EXTRACTION           pdfplumber ruled tables → layout-based transaction tables (header
 ↓                            carried across pages) → "label + amounts" financial tables
 ↓ FIELD_EXTRACTION           document-specific extractor → fields with provenance (+ missing records)
 ↓ FIELD_VALIDATION           format / checksum / date / numeric / confidence rules per field
 ↓ DOCUMENT_VALIDATION        balance continuity, statement totals, P&L/BS arithmetic, unparsed rows …
 ↓ CROSS_DOCUMENT_RECONCILIATION   (application level)
 ↓ FINANCIAL_NORMALIZATION         canonical facts / periods / conflicts / bank transactions
 ↓ TRANSACTION_INTELLIGENCE        transaction classification, patterns, monthly cash flow
 ↓ FINANCIAL_HEALTH                descriptive health metrics + indicators per period
 ↓ FINANCIAL_FORECAST              deterministic forecasts (revenue, monthly business cash flow)
 ↓ REPAYMENT_CAPACITY              descriptive repayment capacity (only when loan terms were provided)
 ↓ COMPLETENESS_CHECK              (application level)
 ↓ FINAL_DOCUMENT_STATUS           PROCESSED / NEEDS_REVIEW (+ reasons) / FAILED / INVALID
```

* Every stage writes a `processing_stages` record:
  `{"stage": "TABLE_EXTRACTION", "status": "COMPLETED_WITH_WARNINGS", "records_processed": 64, "records_failed": 1, "warnings": 2}`.
* A stage exception marks the stage `FAILED` (error + traceback stored), the document `FAILED`, records
  the remaining document stages as `SKIPPED`, and still runs the application-level stages so
  completeness/reconciliation stay truthful.
* Classification needs the text, so it parses the file in memory; TEXT/TABLE_EXTRACTION persist that raw layer.
* Re-processing (`POST /api/documents/{id}/process`) increments `processing_run`, replaces the
  document's pages/tables/rows/fields/validation results, and appends a new job — no duplicate records.
  Concurrent runs of the same document are refused (409); stale runs (> 30 min) are superseded.
* Officers can correct a document type (`PATCH /api/documents/{id}/type`); the override is audited and kept on re-runs.

**Document statuses:** `UPLOADED → VALIDATING → VALID | INVALID → PROCESSING → PROCESSED | NEEDS_REVIEW | FAILED`.
**Application statuses:** `CREATED, DOCUMENTS_RECEIVED, PROCESSING, NEEDS_REVIEW, PROCESSED` (no approval states).
An application is `PROCESSED` only when all documents processed cleanly, no required document is missing
and no cross-document inconsistency is open.

### Confidence model

* Labelled value matching its expected pattern ≈ 0.95; labelled value on the next line 0.85; unlabelled
  pattern match 0.75; table line item 0.93; format mismatch ≤ 0.5.
* OCR pages scale confidence by `0.5 + 0.5 × OCR confidence`; conflicting candidates −0.15 (alternatives kept).
* LLM-extracted values are capped at 0.60 — always below the review threshold.
* Classification: `strength × (0.6 + 0.4 × margin_to_runner_up)`; a filename alone (max 0.35 weight) can never pass.

### Where did this value come from?

`GET /api/fields/{field_id}/provenance` returns the field, document, page, table, row and an explanation:

```text
revenue: 68,20,000.00 (normalised: 6820000.00)
Source: 07_Profit_and_Loss_FY2024-25.pdf (PL-007)
Page: 1
Table: 0, row 1
Label: Revenue from Operations
Method: TABLE
Confidence: 93%
```

In the UI, clicking the field outlines the exact bounding box on the rendered page.

---

## Document schemas

All fields are nullable; missing values are recorded as `MISSING`, never guessed. (`*` = required)

| Type | Fields |
|---|---|
| PAN | `pan*`, `name*`, `entity_type` (derived from PAN 4th character, method `DERIVED`), `date_of_birth` |
| GST certificate | `gstin*`, `legal_name*`, `trade_name`, `registration_date`, `business_type`, `principal_address`, `status` |
| GST return | `gstin*`, `legal_name`, `return_type*`, `tax_period`, `financial_year`, `filing_date`, `taxable_turnover`, `total_tax` |
| Udyam | `udyam_number*`, `enterprise_name*`, `registration_date`, `enterprise_type`, `major_activity`, `nic_code` |
| Bank statement | `account_number*`, `account_holder*`, `bank_name`, `ifsc`, `period_start*`, `period_end*`, `opening_balance*`, `closing_balance*`, `transactions[]` (`date, value_date, description, reference, debit, credit, balance`) |
| ITR | `pan*`, `name`, `itr_form`, `assessment_year*`, `gross_total_income`, `business_income`, `taxable_income`, `tax_paid`, `turnover`, `filing_date`, `acknowledgement_number` |
| Profit & Loss | `period*`, `revenue*`, `cost_of_goods_sold`, `gross_profit`, `operating_expenses`, `ebitda`, `depreciation`, `interest`, `profit_before_tax*`, `tax`, `profit_after_tax*` |
| Balance sheet | `period*`, `fixed_assets`, `inventory`, `receivables`, `cash`, `bank_balance`, `other_current_assets`, `total_assets*`, `capital`, `reserves`, `borrowings` (sum of LT+ST components, `DERIVED`), `trade_payables`, `other_liabilities`, `total_liabilities*` |
| Business registration | `certificate_type`, `registration_number*`, `entity_name*`, `registration_date`, `registering_authority`, `address` |

Other types (KYC, cash flow, loan statement, deeds, MOA/AOA, quotation, business plan) are classified and
their raw text/tables preserved, with a `NO_STRUCTURED_SCHEMA` warning. The full catalogue is at `GET /api/meta`.

**Bank transaction rows:** `TRANSACTION`, `OPENING_BALANCE`, `CLOSING_BALANCE`, `SUMMARY`, `HEADER`,
`CONTINUATION` (wrapped narration merged into the previous transaction), `UNPARSED` (kept as `FAILED`
with the reason). Large tables are never sent to the LLM.

---

## Validation rules

**Field level** (`validation/field_validation.py`)

| Rule | Outcome |
|---|---|
| `REQUIRED_FIELD` | required field missing → FAIL / ERROR |
| `PAN_FORMAT`, `IFSC_FORMAT`, `UDYAM_FORMAT`, `CIN_FORMAT` | regex format |
| `GSTIN_FORMAT`, `GSTIN_CHECKSUM`, `GSTIN_STATE_CODE`, `GSTIN_EMBEDDED_PAN` | full GSTIN validation incl. mod-36 check digit |
| `DATE_VALID`, `DATE_NOT_FUTURE`, `DATE_PLAUSIBLE` | dates |
| `AMOUNT_NUMERIC`, `AMOUNT_NON_NEGATIVE` | numeric checks (sign rule only where a negative is impossible) |
| `PERIOD_FORMAT` | FY / AY recognisable |
| `LOW_CONFIDENCE`, `CONFLICTING_VALUES`, `LLM_SOURCED`, `PROVENANCE_MISSING` | → NEEDS_REVIEW |

**Document level** (`validation/document_validation.py`)

| Rule | Description |
|---|---|
| `BALANCE_CONTINUITY` | per row: previous balance + credit − debit ≈ balance (±`BALANCE_TOLERANCE`) → `INCONSISTENCY`, row `NEEDS_REVIEW` |
| `STATEMENT_TOTALS`, `CLOSING_MATCHES_LAST_BALANCE`, `SUMMARY_TOTALS` | statement-level arithmetic |
| `TXN_WITHIN_PERIOD`, `TXN_DATE_ORDER`, `OPENING_BALANCE_PRESENT`, `TRANSACTIONS_PRESENT` | statement sanity |
| `UNPARSED_TABLE_ROWS` | any `FAILED` row is surfaced |
| `PL_GROSS_PROFIT`, `PL_PROFIT_AFTER_TAX`, `PL_EBITDA_BRIDGE`, `PL_PAT_EXCEEDS_REVENUE` | P&L arithmetic |
| `BS_BALANCES`, `BS_ASSET_COMPONENTS`, `BS_LIABILITY_COMPONENTS` | balance sheet arithmetic |
| `ITR_TAXABLE_LE_GROSS`, `ITR_TAX_LE_INCOME` | ITR sanity |
| `PAGE_TEXT_UNAVAILABLE`, `LOW_OCR_CONFIDENCE`, `CLASSIFICATION_CONFIDENCE` | quality |

---

## Cross-document reconciliation

| Check | Compares |
|---|---|
| `PAN_CONSISTENCY` | PAN card ↔ GSTIN characters 3–12 (certificate and returns) ↔ ITR |
| `GSTIN_CONSISTENCY` | GST certificate ↔ GST returns |
| `BUSINESS_NAME_CONSISTENCY` | GST legal/trade name ↔ incorporation/registration ↔ PAN ↔ ITR ↔ bank account holder ↔ Udyam ↔ GST returns (+ declared application name). Normalises `PVT/PRIVATE`, `LTD/LIMITED`, `M/S`, `&`; similarity = max(sequence ratio, token Jaccard, token containment). Only deviating documents are flagged. |
| `TURNOVER_ITR_VS_PL`, `TURNOVER_GST_VS_PL`, `TURNOVER_GST_VS_ITR`, `TURNOVER_BANK_VS_PL`, `TURNOVER_BANK_VS_GST` | variance = \|a−b\| / max(\|a\|,\|b\|) against per-pair thresholds; GST from GSTR-9 or summed returns (annualised with a note if partial); bank = total credits, annualised (indicative only) |
| `FINANCIAL_PERIOD_ALIGNMENT` | P&L period ↔ balance-sheet period ↔ ITR (AY − 1) |
| `BANK_BALANCE_VS_BALANCE_SHEET` | statement closing balance ↔ balance-sheet bank balance when the statement ends on the BS date |

Default thresholds (`validation/reconciliation.py`, override with `RECONCILIATION_THRESHOLDS_FILE`):

```json
{ "TURNOVER_ITR_VS_PL": 0.05, "TURNOVER_GST_VS_PL": 0.10, "TURNOVER_GST_VS_ITR": 0.10,
  "TURNOVER_BANK_VS_PL": 0.25, "TURNOVER_BANK_VS_GST": 0.25, "BANK_BALANCE_VS_BS": 0.01 }
```

Result example:

```json
{ "check_code": "BUSINESS_NAME_CONSISTENCY", "status": "PASS", "confidence": 0.89,
  "sources": [{"document_code": "GST-002", "field": "legal_name", "page": 1, ...}, ...] }
```

Statuses: `PASS`, `INCONSISTENCY`, `INSUFFICIENT_DATA`.

---

## Document requirement policy

`requirements/document_requirements.py` ships a **`PROTOTYPE_DEFAULT_POLICY`** for demonstration.
It is *not* a regulatory or bank rule set. Each requirement has `document_type`, `applicant_types`,
`loan_types`, `mandatory_status` (`MANDATORY | CONDITIONAL | SUPPORTING`), `condition`, `priority`,
`min_count` and optional `satisfied_by` alternatives.

Conditions are machine-evaluable where possible (`amount_gt`, `amount_lte`, `document_present`, `always`);
`officer_confirmation` conditions show as *Officer to confirm*. Replace the policy with a JSON file:

```json
{
  "name": "BANK_X_MSME_POLICY", "version": "2026.1", "disclaimer": "...",
  "requirements": [
    {"id": "PAN-ALL", "document_type": "PAN", "mandatory_status": "MANDATORY", "priority": 1},
    {"id": "BS-PROP", "document_type": "BALANCE_SHEET", "applicant_types": ["SOLE_PROPRIETOR"],
     "mandatory_status": "CONDITIONAL",
     "condition": {"kind": "amount_gt", "value": 1000000, "description": "Loans above 10 lakh"}}
  ]
}
```

Requirement states: `RECEIVED`, `RECEIVED_NEEDS_REVIEW`, `RECEIVED_INVALID`, `PENDING_PROCESSING`,
`MISSING`, `NOT_APPLICABLE`, `OFFICER_TO_CONFIRM`.

---

## Financial health analysis

Describes the financial condition of the business per period from the canonical financial facts,
classified bank transactions and monthly cash flow (`backend/app/financial_health/`). It is **not** a
risk score, repayment-capacity measure, forecast or loan decision.

| Dimension | Metrics (formula) | Source |
|---|---|---|
| Revenue | revenue level; YoY change / growth `(rev_t - rev_t-1) / rev_t-1`; compound trend; annual consistency `1 - std/mean` (>= 3 years) | P&L revenue, else declared turnover (ITR / GST) — basis recorded, different bases never compared |
| Profitability | gross / operating / net margin `x / revenue`; PAT change / growth; net-margin change (pp); profit trend | P&L |
| Liquidity | cash & bank; current ratio `CA / CL`; `CA - CL`; average / minimum bank balance per account | Balance sheet, bank statements |
| Cash flow | business inflow / outflow; net business cash flow; cash-flow margin `net / inflow`; positive / negative months; positive-month share; classification coverage; unknown-credit share; monthly net cash flow | Classified transactions (INCOME / EXPENSE with BUSINESS nature only) |
| Leverage | borrowings; debt / revenue; debt / net worth; borrowings change / trend; debt payments and loan disbursements observed in bank data | Balance sheet, classified transactions |
| Business stability | business-inflow consistency; months with business inflow; business transactions per month; largest-customer share; recurring share of inflow | Monthly cash flow, counterparties, recurring patterns |
| Tax / compliance | ITR and GST return documents per FY; GST filing-period coverage; months without a GST return; source conflicts | Financial periods, conflicts |

Rules:

* **Statuses** `AVAILABLE / PARTIAL / NOT_AVAILABLE / CONFLICTING / LOW_CONFIDENCE` on every metric. Missing inputs
  stay missing (value `null`, never 0). A ratio is never calculated from a `CONFLICTING` fact — the metric is
  `CONFLICTING`, lists the conflicting sources, and the dimension indicator is `CONFLICTING_DATA`.
  Zero denominators and zero / negative bases are never divided by.
* **Periods**: facts are combined only within one canonical period. Trends compare consecutive financial years
  only. Bank metrics are grouped per FY: `ANNUAL` when all 12 months are covered, otherwise `PARTIAL_PERIOD` with
  status `PARTIAL` (observed values, explicitly partial). Monthly net cash flow is reported per month (`MONTHLY`).
  Bank-derived and financial-statement values are never mixed in one formula.
* **Indicators** per dimension and period: `STRONG / STABLE / DECLINING / WEAK / INSUFFICIENT_DATA / CONFLICTING_DATA`,
  each with the rule applied and the metrics used. Thresholds are configurable JSON and change indicators only, never values.
* **Explanations** are deterministic templates: what was calculated, period, formula, inputs (with status, basis and
  source pages), result or reason not calculated, status, evidence documents and pages.
* **Provenance**: metric → calculation → canonical facts → extracted fields → document → page/bbox, or
  metric → monthly aggregates → classified transactions → canonical transactions → statement rows → page/bbox.
  `GET /api/financial-health/metrics/{id}` resolves the chain.

## Financial forecasting

Deterministic statistical projections (`backend/app/forecasting/`) — not actuals, and not a repayment-capacity,
risk or credit assessment.

| Target | Frequency | History used |
|---|---|---|
| `revenue` | ANNUAL (next FY) | Canonical facts per FY: P&L revenue, else declared ITR / GST turnover (one basis only) |
| `business_inflow` | MONTHLY (next 3 months) | Combined monthly cash flow — credits classified INCOME with BUSINESS nature (never total bank credits) |
| `business_outflow` | MONTHLY | Debits classified EXPENSE with BUSINESS nature |
| `net_business_cash_flow` | MONTHLY | Business inflow − business outflow |

* **History**: every period with data is an observation. CONFLICTING (never resolved), PARTIAL, and different-basis
  observations are excluded with a reason; missing periods are never filled. The model trains on the most recent
  run of consecutive complete observations; forecasts start after the last observed period (`horizon` = steps
  after the last training observation).
* **Data-quality checks** (stored per run): observations, history length, consecutive periods, missing periods,
  partial periods, conflicting facts, comparable basis, low-confidence observations, classification coverage,
  UNKNOWN transaction share, outliers (robust z, flagged not changed), forecast-horizon gap. A FAIL means
  `NOT_AVAILABLE` (no forecast); any WARN means `LOW_CONFIDENCE`.
* **Models**: `naive` (latest value), `moving_average` (last k), `linear_trend` (OLS), and `seasonal_naive` only
  when seasonality is detected. Eligibility depends on history length; the model with the lowest MAE in a
  rolling-origin, one-step-ahead backtest (training on earlier observations only) is selected, ties going to the
  simpler model. All candidates' backtests (MAE, RMSE, MAPE where every actual is positive) are stored.
* **Seasonality**: monthly only, needs ≥ 24 consecutive complete months; otherwise `NOT_AVAILABLE`.
* **Uncertainty**: approximate 80% interval = forecast ± 1.2816 × backtest RMSE × √steps, only with enough
  backtest errors (annual 3, monthly 6); otherwise no interval and the reason. Negative projections of
  non-negative metrics are not reported.
* **Provenance**: forecast → historical observations → canonical facts → extracted fields → document → page/bbox, or
  → monthly cash-flow aggregates → classified / canonical transactions → statement rows → page/bbox. Every run
  stores the engine and config versions and the observation ids needed to reproduce it.

## Repayment capacity

Descriptive analysis of whether business cash flow covers existing and proposed debt service
(`backend/app/repayment/`). No risk score, approval, rejection or credit recommendation.

* **Loan terms** are officer / user-provided (`POST .../repayment-capacity`), stored as `USER_PROVIDED` and audited:
  `requested_amount`, `annual_interest_rate` (%, 0 allowed), `tenure_months`, `repayment_frequency`
  (`MONTHLY` / `QUARTERLY`), optional `grace_period_months` + `grace_period_treatment`
  (`INTEREST_ONLY` / `CAPITALISED`, required with a grace period), optional stress overrides. Nothing is assumed;
  without terms there is no repayment calculation.
* **Repayment**: installment `P·i·(1+i)^n / ((1+i)^n − 1)` (or `P / n` at 0%), full schedule (interest, principal,
  balance; last installment clears the balance), total repayment and interest. Invalid terms are rejected (422).
* **Cash available for debt service** = business inflow − business outflow (classified BUSINESS income / expense
  only — never total credits, transfers, loan disbursements, personal credits or financing inflows).
* **Existing debt service** = debits classified `LOAN_REPAYMENT` / `INTEREST_PAYMENT` only (recurring debits are not
  assumed to be EMIs); evidence lists the transactions, EMI patterns, UNKNOWN debits to lenders (not counted) and
  balance-sheet borrowings without observed repayments (→ `LOW_CONFIDENCE`).
* **DSCR** = cash available / (existing + proposed monthly debt service), for HISTORICAL (complete months of the
  last 12), FORECAST (forecast months; existing debt assumed to continue) and STATEMENT
  ((PAT + depreciation + interest) / annualised debt service). Post-debt-service cash flow per basis and per month.
* **Stress scenarios** `BASE / REVENUE_DOWN / EXPENSE_UP / COMBINED_STRESS` (default −20% inflow, +10% outflow).
* **Data quality**: loan terms, history coverage, partial / missing months, classification coverage, UNKNOWN credit /
  debit share, existing-debt evidence, forecast confidence, conflicting facts. Statuses
  `AVAILABLE / PARTIAL / NOT_AVAILABLE / CONFLICTING / LOW_CONFIDENCE`; missing values are never zero.
* **Outcome** (descriptive): `ADEQUATE_DATA / LIMITED_DATA / LOW_CAPACITY / NEGATIVE_CAPACITY / CONFLICTING_DATA`.
* **Provenance**: metric → monthly aggregates → classified / canonical transactions → statement rows → page/bbox;
  → forecast results; → financial facts → extracted fields → page/bbox; loan terms listed separately as user-provided.

## Risk feature engineering

A versioned, auditable feature vector for **future** risk modelling (`backend/app/risk_features/`). No model is
trained or run: no risk score, probability of default, approval, rejection or recommendation.

* **Centralized definitions** (`definitions.py`): 68 features in 6 groups — BUSINESS_FINANCIALS, TRANSACTION,
  STABILITY, FORECAST, REPAYMENT, DATA_QUALITY — each with name, description, unit, value type, formula, source
  layer, required inputs, period scope and version. One calculator per definition (`calculators.py`), checked
  one-to-one at import. `FEATURE_VERSION = "v1"`; the definitions hash covers definitions + calculation config.
* **Sources**: persisted outputs of the earlier layers only (financial facts, health metrics, monthly cash flow,
  classifications, recurring patterns, forecasts, repayment metrics / scenarios, documents, extracted fields).
* **Every feature**: value (numeric or text), unit, period, status (`AVAILABLE / PARTIAL / NOT_AVAILABLE /
  CONFLICTING / LOW_CONFIDENCE`), confidence, source layer, calculation (formula, inputs, details), provenance.
  Missing → null + `NOT_AVAILABLE` (never 0); conflicting → null + `CONFLICTING` (never resolved). Historical,
  forecast and statement DSCR are separate features.
* **Validation**: value types, units, statuses, null rules, period consistency (latest FY / bank window),
  duplicate names, provenance present, missing and conflicting inputs reported.
* **Snapshots**: content-addressed by a SHA-256 source fingerprint (record ids + content). `POST` reuses an
  identical snapshot; `rebuild` recomputes and verifies identical values (same id); changed sources create a new
  snapshot and older ones are kept (`is_latest = false`). `GET` reports whether the snapshot is stale.
  Upstream layer versions (rules, thresholds, forecast / repayment engine and config) are stored with each snapshot.

## API endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/api/applications` | Create application |
| GET | `/api/applications` | List applications |
| GET | `/api/applications/{id}` | Application |
| POST | `/api/applications/{id}/documents` | Upload files (multipart `files`, `?auto_process=true`) |
| GET | `/api/applications/{id}/documents` | Documents with pipeline stage summary |
| GET | `/api/applications/{id}/validation` | All validation findings + invalid files |
| GET | `/api/applications/{id}/reconciliation` | Cross-document checks |
| GET | `/api/applications/{id}/completeness` | Requirement checklist evaluation |
| POST | `/api/documents/{id}/process` | (Re-)process (`?background=true` to return immediately) |
| GET | `/api/documents/{id}/status` | Status + per-stage pipeline + latest job |
| GET | `/api/documents/{id}/extraction` | Fields, tables, rows, pages, warnings, summary |
| *extra* GET | `/api/applications/{id}/overview` | Dashboard aggregate (no approval score) |
| *extra* GET | `/api/applications/{id}/audit` | Audit log |
| *extra* POST | `/api/applications/{id}/process` · `/reconcile` | Process pending/failed docs · re-run app stages |
| *extra* GET | `/api/documents/{id}` · `/validation` · `/pages` · `/raw` · `/file` · `/pages/{n}/image` | Detail, findings, raw text, raw artefact, original file, rendered page |
| *extra* PATCH | `/api/documents/{id}/type` | Officer type override (audited) |
| *extra* GET | `/api/fields/{id}/provenance` | "Where did this value come from?" |
| *extra* GET | `/api/jobs/{id}` · `/api/meta` · `/health` | Job detail · enums/thresholds/schemas/policy · health |
| GET | `/api/applications/{id}/financial-health` | Health profile: periods → dimensions → indicator + metrics |
| GET | `/api/applications/{id}/financial-health/{period}` | One period (`FY2024-25`, `2024-04`, …); 404 if none |
| POST | `/api/applications/{id}/financial-health/rebuild` | Rebuild the health profile |
| GET | `/api/financial-health/metrics/{id}` | One metric with provenance resolved to fields / statement rows |
| GET | `/api/applications/{id}/forecasts` | All targets: historical actuals, forecast, model, backtest, uncertainty, data quality |
| GET | `/api/applications/{id}/forecasts/{metric}` | One target with model selection, observation sources, evidence, provenance |
| POST | `/api/applications/{id}/forecasts/rebuild` | Rebuild forecasts |
| POST | `/api/applications/{id}/repayment-capacity` | Submit proposed loan terms (body) and build the analysis |
| GET | `/api/applications/{id}/repayment-capacity` | Terms, schedule, debt service, capacity, DSCR, scenarios, quality, provenance |
| POST | `/api/applications/{id}/repayment-capacity/rebuild` | Recalculate with the stored terms |
| POST | `/api/applications/{id}/risk-features` | Generate a feature snapshot (reuses an identical one) |
| GET | `/api/applications/{id}/risk-features` | Latest snapshot: version, groups, features, statuses, provenance, quality, metadata |
| POST | `/api/applications/{id}/risk-features/rebuild` | Recompute and verify (or store a new snapshot if sources changed) |

There are deliberately **no approval APIs**.

---

## Testing

```bash
cd backend
pytest            # 79 tests, ~30 s; uses an isolated SQLite DB + temp storage, no OCR/LLM needed

# Same suite on the local PostgreSQL server, using a separate throwaway database
# (every test drops and recreates all tables there):
psql -U postgres -c "CREATE DATABASE msme_loans_test"
TEST_DATABASE_URL=postgresql+psycopg://postgres:<pw>@localhost:5432/msme_loans_test pytest
```

Fixtures are generated on the fly by `scripts/synthetic_docs.py`. Coverage of the requested cases:

| Case | Test |
|---|---|
| valid / invalid PAN | `test_validators.py::test_valid_pan`, `test_invalid_pan`, `test_extraction.py::test_invalid_pan_on_card_is_not_normalised` |
| valid / invalid GST | `test_valid_gstin_checksum`, `test_invalid_gstin_checksum`, `test_invalid_gstin_state_code`, `test_gst_certificate_extraction` |
| valid bank statement / multi-page / table extraction | `test_multi_page_bank_statement`, `test_full_sample_application_end_to_end` |
| balance mismatch | `test_balance_mismatch_is_inconsistency_not_fraud` |
| duplicate document | `test_duplicate_upload_creates_invalid_record` |
| missing field | `test_gst_missing_required_field`, `test_missing_required_field` |
| low-quality / scanned PDF | `test_low_quality_image_warning`, `test_scanned_pdf_flags_missing_text_layer`, `test_scanned_pdf_without_ocr_needs_review`, `test_scanned_pdf_with_ocr` (deterministic fake OCR engine) |
| unparseable rows kept | `test_unparseable_rows_are_kept_not_dropped` |
| financial inconsistency / name mismatch | `test_cross_document_name_and_financial_inconsistency`, `test_pan_mismatch_between_pan_card_and_gstin` |
| idempotent re-processing | `test_reprocessing_is_idempotent` |
| failures recorded, never silent | `test_unexpected_failure_is_recorded_never_silent`, `test_invalid_document_skips_later_stages` |
| LLM schema conformance / hallucination guard | `test_classifier.py::test_llm_*` |
| end-to-end sample application | `test_full_sample_application_end_to_end` |

Frontend: `npm run typecheck` and `npm run build`.

---

## Known limitations

* **Prototype only.** Not hardened for production: no authentication/authorisation, no encryption at
  rest, no malware scanning, no retention policy, single-process background tasks (FastAPI
  `BackgroundTasks`, not a durable queue). Uploads are written to disk before the size check.
* **Extraction heuristics are tuned on synthetic layouts.** Real bank statements, ITR forms and audited
  financials vary widely; expect to add bank-specific column synonyms and label patterns. Multi-period
  P&L/BS columns use the latest-year header or, failing that, the first amount column (warned).
* **OCR is off by default.** Scanned pages are explicitly flagged `SCANNED_PAGE_NO_OCR` and the document
  goes to review. Install Tesseract and set `OCR_ENGINE=tesseract` to enable; the PaddleOCR adapter is a stub.
* **LLM is off by default** and only used as a fallback (classification, missing header fields). Values
  must appear verbatim in the source text and always require review. Only an Anthropic adapter is
  included; other providers implement `LLMProvider._complete_raw`. When enabled it uses server-side
  refusal fallback routing (`fallbacks="default"`).
* **Bank "turnover"** is total credits (includes transfers/loan disbursals) and is indicative only.
* **GST periodic returns** are annualised when fewer than 12 are present — indicative only.
* **Name matching** is lexical (no transliteration, no proprietor ↔ trade-name linkage); a sole
  proprietor's personal-name bank account will be flagged for officer review by design.
* Duplicate detection is exact (SHA-256) within an application; re-scans of the same paper are not detected.
* The default document policy is illustrative and must be replaced by the bank's policy.
* The automated tests run on SQLite by default; they also pass on PostgreSQL 18 via `TEST_DATABASE_URL`.
