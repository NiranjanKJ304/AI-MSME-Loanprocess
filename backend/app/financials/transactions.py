"""Canonical bank transactions (from parsed bank-statement table rows) and banking metrics.

Bank activity stays bank activity: credits are summed into the BANKING metric `total_credits`
only. They are never treated as revenue or turnover.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.models import BankTransaction, Document, ExtractedTable, ExtractedTableRow
from app.models.enums import RowKind, RowStatus, TxnCategory, TxnDirection

# Narration keywords (Indian bank conventions). Direction must also match the category.
_CASH_DEPOSIT = re.compile(r"\b(?:CASH\s*DEP(?:OSIT)?|CSH\s*DEP|BY\s+CASH|CDM|CASH\s+CR)\b", re.I)
_CASH_WITHDRAWAL = re.compile(r"\b(?:ATM|ATW|CASH\s*W(?:D|DL|ITHDRAWAL)|CSH\s*WDL|NFS\s*CASH|SELF\s+CHQ|CHQ\s+PAID\s+SELF|TO\s+SELF)\b", re.I)
_BANK_CHARGES = re.compile(
    r"\b(?:CHARGES?|CHRGS?|CHGS?|COMMISSION|COMM|SMS\s*(?:ALERT|CHG)|MIN(?:IMUM)?\s*BAL|NON[-\s]?MAINT|PENAL(?:TY)?|"
    r"SERVICE\s+(?:CHARGE|FEE)|PROCESSING\s+FEE|FEE|GST\s+ON\s+CHG|DEBIT\s+CARD\s+(?:FEE|AMC)|AMC)\b", re.I)
_DEBT = re.compile(
    r"\b(?:EMI|LOAN|LN\s*(?:REPAY|INST)|INSTAL+MENT|TERM\s+LOAN|TL\s+REPAY|REPAYMENT|OD\s+INT|CC\s+INT|"
    r"INT(?:EREST)?\s+(?:ON\s+)?(?:OD|CC|LOAN|DEBIT)|NACH.*(?:FIN|LOAN|EMI)|ACH\s+DR.*(?:LOAN|FIN|EMI))\b", re.I)


def classify(description: str | None, direction: TxnDirection) -> TxnCategory:
    d = description or ""
    if direction == TxnDirection.CREDIT:
        return TxnCategory.CASH_DEPOSIT if _CASH_DEPOSIT.search(d) else TxnCategory.OTHER
    if direction == TxnDirection.DEBIT:
        if _BANK_CHARGES.search(d):
            return TxnCategory.BANK_CHARGES
        if _DEBT.search(d):
            return TxnCategory.DEBT_PAYMENT
        if _CASH_WITHDRAWAL.search(d):
            return TxnCategory.CASH_WITHDRAWAL
    return TxnCategory.OTHER


def _dec(v) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _date(v) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        return None


@dataclass
class StatementRows:
    transactions: list[BankTransaction] = field(default_factory=list)
    failed_rows: int = 0  # rows extraction could not parse (kept in the extraction layer)
    review_rows: int = 0  # transactions extraction flagged NEEDS_REVIEW
    tables: list[str] = field(default_factory=list)


def build_transactions(application_id, doc: Document, tables: list[ExtractedTable],
                       account_number: str | None) -> StatementRows:
    out = StatementRows()
    seq = 0
    for t in sorted(tables, key=lambda t: t.table_index):
        rows: list[ExtractedTableRow] = list(t.rows)
        if not any(r.row_kind == RowKind.TRANSACTION for r in rows):
            continue
        out.tables.append(str(t.id))
        for r in rows:
            if r.row_kind == RowKind.UNPARSED or r.status == RowStatus.FAILED:
                out.failed_rows += 1
                continue
            if r.row_kind != RowKind.TRANSACTION:
                continue
            p = r.parsed or {}
            debit, credit = _dec(p.get("debit")), _dec(p.get("credit"))
            if credit is not None and (debit is None or debit == 0):
                direction, amount = TxnDirection.CREDIT, credit
            elif debit is not None and (credit is None or credit == 0):
                direction, amount = TxnDirection.DEBIT, -debit
            else:
                direction, amount = TxnDirection.UNKNOWN, None
            if r.status == RowStatus.NEEDS_REVIEW:
                out.review_rows += 1
            seq += 1
            out.transactions.append(BankTransaction(
                id=uuid.uuid4(), application_id=application_id, source_document_id=doc.id, source_row_id=r.id, sequence=seq,
                account_number=account_number, transaction_date=_date(p.get("date")),
                value_date=_date(p.get("value_date")), description=p.get("description"),
                reference=p.get("reference"), debit=debit, credit=credit, amount=amount,
                balance=_dec(p.get("balance")), direction=direction,
                category=classify(p.get("description"), direction), source_page=r.page_number,
                confidence=r.confidence, row_status=r.status,
                provenance={
                    "chain": "bank_transaction -> extracted_table_row -> table -> document -> page -> bbox",
                    "document_id": str(doc.id), "document_code": doc.document_code, "filename": doc.filename,
                    "page": r.page_number, "bbox": r.bbox, "table_id": str(t.id), "table_index": t.table_index,
                    "row_id": str(r.id), "row_index": r.row_index, "raw_cells": r.raw_cells,
                    "row_status": r.status.value, "row_errors": r.errors,
                    "raw": {k: p.get(k) for k in ("debit_raw", "credit_raw", "amount_raw", "balance_raw")},
                },
            ))
    return out


@dataclass
class BalanceStats:
    average: Decimal
    minimum: Decimal
    maximum: Decimal
    days: int
    start: date
    end: date
    notes: list[str]


def balance_stats(txns: list[BankTransaction], opening: Decimal | None, start: date | None,
                  end: date | None) -> BalanceStats | None:
    """Average/min/max of END-OF-DAY balances, carrying the last balance forward over days
    without transactions. Days before the first transaction use the opening balance when known."""
    eod: dict[date, Decimal] = {}
    for t in sorted(txns, key=lambda t: t.sequence):
        if t.transaction_date and t.balance is not None:
            eod[t.transaction_date] = t.balance  # last balance of the day wins
    if not eod:
        return None
    notes: list[str] = []
    first_day, last_day = min(eod), max(eod)
    lo = start or first_day
    hi = end or last_day
    if start is None or end is None:
        notes.append("statement period unknown: statistics cover the transaction date range only")
    current = opening if (opening is not None and start is not None) else None
    if current is None and lo < first_day:
        notes.append(f"opening balance unknown: days before {first_day.isoformat()} excluded")
        lo = first_day
    values: list[Decimal] = []
    d = lo
    while d <= hi:
        if d in eod:
            current = eod[d]
        if current is not None:
            values.append(current)
        d += timedelta(days=1)
    if not values:
        return None
    avg = (sum(values, Decimal(0)) / len(values)).quantize(Decimal("0.01"))
    return BalanceStats(avg, min(values), max(values), len(values), lo, hi, notes)
