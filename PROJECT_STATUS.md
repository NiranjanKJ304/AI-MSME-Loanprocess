# Project Status — AI-MSME Loan Decision Intelligence Platform

**Last updated:** 2026-10-06
**Current state:** Document pipeline, extraction hardening, canonical financial layer, transaction intelligence, financial health analysis, deterministic forecasting, repayment capacity and risk feature engineering — **complete**. Runs locally on PostgreSQL 18 (no Docker).
**Next phase:** Not started. Risk scoring / ML model, explainability (SHAP), agents and loan decisions are deliberately out of scope so far.

> Prototype decision-support system. It extracts, validates, normalises, describes and projects MSME financials and bank activity, analyses repayment capacity and builds a versioned feature vector for future risk modelling. It does not score, approve or reject loans.

---

## Summary

| Area | Status | Notes |
|---|---|---|
| Backend (FastAPI, SQLAlchemy, Alembic) | ✅ Done | 146 Python files; 54 endpoints; no approval / scoring APIs |
| Document pipeline | ✅ Done | 15 stages, each writing a processing record; failures are recorded, never swallowed |
| Extraction & parsing (hardened) | ✅ Done | Native / OCR / hybrid pages, layout-aware label search, multi-page tables, Indian formats, explicit extraction states |
| Canonical financial layer | ✅ Done | Financial facts, periods, conflicts, canonical bank transactions — full provenance |
| Transaction intelligence | ✅ Done | Configurable classification, business nature, counterparties, recurring patterns, reversals, internal transfers, monthly cash flow |
| Financial health analysis | ✅ Done | 7 descriptive dimensions per period, transparent formulas, indicators with evidence — not a score |
| Financial forecasting | ✅ Done | Revenue (annual) + business inflow / outflow / net cash flow (monthly); naive / moving average / linear trend / seasonal naive; backtested |
| Repayment capacity | ✅ Done | User-provided loan terms, EMI / schedule, existing + proposed debt service, historical / forecast / statement DSCR, stress scenarios — descriptive outcome only |
| Risk feature engineering | ✅ Done | 68 versioned features (`v1`) in 6 groups, centralized definitions, reproducible fingerprinted snapshots — no model, no score |
| Frontend (Next.js 16, TypeScript, Tailwind 4) | ✅ Done | 8 pages; type-checks cleanly. No UI yet for the financial / transaction / health / forecast / repayment / feature APIs (by design) |
| Automated tests | ✅ 286 / 286 passing | SQLite **and** local PostgreSQL 18 (disposable `msme_loans_test` DB, dropped after each run) |
| PostgreSQL (local, no Docker) | ✅ Connected & migrated | PostgreSQL 18.4 Windows service on :5432; `msme_loans` at Alembic `0008`, 30/30 tables; `check_db` OK |
| Docker | 🗑️ Removed | Everything runs on local installs |
| OCR (Tesseract) | ⚠️ Not verified live | Off by default; tested with a deterministic fake engine and a mocked Tesseract adapter |
| LLM fallback (Anthropic) | ⚠️ Not verified live | Off by default; tested with a fake provider |
| Real documents | ⚠️ Not yet tested | All fixtures are synthetic (deliberately varied layouts) |
| Local dev setup in project folder | ⏳ Pending | `backend/.env` exists; `backend/.venv` and `frontend/node_modules` not yet installed; `msme_loans` has no data loaded |

---

## Phase checklist

| # | Phase | Status | Key deliverables |
|---|---|---|---|
| 1 | Project setup + PostgreSQL + FastAPI + Next.js | ✅ | Monorepo, local PostgreSQL, Alembic, `scripts/check_db.py`, `.env.example` files |
| 2 | Application creation + document upload | ✅ | `POST /api/applications`, multi-file upload, background processing |
| 3 | Document registry + file validation | ✅ | Record for every upload (incl. invalid/duplicate); MIME sniffing, size, extension, PDF password/corruption/pages, image quality, SHA-256 duplicates |
| 4 | PDF / text / table extraction | ✅ | PyMuPDF text layer, OCR abstraction, pdfplumber + layout tables, raw JSON artefact per document |
| 5 | Document classification | ✅ | Filename → patterns → keywords → layout → LLM fallback; low confidence ⇒ `NEEDS_REVIEW`; officer override |
| 6 | Document-specific extraction schemas | ✅ | PAN, GST certificate, GST return, Udyam, bank statement, ITR, P&L, balance sheet, business registration |
| 7 | Field-level validation + confidence | ✅ | PAN/GSTIN (incl. checksum)/IFSC/Udyam/CIN formats, dates, amounts, low-confidence, conflicts |
| 8 | Cross-document reconciliation | ✅ | PAN, GSTIN, business name, turnover (configurable thresholds), period alignment, bank vs balance sheet |
| 9 | Requirement / completeness engine | ✅ | Configurable prototype policy (MANDATORY / CONDITIONAL / SUPPORTING) |
| 10 | Frontend processing & validation dashboard | ✅ | Overview, upload + checklist, processing, validation/reconciliation, audit log, document detail with evidence viewer |
| 11 | Extraction & parsing hardening | ✅ | See below — migration `0002` |
| 12 | Canonical financial data layer | ✅ | See below — migration `0003` |
| 13 | Transaction intelligence layer | ✅ | See below — migration `0004` |
| 14 | Financial health analysis | ✅ | See below — migration `0005` |
| 15 | Financial forecasting | ✅ | See below — migration `0006` |
| 16 | Repayment capacity | ✅ | See below — migration `0007` |
| 17 | Risk feature engineering | ✅ | See below — migration `0008` |

### Phase 11 — Extraction & parsing hardening
- Page inspection before reading: native text, OCR, or **hybrid** (scanned page with a thin text layer); garbled text layers trigger OCR; method and reasons stored per page.
- OCR boxes mapped back through preprocessing (scale/deskew) to page coordinates; one OCR pass; OCR failures recorded as `OCR_FAILED`, never silent.
- Layout-aware label → value search (beside / below / above / in table cells; bilingual and numbered labels; two-column forms).
- Tables: token-based header vocabulary (Txn Date, Narration/Particulars, Withdrawal/Dr, Deposit/Cr, Amount + Dr/Cr…), two-line headers, repeated headers marked, page segments merged into one table, wrapped narration attached to the nearest transaction.
- Normalisation: `₹1,25,000`, `12,500.00CR`, `Rs.1,25,000/-`, unformatted figures, percentages, lakhs/crores units, OCR repair of IDs (OCR text only).
- Confidence built from explainable factors; field states `EXTRACTED / NOT_FOUND / LOW_CONFIDENCE / AMBIGUOUS / EXTRACTION_FAILED / OCR_REQUIRED / UNSUPPORTED`.

### Phase 12 — Canonical financial data layer
- One **financial fact** per source document × metric × period (33 metrics across business, profitability, balance sheet, banking, tax), never merged or overwritten; raw value and unit scaling kept.
- **Periods** normalised: `FY2024-25`, `2024-2025`, 01-Apr-2024 → 31-Mar-2025, AY 2025-26 → one period; months, quarters, custom ranges; different FYs never compared.
- Derived metrics (cash & bank, net worth, total liabilities) only when all components exist; otherwise `PARTIAL` with no value — never zero.
- **Conflicts** recorded (e.g. ITR vs GST turnover), not resolved; configurable tolerance.
- **Canonical bank transactions** with signed amounts and provenance to the statement row; bank credits kept as banking activity, never revenue.
- Profile states per period × metric: `AVAILABLE / NOT_AVAILABLE / PARTIAL / CONFLICTING / LOW_CONFIDENCE`.

### Phase 13 — Transaction intelligence
- 23 categories (income, expense, financing, transfer, cash, other); weak evidence stays `UNKNOWN` or `LOW_CONFIDENCE` — never forced.
- Separate business-nature dimension: `BUSINESS / PERSONAL / TRANSFER / FINANCING / UNKNOWN`.
- Multi-signal evidence: keywords with direction/amount/exclusion constraints, channel, counterparty type, own/related names, other account numbers, mirror entries across accounts, reversals, recurrence.
- Counterparty extraction (raw + normalised + type); recurring patterns (frequency, average, occurrences, first/last seen, confidence); reversal/refund pairs linked and excluded from totals; internal transfers detected and linked.
- Monthly cash flow per statement and combined (business / financing / transfer / cash / personal / other / **unknown** / excluded buckets), classification coverage, `PARTIAL` months for incomplete data; transaction-derived cash-flow metrics only.
- Rules in `app/transaction_intel/default_rules.json` (validated on load; override with `TXN_RULES_FILE`).

### Phase 14 — Financial health analysis
- Seven independent dimensions: revenue, profitability, liquidity, cash flow, leverage, business stability, tax / compliance.
- Transparent formulas (net / gross / operating margin, current ratio, debt to revenue / net worth, cash-flow margin, year-over-year change, compound trend, consistency) stored with inputs, period, status and result.
- Missing inputs stay `NOT_AVAILABLE`; ratios are never calculated from `CONFLICTING` facts (sources listed); zero denominators are never divided by.
- Per period: annual, quarterly, monthly, partial period. Trends compare consecutive financial years only, and revenue only on the same basis.
- Descriptive indicators `STRONG / STABLE / DECLINING / WEAK / INSUFFICIENT_DATA / CONFLICTING_DATA` with the rule and evidence; thresholds in `default_thresholds.json` (`HEALTH_THRESHOLDS_FILE`).
- Deterministic explanations; provenance to facts → fields → pages/bbox, or to transactions → statement rows.

### Phase 15 — Financial forecasting
- Targets: revenue (next FY) and monthly business inflow, business outflow and net business cash flow (next 3 months). Bank forecasts use classified business transactions only — never total credits.
- Data-quality checks before forecasting: observations, history length, consecutive periods, missing / partial periods, conflicting facts, revenue basis, classification coverage, UNKNOWN share, outliers, horizon gap. FAIL ⇒ `NOT_AVAILABLE`; WARN ⇒ `LOW_CONFIDENCE`.
- Missing months are never filled; PARTIAL and CONFLICTING observations are excluded with a reason.
- Explicit model selection (naive, moving average, linear trend; seasonal naive only if seasonality is detected) by rolling-origin one-step backtest (MAE / RMSE / MAPE), ties to the simpler model.
- Seasonality only with ≥ 24 consecutive complete months; approximate 80% intervals only with enough backtest errors, otherwise none with the reason.
- Settings in `default_config.json` (`FORECAST_CONFIG_FILE`); engine and config versions stored per run.

### Phase 16 — Repayment capacity
- Proposed loan terms are officer / user-provided (amount, rate, tenure, monthly / quarterly, optional grace period with explicit treatment), stored as `USER_PROVIDED` and audited. Nothing is assumed; no terms ⇒ no repayment calculation.
- Transparent EMI / installment formula (zero interest supported), full schedule, total repayment and interest; invalid terms rejected (422).
- Cash available for debt service = classified business inflow − business outflow (never total credits, transfers, loan disbursements, personal or financing inflows).
- Existing debt service = debits classified loan repayment / interest only; uncertain evidence (low-confidence EMIs, UNKNOWN debits to lenders, borrowings without observed repayments) ⇒ `LOW_CONFIDENCE`.
- DSCR and post-debt-service cash flow for historical, forecast and statement bases, per month; stress scenarios `BASE / REVENUE_DOWN / EXPENSE_UP / COMBINED_STRESS`.
- Descriptive outcome `ADEQUATE_DATA / LIMITED_DATA / LOW_CAPACITY / NEGATIVE_CAPACITY / CONFLICTING_DATA` — never approve / reject. Settings in `REPAYMENT_CONFIG_FILE`.

### Phase 17 — Risk feature engineering
- 68 features in 6 groups (business financials, transaction, stability, forecast, repayment, data quality), all defined in one registry (`app/risk_features/definitions.py`) with formula, unit, source layer, required inputs and version; one calculator per definition.
- Built only from the stored outputs of earlier layers; every feature carries value, unit, period, status, confidence, source layer, calculation and provenance. Missing ⇒ null `NOT_AVAILABLE`; conflicting ⇒ null `CONFLICTING`.
- Validation of types, units, statuses, null rules, period consistency, duplicates and provenance.
- Snapshots are versioned (`v1` + definitions hash) and content-addressed by a source fingerprint: identical inputs are reused, rebuilds verify identical values, changed inputs create a new snapshot (older ones kept), `stale` flag on read.

---

## Definition of Done (prototype foundation)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Create an MSME loan application | ✅ | `test_create_application_validation`; `/applications/new` |
| 2 | Upload multiple documents | ✅ | Multipart `files`; 22-file live upload |
| 3 | Automatically identify document types | ✅ | `test_classifies_sample_documents` (9 types) |
| 4 | Extract text and tables | ✅ | `test_multi_page_bank_statement`, `test_bank_variant_two_line_header_repeated_on_every_page` |
| 5 | Extract structured fields | ✅ | `test_extraction.py`, `test_extraction_realworld.py` |
| 6 | See confidence for each field | ✅ | `confidence` + `confidence_factors` on every field |
| 7 | See exact source page for values | ✅ | `/api/fields/{id}/provenance`; bbox highlight in UI |
| 8 | Detect missing fields | ✅ | `NOT_FOUND` / `OCR_REQUIRED` field states |
| 9 | Detect invalid documents | ✅ | `test_file_validation.py` |
| 10 | Detect duplicate documents | ✅ | `test_duplicate_upload_creates_invalid_record` |
| 11 | Validate bank transactions | ✅ | Row statuses `EXTRACTED / VALIDATED / NEEDS_REVIEW / FAILED` |
| 12 | Detect balance inconsistencies | ✅ | `test_balance_mismatch_is_inconsistency_not_fraud` |
| 13 | Compare information across documents | ✅ | Reconciliation checks + financial fact conflicts |
| 14 | Identify missing / conditional / supporting documents | ✅ | Completeness endpoint + upload-page checklist |
| 15 | See every processing failure and warning | ✅ | `test_unexpected_failure_is_recorded_never_silent` |
| 16 | View complete processing status in the frontend | ✅ | Processing dashboard + per-document pipeline |
| 17 | Re-run processing without duplicate records | ✅ | `test_reprocessing_is_idempotent`, `test_rebuild_is_idempotent_and_follows_reprocessing` |
| 18 | Preserve originals and raw extraction | ✅ | Write-once storage; `document_pages` with blocks / text quality / OCR output |

---

## Tests

| Suite | Tests | Covers |
|---|---|---|
| Original foundation (`test_file_validation`, `test_validators`, `test_classifier`, `test_extraction`, `test_pipeline_api`) | 79 | Validation, classification, extraction, pipeline, reconciliation, completeness |
| `test_extraction_realworld.py` | 58 | Scanned / mixed / hybrid PDFs, OCR failure and repair, bank layout variants, label layouts, Indian amounts, T-format P&L, lakhs, ITR-V |
| `test_financial_layer.py` | 28 | Periods, multiple sources, conflicts, missing / partial values, units, bank transactions, provenance |
| `test_transaction_intelligence.py` | 32 | All categories, revenue vs transfer / loan, recurring, reversals, refunds, internal transfers, monthly cash flow, coverage, partial statements, rules config |
| `test_financial_health.py` | 28 | Formulas, division by zero, revenue growth, margins, liquidity, current ratio, debt, cash flow, partial / missing / conflicting data, incompatible periods, provenance, thresholds |
| `test_forecasting.py` | 22 | Models, error metrics, no-leakage backtest, model selection, insufficient history, missing / partial months, conflicts, all four targets, seasonality, intervals, provenance, idempotent rebuild |
| `test_repayment_capacity.py` | 23 | EMI, zero interest, monthly / quarterly, grace periods, invalid terms, existing / proposed debt, DSCR, insufficient / partial / conflicting data, UNKNOWN share, stress scenarios, provenance, idempotent rebuild |
| `test_risk_features.py` | 16 | Definitions / versioning, all six feature groups, missing / conflicting / low-confidence / partial inputs, validation, reuse / verified rebuild / staleness, determinism, provenance |
| **Total** | **286** | Passing on SQLite and PostgreSQL 18 |

---

## Database migrations

| Revision | Adds | Applied to `msme_loans` |
|---|---|---|
| `0001` | Core schema: applications, documents, pages, tables/rows, fields, validation, reconciliation, jobs/stages, audit log | ✅ |
| `0002` | Page blocks / text quality / errors; field `extraction_status` | ✅ |
| `0003` | `financial_periods`, `financial_facts`, `financial_conflicts`, `bank_transactions` | ✅ |
| `0004` | `transaction_classifications`, `recurring_patterns`, `cashflow_monthly`, `cashflow_metrics` | ✅ |
| `0005` | `financial_health_metrics`, `financial_health_indicators` | ✅ |
| `0006` | `forecast_runs`, `forecast_observations`, `forecast_results` | ✅ |
| `0007` | `repayment_loan_terms`, `repayment_analyses`, `repayment_capacity_metrics`, `repayment_scenarios` | ✅ |
| `0008` | `risk_feature_sets`, `risk_features` | ✅ |

---

## Verification log

| Date | Check | Result |
|---|---|---|
| 2026-10-05 | Backend test suite | 79 passed |
| 2026-10-05 | Frontend `tsc --noEmit` + `next build` | Clean |
| 2026-10-05 | Live run: clean sample application (9 docs) + negative set (13 docs) | Expected statuses and reasons |
| 2026-10-06 | Local PostgreSQL 18.4 connection via `backend/.env`; migration `0001`; API `/health` | Connected; `database: true` |
| 2026-10-06 | Extraction hardening: suite on SQLite and PostgreSQL; migration `0002` | 137 passed (both); no drift |
| 2026-10-06 | Canonical financial layer: suite on SQLite and PostgreSQL; migration `0003` | 165 passed (both); no drift |
| 2026-10-06 | Transaction intelligence: suite on SQLite and PostgreSQL; migration `0004` | 197 passed (both); no drift |
| 2026-10-06 | Financial health: suite on SQLite and PostgreSQL; migration `0005` (incl. downgrade / upgrade) | 225 passed (both); no drift |
| 2026-10-06 | Forecasting: suite on SQLite and PostgreSQL; migration `0006` (incl. downgrade / upgrade) | 247 passed (both); no drift |
| 2026-10-06 | Repayment capacity: suite on SQLite and PostgreSQL; migration `0007` (incl. downgrade / upgrade) | 270 passed (both); no drift |
| 2026-10-06 | Risk features: suite on SQLite and PostgreSQL; migration `0008` (incl. downgrade / upgrade) | 286 passed (both); no drift |
| 2026-10-06 | `python -m scripts.check_db` | `alembic 0008`, 30/30 tables, `status: OK` |
| 2026-10-06 | Frontend type-check after extraction-status display change | Clean |

### Issues found and fixed during these phases
- OCR boxes were in the wrong place after deskewing; now mapped back to page coordinates.
- Wrapped narration at the bottom of a page was dropped; now kept.
- Long narrations were split across columns ("SHAKTHI PUMPS LTD" lost "LTD"); short numbers in narrations ("TL 7788") were moved to another column.
- ITR-V row numbers were read as amounts.
- A backspace character had slipped into two regex patterns; a test now checks the rule file for control characters.
- GST filing coverage missed returns whose document could not be resolved; it now also reads the document type recorded on the period's facts.
- Integer risk features (counts, tenure) were returned as strings; they are now JSON integers.

---

## Open items

### Before relying on this setup
- [ ] Install dependencies in the project: `backend/.venv` (`pip install -r requirements.txt`) and `frontend/node_modules` (`npm install`). Consider excluding both from OneDrive sync.
- [ ] Load the sample set into `msme_loans` (currently empty): `python -m scripts.generate_samples --upload http://localhost:8000 --with-negative`.
- [ ] Install Tesseract and test `OCR_ENGINE=tesseract` on real scanned documents.
- [ ] If an LLM fallback is wanted: set `LLM_PROVIDER=anthropic` + key and test on low-confidence documents.

### Before the next phase
- [ ] Test extraction and transaction classification on real (anonymised) bank statements, ITRs and audited financials; extend label synonyms and `default_rules.json`.
- [ ] Add related parties (directors, partners, family) to `related_party_names`, or extract them from KYC / deeds.
- [ ] Agree thresholds with credit teams: reconciliation variance, fact-conflict tolerance (`FACT_CONFLICT_TOLERANCE`), classification thresholds.
- [ ] Replace `PROTOTYPE_DEFAULT_POLICY` with the bank's document requirement policy.
- [ ] Decide whether the financial-facts banking metrics should use the new transaction categories (two category systems exist today).
- [ ] Agree health indicator thresholds and forecasting settings (minimum history, coverage, UNKNOWN share) with the credit team.
- [ ] Validate health metrics and forecasts on real multi-year data (the sample has one FY, so its revenue forecast is `NOT_AVAILABLE`).
- [ ] Agree repayment settings (stress %, descriptive `LOW_CAPACITY` DSCR threshold, coverage / UNKNOWN limits) with the credit team.
- [ ] Review the 68 risk-feature definitions with the modelling team before any model is trained; bump `FEATURE_VERSION` for any change.
- [ ] Add authentication / role-based access; replace FastAPI `BackgroundTasks` with a durable job queue.

### Known limitations (details in README and phase reports)
- Fixtures are synthetic; real-world layouts will need tuning.
- One business-entity credit is only "likely" revenue (`LOW_CONFIDENCE`); credits from individuals stay `UNKNOWN` without more evidence.
- Counterparty matching is lexical (spelling variants are different parties); internal transfers need an exact amount within 2 days.
- A statement with any unparsed row makes all its months `PARTIAL`.
- Monthly / quarterly GST returns are not rolled up into a financial year; currency assumed INR.
- Health: P&L revenue is preferred to declared turnover by a fixed priority (difference noted, not a conflict); tax coverage reflects uploaded documents only.
- Forecasting: few annual observations give `LOW_CONFIDENCE`; intervals are approximate (normal errors) and need enough backtest points; history before a gap is not used; seasonality is detected, and used only through `seasonal_naive`.
- Repayment: existing debt is visible only for the statements provided and is assumed flat over the forecast; quarterly installments use a monthly equivalent; statement DSCR mixes statement numerator and bank debt service; one set of terms per application.
- Risk features: the source fingerprint includes record ids, so upstream rebuilds create new snapshots even with identical data; snapshots are generated on request (not in the pipeline); some features are documented proxies (e.g. profitable months are cash-based).
- No auth, encryption at rest, malware scanning or retention policy.

---

## Out of scope so far (not started, by design)

Risk scoring / ML risk model, probability of default, credit / health score, XGBoost, SHAP, Prophet / ML forecasting, NetworkX, multi-agent decisioning, loan approval / rejection / recommendation.

---

## Quick reference

```bash
# PostgreSQL runs locally as the postgresql-x64-18 Windows service (port 5432)
cd backend && python -m scripts.check_db        # connection + migration check (expects alembic 0008)
cd backend && alembic upgrade head && uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev                      # http://localhost:3000
cd backend && pytest                            # 286 tests
cd backend && python -m scripts.generate_samples --upload http://localhost:8000 --with-negative
```

Main APIs added in phases 12–17:
`/api/applications/{id}/financials/{profile|facts|periods|conflicts|bank-transactions}`,
`/api/applications/{id}/transactions/{intelligence|classified|recurring}`,
`/api/applications/{id}/cashflow/{monthly|metrics}`, `/api/transactions/{id}/classification`, `/api/transaction-rules`,
`/api/applications/{id}/financial-health[/{period}]`, `/api/financial-health/metrics/{id}`,
`/api/applications/{id}/forecasts[/{metric}]`, `POST /api/applications/{id}/forecasts/rebuild`,
`GET|POST /api/applications/{id}/repayment-capacity` (+ `/rebuild`), `GET|POST /api/applications/{id}/risk-features` (+ `/rebuild`).

See [README.md](README.md) for architecture, schemas, validation rules and API reference.
