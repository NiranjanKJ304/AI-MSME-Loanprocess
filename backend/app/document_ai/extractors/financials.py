"""Profit & Loss and Balance Sheet extractors.

Line items are matched from financial-statement tables (label + amount columns) using
synonym lists anchored at the start of the row label. When a row matches several fields,
the longest synonym wins (so "Net profit before tax" is PBT, not "Net profit").
Where several period columns exist, the column whose header carries the latest year is
used (else the first amount column, the Schedule III convention) and a warning says so.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.document_ai import normalizers as N
from app.document_ai.extractor import (
    BaseExtractor,
    ExtractionContext,
    FieldCandidate,
    FieldSpec,
    TableResult,
)
from app.document_ai.schemas import BalanceSheetSchema, ProfitLossSchema
from app.models.enums import DocumentType, ExtractionMethod, RowKind, TableType

# item numbers, "Less:"/"Add:", and T-format account prefixes "To"/"By" (Dr / Cr sides)
_LABEL_PREFIX = re.compile(
    r"^\s*(?:\(?[ivxIVX]{1,4}[.)]|\(?\d{1,2}[.)]|\(?[a-hA-H][.)]|less\s*:|add\s*:)?\s*(?:(?:to|by)\s+)?", re.I)
_PERIOD_RE = re.compile(
    r"(?:year\s+ended|period\s+ended|as\s+(?:at|on))\s*(?P<d>[0-9]{1,2}(?:st|nd|rd|th)?[\s\-/.]*[A-Za-z0-9]{2,9}[\s,\-/.]*\d{4})",
    re.IGNORECASE,
)


def _clean_label(label: str | None) -> str:
    if not label:
        return ""
    s = _LABEL_PREFIX.sub("", label)
    return re.sub(r"\s+", " ", s).strip().lower()


class FinancialStatementExtractor(BaseExtractor):
    synonyms: dict[str, list[str]] = {}
    summable: dict[str, list[str]] = {}  # field -> component synonyms that are summed
    period_spec = FieldSpec("period", "fy", required=True)

    def extract_line_items(
        self, ctx: ExtractionContext, tables: list[TableResult], warnings: list[str]
    ) -> dict[str, FieldCandidate]:
        compiled = {
            f: [(p, re.compile(rf"^(?:{p})\b", re.I)) for p in pats] for f, pats in self.synonyms.items()
        }
        hits: dict[str, list[tuple[int, FieldCandidate]]] = {}
        for t in tables:
            if t.table_type != TableType.FINANCIAL_STATEMENT:
                continue
            col_choice, col_warning = self._current_period_column(t)
            if col_warning and col_warning not in warnings:
                warnings.append(col_warning)
            for row in t.rows:
                if row.kind != RowKind.LINE_ITEM or not row.parsed:
                    continue
                label = _clean_label(row.parsed.get("label"))
                if not label:
                    continue
                best: tuple[int, str, int] | None = None  # (match_len, field, synonym_priority)
                for fname, pats in compiled.items():
                    for prio, (_, rx) in enumerate(pats):
                        m = rx.match(label)
                        if m and (best is None or len(m.group(0)) > best[0]):
                            best = (len(m.group(0)), fname, prio)
                if best is None:
                    continue
                _, fname, prio = best
                raw_amount = self._pick_amount(row.raw_cells, col_choice)
                if raw_amount is None:
                    continue
                spec = self._spec(fname)
                page = ctx.parsed.page(row.page_number)
                c = ctx.candidate(
                    spec, raw_amount, page=page, bbox=row.bbox,
                    snippet=" | ".join(x for x in row.raw_cells if x),
                    method=ExtractionMethod.TABLE, position="TABLE_ROW",
                    label=row.parsed.get("label"), table_index=t.raw.index, row_index=row.row_index,
                    priority=prio,
                )
                hits.setdefault(fname, []).append((prio, c))

        result: dict[str, FieldCandidate] = {}
        for fname, cands in hits.items():
            components = self.summable.get(fname)
            comp_hits = [
                c for _, c in cands
                if components and any(re.match(rf"^(?:{p})\b", _clean_label(c.label), re.I) for p in components)
            ]
            distinct_labels = {_clean_label(c.label) for c in comp_hits}
            if components and len(distinct_labels) > 1:
                result[fname] = self._sum_components(fname, comp_hits)
                continue
            cands.sort(key=lambda pc: (pc[0], -pc[1].confidence))
            best = cands[0][1]
            for prio, c in cands[1:]:
                best.alternatives.append(
                    {"value": c.raw_value, "normalized_value": c.normalized_value, "page": c.page,
                     "label": c.label, "confidence": c.confidence, "method": c.method.value}
                )
                if prio == cands[0][0] and c.normalized_value != best.normalized_value and not best.ambiguous:
                    best.ambiguous = True
                    best.warnings.append(f"CONFLICTING_VALUES: '{c.label}' = {c.raw_value} on page {c.page}")
                    best.confidence = round(best.confidence * 0.7, 3)
            result[fname] = best
        return result

    def _sum_components(self, fname: str, comps: list[FieldCandidate]) -> FieldCandidate:
        total = sum((c.typed_value for c in comps if isinstance(c.typed_value, Decimal)), Decimal("0"))
        first = comps[0]
        return FieldCandidate(
            name=fname, value_type="amount",
            raw_value=" + ".join(c.raw_value or "" for c in comps),
            normalized_value=N.decimal_str(total), typed_value=total,
            confidence=round(min(c.confidence for c in comps) * 0.97, 3),
            page=first.page, bbox=first.bbox, page_size=first.page_size, snippet=first.snippet,
            position="DERIVED", text_source=first.text_source,
            method=ExtractionMethod.DERIVED, label=" + ".join(c.label or "" for c in comps),
            table_index=first.table_index, row_index=first.row_index,
            warnings=[f"DERIVED: sum of {len(comps)} line items"],
            components=[
                {"label": c.label, "value": c.raw_value, "page": c.page,
                 "bbox": list(c.bbox) if c.bbox else None, "row_index": c.row_index,
                 "table_index": c.table_index}
                for c in comps
            ],
        )

    def _current_period_column(self, t: TableResult) -> tuple[int | None, str | None]:
        """Index (within the row's amount cells) of the current-period column."""
        n_amounts = max(
            (len(r.parsed.get("amounts", [])) for r in t.rows if r.parsed and r.kind == RowKind.LINE_ITEM),
            default=0,
        )
        if n_amounts < 2:
            return 0, None
        hi = t.analysis.header_index
        if hi is None:
            return 0, "MULTI_PERIOD_COLUMNS: no period header; first amount column taken as current period"
        # Year tokens in header order (one per period column); works for ruled and layout tables.
        years: list[int] = []
        labels: list[str] = []
        for cell in t.raw.rows[hi]:
            ys = [int(y) for y in re.findall(r"20\d{2}", cell or "")]
            if ys:
                years.append(max(ys))
                labels.append(cell or "")
        if len(years) == n_amounts:
            idx = years.index(max(years))
            return idx, f"MULTI_PERIOD_COLUMNS: used column '{labels[idx]}' (latest year) as current period"
        return 0, "MULTI_PERIOD_COLUMNS: first amount column taken as current period"

    @staticmethod
    def _pick_amount(cells: list[str | None], idx: int | None) -> str | None:
        # Money-formatted cells only (commas/decimals) so "Note" numbers are not mistaken for amounts
        cells = [re.sub(r"\s+", " ", c).strip() if c else c for c in cells]
        amounts = [c for c in cells[1:] if c and N.AMOUNT_IN_TEXT.fullmatch(c)]
        if not amounts:
            amounts = [c for c in cells[1:] if c and re.fullmatch(r"\(?-?\d{3,}\)?", c)]
        if not amounts:
            return None
        i = idx if idx is not None and idx < len(amounts) else 0
        return amounts[i]

    def _spec(self, name: str) -> FieldSpec:
        return next(s for s in self.field_specs if s.name == name)

    def find_period(self, ctx: ExtractionContext) -> FieldCandidate | None:
        for page, _, line in ctx.lines():
            m = _PERIOD_RE.search(line.text)
            if m:
                d = N.parse_date(m.group("d"))
                if d:
                    fy = N.fy_label(d)
                    c = ctx.candidate(
                        FieldSpec("period", "string", required=True), m.group(0), page=page,
                        bbox=line.bbox_for_span(m.start(), m.end()) if line.words else None, snippet=line.text,
                        method=ExtractionMethod.LABEL, position="INLINE", label="period",
                        word_conf=line.ocr_conf_for_span(m.start(), m.end()) if line.words else None,
                    )
                    # the period is the financial year containing the closing date
                    c.value_type = "fy"
                    c.normalized_value = c.typed_value = fy
                    c.warnings.append(f"DERIVED_FY: '{m.group('d')}' falls in FY {fy}")
                    return c
        for page, _, line in ctx.lines():
            fy = N.parse_fy(line.text)
            if fy and re.search(r"\bF\.?Y\.?|financial\s+year", line.text, re.I):
                return ctx.candidate(
                    self.period_spec, fy, page=page, bbox=line.bbox, snippet=line.text,
                    method=ExtractionMethod.LABEL, position="INLINE",
                )
        return None

    def apply_amount_unit(self, ctx: ExtractionContext, found: dict[str, FieldCandidate], warnings: list[str]) -> None:
        """Scale amounts when the statement says so ('Amount in ₹ lakhs'). Raw values are untouched."""
        for page in ctx.parsed.pages:
            unit = N.detect_amount_unit("\n".join(ln.text for ln in page.lines[:20]))
            if unit:
                break
        else:
            return
        mult, word = unit
        warnings.append(f"AMOUNT_UNIT: statement amounts are in {word}; normalised values scaled x{mult}")
        for c in found.values():
            if c.value_type == "amount" and isinstance(c.typed_value, Decimal):
                c.typed_value = c.typed_value * mult
                c.normalized_value = N.decimal_str(c.typed_value)
                c.warnings.append(f"UNIT_SCALED: raw '{c.raw_value}' in {word} x{mult}")
                if c.confidence_factors is not None:
                    c.confidence_factors["unit_multiplier"] = str(mult)

    def post_process(self, ctx, found, tables, warnings):
        found.update(self.extract_line_items(ctx, tables, warnings))
        period = self.find_period(ctx)
        if period:
            found["period"] = period
        self.apply_amount_unit(ctx, found, warnings)
        if not any(t.table_type == TableType.FINANCIAL_STATEMENT for t in tables):
            warnings.append("NO_FINANCIAL_TABLE: no line-item table detected")


class ProfitLossExtractor(FinancialStatementExtractor):
    document_type = DocumentType.PROFIT_LOSS
    schema = ProfitLossSchema
    field_specs = [
        FinancialStatementExtractor.period_spec,
        FieldSpec("revenue", "amount", required=True, non_negative=True),
        FieldSpec("other_income", "amount", non_negative=True),
        FieldSpec("cost_of_goods_sold", "amount", non_negative=True),
        FieldSpec("gross_profit", "amount"),
        FieldSpec("operating_expenses", "amount", non_negative=True),
        FieldSpec("operating_profit", "amount"),
        FieldSpec("ebitda", "amount"),
        FieldSpec("depreciation", "amount", non_negative=True),
        FieldSpec("interest", "amount", non_negative=True),
        FieldSpec("profit_before_tax", "amount", required=True),
        FieldSpec("tax", "amount"),
        FieldSpec("profit_after_tax", "amount", required=True),
    ]
    synonyms = {
        "revenue": [r"revenue\s+from\s+operations", r"net\s+sales", r"sales(?:\s+(?:and|&)\s+services)?",
                    r"turnover", r"gross\s+receipts", r"income\s+from\s+operations", r"total\s+revenue"],
        "cost_of_goods_sold": [r"cost\s+of\s+goods\s+sold", r"cost\s+of\s+sales", r"cost\s+of\s+materials\s+consumed",
                               r"purchases?\s+of\s+stock[-\s]in[-\s]trade", r"purchases"],
        "gross_profit": [r"gross\s+profit"],
        "other_income": [r"other\s+(?:operating\s+)?income", r"non[-\s]operating\s+income", r"miscellaneous\s+income"],
        "operating_profit": [r"operating\s+profit", r"profit\s+from\s+operations", r"ebit(?!da)",
                             r"earnings\s+before\s+interest\s+(?:and|&)\s+tax(?:es)?"],
        "operating_expenses": [r"total\s+operating\s+expenses", r"operating\s+expenses",
                               r"administrative\s+(?:and|&)\s+other\s+expenses", r"other\s+expenses"],
        "ebitda": [r"ebitda", r"earnings\s+before\s+interest,?\s+tax(?:es)?,?\s+depreciation(?:\s+(?:and|&)\s+amorti[sz]ation)?"],
        "depreciation": [r"depreciation\s+(?:and|&)\s+amorti[sz]ation(?:\s+expenses?)?", r"depreciation"],
        "interest": [r"finance\s+costs?", r"interest(?:\s+(?:expense|paid|on\s+loans))?"],
        "profit_before_tax": [r"(?:net\s+)?profit\s+before\s+tax(?:ation)?", r"pbt"],
        "tax": [r"total\s+tax\s+expenses?", r"tax\s+expenses?", r"provision\s+for\s+(?:income\s+)?tax",
                r"income\s+tax(?:\s+expense)?", r"current\s+tax"],
        "profit_after_tax": [r"(?:net\s+)?profit\s+after\s+tax", r"profit\s+for\s+the\s+(?:year|period)",
                             r"net\s+profit", r"pat"],
    }


class BalanceSheetExtractor(FinancialStatementExtractor):
    document_type = DocumentType.BALANCE_SHEET
    schema = BalanceSheetSchema
    field_specs = [
        FinancialStatementExtractor.period_spec,
        FieldSpec("fixed_assets", "amount", non_negative=True),
        FieldSpec("inventory", "amount", non_negative=True),
        FieldSpec("receivables", "amount", non_negative=True),
        FieldSpec("cash", "amount", non_negative=True),
        FieldSpec("bank_balance", "amount"),
        FieldSpec("other_current_assets", "amount", non_negative=True),
        FieldSpec("current_assets", "amount", non_negative=True),
        FieldSpec("total_assets", "amount", required=True, non_negative=True),
        FieldSpec("capital", "amount"),
        FieldSpec("reserves", "amount"),
        FieldSpec("borrowings", "amount", non_negative=True),
        FieldSpec("trade_payables", "amount", non_negative=True),
        FieldSpec("current_liabilities", "amount", non_negative=True),
        FieldSpec("net_worth", "amount"),
        FieldSpec("other_liabilities", "amount", non_negative=True),
        FieldSpec("total_liabilities", "amount", required=True, non_negative=True),
    ]
    synonyms = {
        "fixed_assets": [r"property,?\s+plant\s+(?:and|&)\s+equipment", r"fixed\s+assets", r"net\s+block",
                         r"tangible\s+assets"],
        "inventory": [r"inventories", r"inventory", r"closing\s+stock", r"stock[-\s]in[-\s]trade"],
        "receivables": [r"trade\s+receivables", r"sundry\s+debtors", r"debtors", r"receivables"],
        "cash": [r"cash\s+and\s+cash\s+equivalents", r"cash\s+in\s+hand", r"cash\s+on\s+hand", r"cash"],
        "bank_balance": [r"balances?\s+with\s+banks?", r"bank\s+balances?", r"cash\s+at\s+bank"],
        "other_current_assets": [r"other\s+current\s+assets", r"short[-\s]term\s+loans\s+(?:and|&)\s+advances",
                                 r"loans\s+(?:and|&)\s+advances"],
        "total_assets": [r"total\s+assets"],
        "current_assets": [r"total\s+current\s+assets", r"current\s+assets"],
        "current_liabilities": [r"total\s+current\s+liabilities", r"current\s+liabilities"],
        "net_worth": [r"net\s+worth", r"total\s+equity", r"total\s+shareholders'?\s+funds?", r"shareholders'?\s+funds?",
                      r"owners'?\s+equity"],
        "capital": [r"share\s+capital", r"partners'?\s+capital(?:\s+accounts?)?", r"proprietor'?s?\s+capital",
                    r"capital\s+account", r"capital"],
        "reserves": [r"reserves\s+(?:and|&)\s+surplus", r"reserves", r"retained\s+earnings"],
        "borrowings": [r"long[-\s]term\s+borrowings", r"short[-\s]term\s+borrowings", r"secured\s+loans",
                       r"unsecured\s+loans", r"total\s+borrowings", r"borrowings"],
        "trade_payables": [r"trade\s+payables", r"sundry\s+creditors", r"creditors"],
        "other_liabilities": [r"other\s+current\s+liabilities", r"other\s+liabilities",
                              r"short[-\s]term\s+provisions", r"provisions"],
        "total_liabilities": [r"total\s+equity\s+(?:and|&)\s+liabilities", r"total\s+capital\s+(?:and|&)\s+liabilities",
                              r"total\s+liabilities"],
    }
    summable = {
        "borrowings": [r"long[-\s]term\s+borrowings", r"short[-\s]term\s+borrowings", r"secured\s+loans",
                       r"unsecured\s+loans"],
    }
