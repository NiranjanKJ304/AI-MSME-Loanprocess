"""Bank statement extractor.

Header fields come from the layout locator; transactions come exclusively from extracted tables
(never from an LLM). Every raw row is kept with a kind + status:
  TRANSACTION rows      EXTRACTED / NEEDS_REVIEW / FAILED (validated later: VALIDATED)
  CONTINUATION rows     wrapped narration, attached to the nearest transaction (above or below,
                        also across a page break)
  OPENING/CLOSING/SUMMARY/HEADER rows recorded as such (repeated headers are never transactions)
  anything else         UNPARSED + FAILED with the reason

Column semantics come from the table header vocabulary (Date/Txn Date, Narration/Particulars,
Withdrawal/Debit/Dr, Deposit/Credit/Cr, Balance, single Amount + Dr/Cr), not one bank's layout.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.document_ai import normalizers as N
from app.document_ai.extractor import (
    BaseExtractor,
    ExtractionContext,
    FieldCandidate,
    FieldSpec,
    RowResult,
    TableResult,
    process_generic_table,
)
from app.document_ai.schemas import BankStatementSchema, Transaction
from app.document_ai.table_extractor import analyze_table, is_transaction_mapping, map_transaction_header
from app.models.enums import DocumentType, ExtractionMethod, RowKind, RowStatus, TableType

BANK_NAME_RE = re.compile(
    r"\b(State Bank of India|Bank of (?:Baroda|India|Maharashtra)|Punjab National Bank|"
    r"Union Bank of India|Central Bank of India|Indian Overseas Bank|Indian Bank|Canara Bank|"
    r"(?:[A-Z][A-Za-z-]+\s){1,3}Bank(?:\s(?:Ltd\.?|Limited))?)\b"
)
# IFSC bank-code prefix -> bank (deterministic reference data, used only when no name is printed)
IFSC_BANKS = {
    "SBIN": "State Bank of India", "HDFC": "HDFC Bank", "ICIC": "ICICI Bank", "UTIB": "Axis Bank",
    "KKBK": "Kotak Mahindra Bank", "PUNB": "Punjab National Bank", "BARB": "Bank of Baroda",
    "CNRB": "Canara Bank", "UBIN": "Union Bank of India", "IDIB": "Indian Bank", "IOBA": "Indian Overseas Bank",
    "BKID": "Bank of India", "CBIN": "Central Bank of India", "MAHB": "Bank of Maharashtra",
    "YESB": "Yes Bank", "IDFB": "IDFC First Bank", "INDB": "IndusInd Bank", "FDRL": "Federal Bank",
    "KVBL": "Karur Vysya Bank", "TMBL": "Tamilnad Mercantile Bank", "CIUB": "City Union Bank",
    "SIBL": "South Indian Bank", "KARB": "Karnataka Bank", "RATN": "RBL Bank", "AUBL": "AU Small Finance Bank",
    "DCBL": "DCB Bank", "CSBK": "CSB Bank", "UCBA": "UCO Bank", "PSIB": "Punjab & Sind Bank",
}
_D = N.DATE_IN_TEXT.pattern
PERIOD_RE = re.compile(rf"(?P<a>{_D})\W{{0,4}}(?:to|till|upto|until|-|–)\W{{0,4}}(?P<b>{_D})", re.IGNORECASE)
PERIOD_CONTEXT = re.compile(r"period|from|statement|between|duration", re.IGNORECASE)
HOLDER_LINE = re.compile(r"^\s*(?:M/S\.?|M/s\.?|MR\.?|MRS\.?|MS\.?|SHRI|SMT\.?)\s+[A-Z][A-Za-z.&' -]{2,}")


class BankStatementExtractor(BaseExtractor):
    document_type = DocumentType.BANK_STATEMENT
    schema = BankStatementSchema
    llm_max_pages = 1  # header fields only; transaction tables are never sent to an LLM
    field_specs = [
        FieldSpec(
            "account_number", "code", required=True,
            labels=[r"account\s*(?:no\.?|number|#)", r"a/c\.?\s*(?:no\.?|number)", r"acct\.?\s*(?:no\.?|number)"],
            pattern=r"[0-9Xx*][0-9Xx*\- ]{5,24}[0-9]",
        ),
        FieldSpec(
            "account_holder", "name", required=True,
            labels=[r"account\s+holder(?:'?s)?(?:\s+name)?", r"account\s+name", r"customer\s+name",
                    r"name\s+of\s+(?:the\s+)?account\s+holder", r"name(?=\s*:|\s{2,}|\s*$)"],
        ),
        FieldSpec("bank_name", "string"),
        FieldSpec("ifsc", "ifsc", labels=[r"ifsc(?:\s+code)?", r"ifs\s+code"], pattern=r"[A-Z]{4}0[A-Z0-9]{6}",
                  unlabeled=True),
        FieldSpec("period_start", "date", required=True),
        FieldSpec("period_end", "date", required=True),
        FieldSpec("opening_balance", "amount", labels=[r"opening\s+balance", r"balance\s+b/?f"], required=True),
        FieldSpec("closing_balance", "amount", labels=[r"closing\s+balance", r"balance\s+c/?f"], required=True),
    ]

    # ------------------------------------------------------------------ tables
    def process_tables(self, ctx: ExtractionContext) -> list[TableResult]:
        results: list[TableResult] = []
        inherited: dict[str, int] | None = None
        for raw in ctx.raw_tables:
            analysis = analyze_table(raw, inherited)
            if analysis.table_type != TableType.TRANSACTIONS:
                results.append(process_generic_table(raw, analysis))
                continue
            inherited = {k: v for k, v in analysis.column_mapping.items()
                         if isinstance(v, int) and not isinstance(v, bool)}
            results.append(self._process_txn_table(ctx, raw, analysis, inherited))
        return results

    def _process_txn_table(self, ctx, raw, analysis, mapping: dict[str, int]) -> TableResult:
        tr = TableResult(raw=raw, analysis=analysis)
        for i, cells in enumerate(raw.rows):
            page, bbox = raw.row_pages[i], raw.row_bboxes[i]
            hint = raw.row_hints[i] if i < len(raw.row_hints) else None
            if (analysis.header_index is not None and i == analysis.header_index) or hint == "HEADER":
                tr.rows.append(RowResult(i, page, bbox, cells, RowKind.HEADER, RowStatus.VALIDATED,
                                         errors=["REPEATED_HEADER"] if i != analysis.header_index else []))
                continue
            if is_transaction_mapping(map_transaction_header(cells)):
                tr.rows.append(RowResult(i, page, bbox, cells, RowKind.HEADER, RowStatus.VALIDATED,
                                         errors=["REPEATED_HEADER"]))
                continue
            tr.rows.append(_parse_txn_row(i, page, bbox, cells, mapping, ctx.page_factor(page)))
        _attach_continuations(tr.rows)
        txn_rows = [r for r in tr.rows if r.kind == RowKind.TRANSACTION]
        if txn_rows:
            tr.confidence = round(sum(r.confidence or 0 for r in txn_rows) / len(txn_rows), 3)
        failed = sum(1 for r in tr.rows if r.status == RowStatus.FAILED)
        if failed:
            tr.warnings.append(f"{failed} row(s) could not be parsed (kept as FAILED)")
        pages = sorted({r.page_number for r in txn_rows})
        if len(pages) > 1:
            tr.warnings.append(f"TABLE_SPANS_PAGES: {pages[0]}-{pages[-1]} reconstructed as one table")
        return tr

    # ------------------------------------------------------------------ fields
    def post_process(self, ctx, found, tables, warnings):
        first = ctx.parsed.pages[0] if ctx.parsed.pages else None
        if first is not None and "bank_name" not in found:
            for line in first.lines[:15]:
                m = BANK_NAME_RE.search(line.text)
                if m:
                    found["bank_name"] = ctx.candidate(
                        self._spec("bank_name"), m.group(1), page=first,
                        bbox=line.bbox_for_span(m.start(1), m.end(1)) if line.words else None, snippet=line.text,
                        method=ExtractionMethod.REGEX, base_conf=0.8, position="PATTERN",
                    )
                    break
        ifsc = found.get("ifsc")
        if "bank_name" not in found and ifsc and ifsc.normalized_value and ifsc.normalized_value[:4] in IFSC_BANKS:
            name = IFSC_BANKS[ifsc.normalized_value[:4]]
            found["bank_name"] = FieldCandidate(
                name="bank_name", value_type="string", raw_value=ifsc.normalized_value[:4], normalized_value=name,
                typed_value=name, confidence=round(min(ifsc.confidence, 0.85), 3), page=ifsc.page, bbox=ifsc.bbox,
                page_size=ifsc.page_size, snippet=ifsc.snippet, method=ExtractionMethod.DERIVED,
                position="DERIVED", text_source=ifsc.text_source,
                warnings=[f"DERIVED: bank identified from IFSC prefix '{ifsc.normalized_value[:4]}'"],
                confidence_factors={"derived_from": "ifsc", "base": min(ifsc.confidence, 0.85)},
            )
        if "account_holder" not in found and first is not None:
            for line in first.lines[:12]:
                m = HOLDER_LINE.match(line.text)
                if m:
                    found["account_holder"] = ctx.candidate(
                        self._spec("account_holder"), line.text.strip(), page=first, bbox=line.bbox,
                        snippet=line.text, method=ExtractionMethod.LAYOUT, base_conf=0.65, position="PATTERN",
                    )
                    found["account_holder"].warnings.append("UNLABELLED: holder name taken from an 'M/S'/'MR' line")
                    break
        # statement period: two dates joined by to/till/- on a line that mentions period/from/statement,
        # or (unlabelled) among the first lines of page 1 above the transaction table
        for page, li, line in ctx.lines():
            m = PERIOD_RE.search(line.text)
            if m and (PERIOD_CONTEXT.search(line.text) or (page.page_number == 1 and li < 15)):
                for name, grp in (("period_start", "a"), ("period_end", "b")):
                    found[name] = ctx.candidate(
                        self._spec(name), m.group(grp), page=page,
                        bbox=line.bbox_for_span(m.start(grp), m.end(grp)) if line.words else None,
                        snippet=line.text, method=ExtractionMethod.LABEL, position="INLINE",
                        word_conf=line.ocr_conf_for_span(m.start(grp), m.end(grp)) if line.words else None,
                    )
                break
        # opening / closing balance from table rows when no labelled value exists
        for name, kind in (("opening_balance", RowKind.OPENING_BALANCE), ("closing_balance", RowKind.CLOSING_BALANCE)):
            if name in found and found[name].normalized_value is not None:
                continue
            row, table = _find_row(tables, kind, last=(kind == RowKind.CLOSING_BALANCE))
            if row and row.parsed and row.parsed.get("balance") is not None:
                found[name] = ctx.candidate(
                    self._spec(name), row.parsed["balance_raw"], page=ctx.parsed.page(row.page_number),
                    bbox=row.bbox, snippet=" | ".join(c for c in row.raw_cells if c), method=ExtractionMethod.TABLE,
                    position="TABLE_ROW", table_index=table.raw.index, row_index=row.row_index,
                )
        txns = [r for t in tables for r in t.rows if r.kind == RowKind.TRANSACTION]
        if not txns:
            warnings.append("NO_TRANSACTIONS_EXTRACTED: no transaction rows found in any table")

    def _spec(self, name: str) -> FieldSpec:
        return next(s for s in self.field_specs if s.name == name)

    def build_structured(self, fields, tables, warnings) -> dict[str, Any]:
        data = super().build_structured(fields, tables, warnings)
        txns = []
        for t in tables:
            for r in t.rows:
                if r.kind == RowKind.TRANSACTION and r.status != RowStatus.FAILED and r.parsed:
                    txns.append(Transaction.model_validate(
                        {k: r.parsed.get(k) for k in Transaction.model_fields}).model_dump(mode="json"))
        data["transactions"] = txns
        return data


def _find_row(tables: list[TableResult], kind: RowKind, last: bool = False):
    hits = [(r, t) for t in tables for r in t.rows if r.kind == kind]
    if not hits:
        return None, None
    return hits[-1] if last else hits[0]


def _cell(cells: list[str | None], mapping: dict[str, int], key: str) -> str | None:
    idx = mapping.get(key)
    if idx is None or idx >= len(cells):
        return None
    v = cells[idx]
    if not v:
        return None
    v = re.sub(r"\s+", " ", v).strip()  # wrapped cells (pdfplumber) keep line breaks in raw
    return v or None


def _signed(raw: str | None) -> tuple[Decimal | None, bool]:
    """Parse an amount; returns (value, unparseable_flag). 'Dr' balances are negative."""
    if raw is None:
        return None, False
    v = N.parse_amount(raw)
    if v is None:
        return None, True
    if N.amount_drcr(raw) == "DR":
        v = -abs(v)
    return v, False


def _parse_txn_row(i, page, bbox, cells, mapping, factor) -> RowResult:
    joined = " ".join(re.sub(r"\s+", " ", c) for c in cells if c)
    low = joined.lower()
    date_raw = _cell(cells, mapping, "date")
    desc = _cell(cells, mapping, "description")
    debit_raw = _cell(cells, mapping, "debit")
    credit_raw = _cell(cells, mapping, "credit")
    amount_raw = _cell(cells, mapping, "amount")
    drcr_raw = _cell(cells, mapping, "drcr")
    bal_raw = _cell(cells, mapping, "balance")
    errors: list[str] = []

    balance, bal_bad = _signed(bal_raw)
    if bal_bad:
        errors.append(f"UNPARSEABLE_BALANCE: '{bal_raw}'")

    for marker, kind in ((r"opening\s+balance|balance\s+b/?f|brought\s+forward", RowKind.OPENING_BALANCE),
                         (r"closing\s+balance|balance\s+c/?f|carried\s+forward", RowKind.CLOSING_BALANCE)):
        if re.search(marker, low):
            if balance is None:
                amts = N.find_amounts(joined)
                if amts:
                    bal_raw = amts[-1][0]
                    balance, _ = _signed(bal_raw)
            return RowResult(i, page, bbox, cells, kind,
                             RowStatus.EXTRACTED if balance is not None else RowStatus.NEEDS_REVIEW,
                             parsed={"balance": N.decimal_str(balance), "balance_raw": bal_raw}, errors=errors,
                             confidence=round(0.95 * factor, 3))
    if re.match(r"^\s*(grand\s+)?total", low) or "transaction total" in low:
        debit, _ = _signed(debit_raw)
        credit, _ = _signed(credit_raw)
        return RowResult(i, page, bbox, cells, RowKind.SUMMARY, RowStatus.EXTRACTED,
                         parsed={"debit_total": N.decimal_str(debit), "credit_total": N.decimal_str(credit)},
                         confidence=round(0.9 * factor, 3))

    any_money = any([debit_raw, credit_raw, amount_raw, bal_raw])
    if not date_raw and not any_money:
        if joined.strip():
            return RowResult(i, page, bbox, cells, RowKind.CONTINUATION, RowStatus.EXTRACTED,
                             parsed={"text": joined.strip()})
        return RowResult(i, page, bbox, cells, RowKind.UNPARSED, RowStatus.FAILED, errors=["EMPTY_ROW"])

    txn_date = N.parse_date(date_raw)
    if txn_date is None:
        errors.append(f"UNPARSEABLE_DATE: '{date_raw or ''}'")
    value_date_raw = _cell(cells, mapping, "value_date")
    value_date = N.parse_date(value_date_raw)
    if value_date_raw and value_date is None:
        errors.append(f"UNPARSEABLE_VALUE_DATE: '{value_date_raw}'")

    debit, d_bad = _signed(debit_raw)
    credit, c_bad = _signed(credit_raw)
    if d_bad:
        errors.append(f"UNPARSEABLE_DEBIT: '{debit_raw}'")
    if c_bad:
        errors.append(f"UNPARSEABLE_CREDIT: '{credit_raw}'")
    # many statements print 0.00 in the unused column
    if debit is not None and credit is not None:
        if debit == 0 and credit != 0:
            debit = None
        elif credit == 0 and debit != 0:
            credit = None
    if amount_raw and debit is None and credit is None:
        amt, a_bad = _signed(amount_raw)
        flag = (drcr_raw or N.amount_drcr(amount_raw) or "").upper().strip(".")
        if a_bad or amt is None:
            errors.append(f"UNPARSEABLE_AMOUNT: '{amount_raw}'")
        elif flag.startswith("D") or (not flag and amt < 0):
            debit = abs(amt)
        elif flag.startswith("C"):
            credit = abs(amt)
        else:
            errors.append(f"AMOUNT_DIRECTION_UNKNOWN: '{amount_raw}' has no Dr/Cr indicator")
    if debit is not None:
        debit = abs(debit)
    if credit is not None:
        credit = abs(credit)
    if debit and credit:
        errors.append("BOTH_DEBIT_AND_CREDIT: row has both debit and credit amounts")
    if debit is None and credit is None and not any(e.startswith("UNPARSEABLE") for e in errors):
        errors.append("NO_AMOUNT: neither debit nor credit present")
    if balance is None and not bal_bad:
        errors.append("NO_BALANCE: running balance missing")

    parsed = {
        "date": txn_date.isoformat() if txn_date else None,
        "value_date": value_date.isoformat() if value_date else None,
        "description": desc,
        "reference": _cell(cells, mapping, "reference"),
        "debit": N.decimal_str(debit),
        "credit": N.decimal_str(credit),
        "balance": N.decimal_str(balance),
        "balance_raw": bal_raw,
        "debit_raw": debit_raw,
        "credit_raw": credit_raw,
        "amount_raw": amount_raw,
    }
    if txn_date is None and debit is None and credit is None and balance is None:
        status, kind = RowStatus.FAILED, RowKind.UNPARSED
    elif errors:
        status, kind = RowStatus.NEEDS_REVIEW, RowKind.TRANSACTION
    else:
        status, kind = RowStatus.EXTRACTED, RowKind.TRANSACTION
    conf = 0.95 - 0.15 * len(errors)
    return RowResult(i, page, bbox, cells, kind, status, parsed=parsed, errors=errors,
                     confidence=round(max(0.1, conf) * factor, 3))


def _attach_continuations(rows: list[RowResult]) -> None:
    """Attach wrapped-narration rows to the nearest transaction.

    Same page: whichever transaction is vertically closer (ties -> the one above), so narration
    printed above a vertically-centred date line attaches downwards. At the top of a page the
    text continues the last transaction of the previous page.
    """
    txn_idx = [k for k, r in enumerate(rows) if r.kind == RowKind.TRANSACTION]
    for k, r in enumerate(rows):
        if r.kind != RowKind.CONTINUATION:
            continue
        prev = next((rows[j] for j in reversed(txn_idx) if j < k), None)
        nxt = next((rows[j] for j in txn_idx if j > k), None)
        target = prev
        # attach downwards only when clearly closer to the next transaction (ties -> above)
        tol = 0.25 * ((r.bbox[3] - r.bbox[1]) if r.bbox else 1.0)
        if prev is None or (nxt is not None and prev.page_number == r.page_number == nxt.page_number
                            and _gap(prev, r) > _gap(r, nxt) + tol):
            target = nxt if nxt is not None and (prev is None or nxt.page_number == r.page_number) else prev
        if target is None or target.parsed is None:
            r.kind, r.status = RowKind.UNPARSED, RowStatus.FAILED
            r.errors.append("ORPHAN_TEXT_ROW: text row with no transaction to attach to")
            continue
        text = (r.parsed or {}).get("text", "")
        before = target.row_index > r.row_index
        parts = [text, target.parsed.get("description")] if before else [target.parsed.get("description"), text]
        target.parsed["description"] = " ".join(p for p in parts if p)
        r.parsed = {"continuation_of": target.row_index, "text": text, "attached": "below" if before else "above"}
        r.status = RowStatus.VALIDATED


def _gap(upper: RowResult, lower: RowResult) -> float:
    if upper.bbox is None or lower.bbox is None:
        return float(lower.row_index - upper.row_index)
    return lower.bbox[1] - upper.bbox[3]
