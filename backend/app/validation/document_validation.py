"""Document-level validation (deterministic; runs before any LLM reasoning).

Bank statements: per-row balance continuity (prev + credit - debit ~= balance), statement
totals (opening + credits - debits ~= closing), transactions inside the statement period,
date ordering, unparsed rows, summary-row totals.
P&L / Balance sheet / ITR: internal arithmetic consistency.
All: unreadable pages, low classification confidence.

Mismatches are reported as INCONSISTENCY - never as fraud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.config import get_settings
from app.models import Document, DocumentPage, ExtractedField, ExtractedTable, ExtractedTableRow
from app.models.enums import CheckStatus, DocumentType, RowKind, RowStatus, Severity, TextSource
from app.validation.rules import RuleOutcome, approx_equal, dec

ERR, WARN, INFO = Severity.ERROR, Severity.WARNING, Severity.INFO


@dataclass
class DocOutcome:
    outcome: RuleOutcome
    row: ExtractedTableRow | None = None
    field_name: str | None = None
    page: int | None = None


@dataclass
class DocumentValidationReport:
    outcomes: list[DocOutcome] = field(default_factory=list)
    rows_checked: int = 0
    rows_inconsistent: int = 0

    def add(self, o: RuleOutcome, *, row=None, field_name=None, page=None) -> None:
        self.outcomes.append(DocOutcome(o, row, field_name, page))


def _val(fields: dict[str, ExtractedField], name: str) -> Decimal | None:
    f = fields.get(name)
    if f is None or f.is_missing:
        return None
    return dec(f.normalized_value)


def validate_document(
    document: Document,
    fields: dict[str, ExtractedField],
    tables: list[ExtractedTable],
    pages: list[DocumentPage],
) -> DocumentValidationReport:
    rep = DocumentValidationReport()
    settings = get_settings()

    if (document.classification_confidence or 0) < settings.classification_confidence_threshold:
        rep.add(RuleOutcome("CLASSIFICATION_CONFIDENCE", CheckStatus.NEEDS_REVIEW, WARN,
                            f"Document type {document.document_type.value} assigned with low confidence "
                            f"({(document.classification_confidence or 0):.2f}); confirm the type"))
    for p in pages:
        if p.text_source == TextSource.NONE:
            rep.add(RuleOutcome("PAGE_TEXT_UNAVAILABLE", CheckStatus.NEEDS_REVIEW, ERR,
                                f"Page {p.page_number} has no text layer and was not OCR'd; its content is not extracted"),
                    page=p.page_number)
        elif p.text_source == TextSource.OCR and (p.ocr_confidence or 0) < 70:
            rep.add(RuleOutcome("LOW_OCR_CONFIDENCE", CheckStatus.NEEDS_REVIEW, WARN,
                                f"Page {p.page_number} OCR confidence {p.ocr_confidence or 0:.0f}%"),
                    page=p.page_number)

    for t in tables:
        failed = [r for r in t.rows if r.status == RowStatus.FAILED]
        if failed:
            rep.add(RuleOutcome("UNPARSED_TABLE_ROWS", CheckStatus.NEEDS_REVIEW, WARN,
                                f"Table {t.table_index} (page {t.page_number}): {len(failed)} row(s) could not be "
                                "parsed; they are retained with status FAILED", details={"rows": [r.row_index for r in failed]}),
                    page=t.page_number)

    dt = document.document_type
    if dt == DocumentType.BANK_STATEMENT:
        _bank(rep, fields, tables, Decimal(str(settings.balance_tolerance)))
    elif dt == DocumentType.PROFIT_LOSS:
        _profit_loss(rep, fields)
    elif dt == DocumentType.BALANCE_SHEET:
        _balance_sheet(rep, fields)
    elif dt == DocumentType.ITR:
        _itr(rep, fields)
    return rep


# --------------------------------------------------------------------------- bank
def _bank(rep: DocumentValidationReport, fields, tables: list[ExtractedTable], tol: Decimal) -> None:
    rows = [r for t in sorted(tables, key=lambda t: t.table_index) for r in t.rows]
    txns = [r for r in rows if r.row_kind == RowKind.TRANSACTION]
    if not txns:
        rep.add(RuleOutcome("TRANSACTIONS_PRESENT", CheckStatus.FAIL, ERR,
                            "No transactions could be extracted from the bank statement"))
        return

    opening = _val(fields, "opening_balance")
    closing = _val(fields, "closing_balance")
    start = fields.get("period_start")
    end = fields.get("period_end")
    p_start = start.normalized_value if start and not start.is_missing else None
    p_end = end.normalized_value if end and not end.is_missing else None

    prev = opening
    total_cr = Decimal("0")
    total_dr = Decimal("0")
    last_balance: Decimal | None = None
    prev_date: str | None = None
    order_violations = 0
    for r in txns:
        p = r.parsed or {}
        debit, credit, bal = dec(p.get("debit")), dec(p.get("credit")), dec(p.get("balance"))
        total_cr += credit or 0
        total_dr += debit or 0
        errors = list(r.errors or [])
        d = p.get("date")
        if d and p_start and p_end and not (p_start <= d <= p_end):
            errors.append(f"OUTSIDE_PERIOD: {d} not within {p_start}..{p_end}")
            rep.add(RuleOutcome("TXN_WITHIN_PERIOD", CheckStatus.INCONSISTENCY, WARN,
                                f"Row {r.row_index} (page {r.page_number}): date {d} outside statement period "
                                f"{p_start} to {p_end}", actual=d), row=r, page=r.page_number)
        if d and prev_date and d < prev_date:
            order_violations += 1
        prev_date = d or prev_date

        if bal is not None and prev is not None and (debit is not None or credit is not None):
            rep.rows_checked += 1
            expected = prev + (credit or 0) - (debit or 0)
            if not approx_equal(expected, bal, tol):
                rep.rows_inconsistent += 1
                diff = bal - expected
                errors.append(f"BALANCE_MISMATCH: expected {expected}, statement shows {bal} (diff {diff})")
                rep.add(RuleOutcome("BALANCE_CONTINUITY", CheckStatus.INCONSISTENCY, ERR,
                                    f"Row {r.row_index} (page {r.page_number}): previous balance {prev} "
                                    f"+ credit {credit or 0} - debit {debit or 0} = {expected}, but statement "
                                    f"shows {bal} (difference {diff})",
                                    expected=str(expected), actual=str(bal),
                                    details={"previous_balance": str(prev), "difference": str(diff)}),
                        row=r, page=r.page_number)
                r.status = RowStatus.NEEDS_REVIEW
            elif r.status == RowStatus.EXTRACTED:
                r.status = RowStatus.VALIDATED
        elif r.status == RowStatus.EXTRACTED and prev is None:
            errors.append("BALANCE_UNVERIFIED: no previous balance to check against")
        r.errors = errors or None
        if bal is not None:
            prev = bal
        elif prev is not None:
            prev = prev + (credit or 0) - (debit or 0)
        last_balance = bal if bal is not None else last_balance

    if order_violations:
        rep.add(RuleOutcome("TXN_DATE_ORDER", CheckStatus.NEEDS_REVIEW, WARN,
                            f"{order_violations} transaction(s) appear out of chronological order"))
    if opening is None:
        rep.add(RuleOutcome("OPENING_BALANCE_PRESENT", CheckStatus.NEEDS_REVIEW, WARN,
                            "Opening balance not found; the first transaction's balance cannot be verified"))
    if opening is not None and closing is not None:
        expected_close = opening + total_cr - total_dr
        if not approx_equal(expected_close, closing, tol):
            rep.add(RuleOutcome("STATEMENT_TOTALS", CheckStatus.INCONSISTENCY, ERR,
                                f"Opening {opening} + credits {total_cr} - debits {total_dr} = {expected_close}, "
                                f"but closing balance is {closing}",
                                expected=str(expected_close), actual=str(closing),
                                details={"total_credits": str(total_cr), "total_debits": str(total_dr)}))
        else:
            rep.add(RuleOutcome("STATEMENT_TOTALS", CheckStatus.PASS, INFO,
                                "Opening + credits - debits equals closing balance"))
    if closing is not None and last_balance is not None and not approx_equal(closing, last_balance, tol):
        rep.add(RuleOutcome("CLOSING_MATCHES_LAST_BALANCE", CheckStatus.INCONSISTENCY, ERR,
                            f"Closing balance {closing} differs from last transaction balance {last_balance}",
                            expected=str(closing), actual=str(last_balance)))

    for r in rows:
        if r.row_kind == RowKind.SUMMARY and r.parsed:
            for key, total in (("debit_total", total_dr), ("credit_total", total_cr)):
                stated = dec(r.parsed.get(key))
                if stated is not None and not approx_equal(stated, total, tol):
                    rep.add(RuleOutcome("SUMMARY_TOTALS", CheckStatus.INCONSISTENCY, WARN,
                                        f"Summary row {key.replace('_', ' ')} {stated} differs from sum of rows {total}",
                                        expected=str(total), actual=str(stated)), row=r, page=r.page_number)
            if r.status == RowStatus.EXTRACTED:
                r.status = RowStatus.VALIDATED
        elif r.row_kind in (RowKind.OPENING_BALANCE, RowKind.CLOSING_BALANCE) and r.status == RowStatus.EXTRACTED:
            r.status = RowStatus.VALIDATED


# --------------------------------------------------------------------------- financials
def _arith(rep, code: str, label: str, lhs: Decimal | None, rhs: Decimal | None, scale: Decimal | None,
           severity: Severity = ERR) -> None:
    if lhs is None or rhs is None:
        return
    tol = max(Decimal("1"), abs(scale or Decimal("0")) * Decimal("0.005"))
    if approx_equal(lhs, rhs, tol):
        rep.add(RuleOutcome(code, CheckStatus.PASS, INFO, f"{label}: consistent"))
    else:
        rep.add(RuleOutcome(code, CheckStatus.INCONSISTENCY, severity,
                            f"{label}: computed {lhs}, stated {rhs} (difference {rhs - lhs})",
                            expected=str(lhs), actual=str(rhs)))


def _profit_loss(rep, f) -> None:
    rev, cogs, gp = _val(f, "revenue"), _val(f, "cost_of_goods_sold"), _val(f, "gross_profit")
    pbt, tax, pat = _val(f, "profit_before_tax"), _val(f, "tax"), _val(f, "profit_after_tax")
    ebitda, dep, intr = _val(f, "ebitda"), _val(f, "depreciation"), _val(f, "interest")
    if rev is not None and cogs is not None:
        _arith(rep, "PL_GROSS_PROFIT", "Revenue - COGS = Gross profit", rev - cogs, gp, rev)
    if pbt is not None and tax is not None:
        _arith(rep, "PL_PROFIT_AFTER_TAX", "PBT - Tax = PAT", pbt - tax, pat, rev or pbt)
    if None not in (ebitda, dep, intr):
        # other income can legitimately sit between EBITDA and PBT => warning only
        _arith(rep, "PL_EBITDA_BRIDGE", "EBITDA - Depreciation - Interest = PBT",
               ebitda - dep - intr, pbt, rev or ebitda, severity=WARN)  # type: ignore[operator]
    if rev is not None and pat is not None and rev > 0 and pat > rev:
        rep.add(RuleOutcome("PL_PAT_EXCEEDS_REVENUE", CheckStatus.INCONSISTENCY, WARN,
                            f"Profit after tax {pat} exceeds revenue {rev}"))


def _balance_sheet(rep, f) -> None:
    ta, tl = _val(f, "total_assets"), _val(f, "total_liabilities")
    _arith(rep, "BS_BALANCES", "Total assets = Total equity & liabilities", ta, tl, ta)
    parts = [_val(f, n) for n in ("fixed_assets", "inventory", "receivables", "cash", "bank_balance",
                                  "other_current_assets")]
    known = [p for p in parts if p is not None]
    if ta is not None and known and sum(known) > ta * Decimal("1.005"):
        rep.add(RuleOutcome("BS_ASSET_COMPONENTS", CheckStatus.INCONSISTENCY, WARN,
                            f"Sum of extracted asset items {sum(known)} exceeds total assets {ta}",
                            expected=f"<= {ta}", actual=str(sum(known))))
    lparts = [_val(f, n) for n in ("capital", "reserves", "borrowings", "trade_payables", "other_liabilities")]
    lknown = [p for p in lparts if p is not None]
    if tl is not None and lknown and sum(lknown) > tl * Decimal("1.005"):
        rep.add(RuleOutcome("BS_LIABILITY_COMPONENTS", CheckStatus.INCONSISTENCY, WARN,
                            f"Sum of extracted liability items {sum(lknown)} exceeds total {tl}",
                            expected=f"<= {tl}", actual=str(sum(lknown))))


def _itr(rep, f) -> None:
    gti, ti, tax = _val(f, "gross_total_income"), _val(f, "taxable_income"), _val(f, "tax_paid")
    if gti is not None and ti is not None and ti > gti:
        rep.add(RuleOutcome("ITR_TAXABLE_LE_GROSS", CheckStatus.INCONSISTENCY, WARN,
                            f"Taxable income {ti} exceeds gross total income {gti}"))
    if ti is not None and tax is not None and ti > 0 and tax > ti:
        rep.add(RuleOutcome("ITR_TAX_LE_INCOME", CheckStatus.INCONSISTENCY, WARN,
                            f"Tax paid {tax} exceeds taxable income {ti}"))
