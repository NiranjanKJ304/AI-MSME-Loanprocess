"""Period normalisation.

Equivalent expressions map to ONE canonical period:

    "FY2024-25", "FY 24-25", "2024-25", "2024-2025", "F.Y. 2024-25"     -> FY2024-25
    01-Apr-2024 .. 31-Mar-2025 (any date format)                         -> FY2024-25
    "AY 2025-26" (assessment year)                                       -> FY2024-25
    "March 2025", "Mar-25", "03/2025", 01-03-2025 .. 31-03-2025          -> 2025-03 (MONTH)
    01-Apr-2024 .. 30-Jun-2024                                           -> FY2024-25-Q1 (QUARTER)
    any other range                                                      -> 2024-04-01..2024-10-31 (CUSTOM)

Nothing is guessed: an expression that cannot be read returns None, and different financial
years always produce different keys.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

from app.document_ai import normalizers as N
from app.models.enums import PeriodType


@dataclass(frozen=True)
class CanonicalPeriod:
    key: str
    period_type: PeriodType
    start: date
    end: date
    fiscal_year: str | None  # "2024-25" - FY the period ends in
    label: str

    def contains(self, other: CanonicalPeriod) -> bool:
        return self.start <= other.start and other.end <= self.end

    def overlaps(self, other: CanonicalPeriod) -> bool:
        return self.start <= other.end and other.start <= self.end


def fy_start_year(d: date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def fy_label_for(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def fiscal_year(start_year: int) -> CanonicalPeriod:
    fy = fy_label_for(start_year)
    return CanonicalPeriod(f"FY{fy}", PeriodType.FY, date(start_year, 4, 1), date(start_year + 1, 3, 31), fy,
                           f"FY {fy}")


_FY_TEXT = re.compile(r"(?:F\.?\s?Y\.?\s*)?(?P<a>(?:19|20)?\d{2})\s*[-–/]\s*(?P<b>(?:19|20)?\d{2})(?!\d)", re.I)


def parse_fiscal_year(text: str | None) -> CanonicalPeriod | None:
    """'FY2024-25' / '2024-2025' / 'FY 24-25' / '2024/25' -> FY period. Requires consecutive years."""
    if not text:
        return None
    m = _FY_TEXT.search(str(text))
    if not m:
        return None
    a, b = m.group("a"), m.group("b")
    start = int(a) if len(a) == 4 else 2000 + int(a)
    end = int(b) if len(b) == 4 else (start // 100) * 100 + int(b)
    if end < start:  # "2099-00" style century wrap
        end += 100
    if end != start + 1:
        return None
    return fiscal_year(start)


def from_assessment_year(text: str | None) -> CanonicalPeriod | None:
    ay = parse_fiscal_year(text)
    return fiscal_year(ay.start.year - 1) if ay else None


def _month(y: int, m: int) -> CanonicalPeriod:
    last = calendar.monthrange(y, m)[1]
    st = date(y, m, 1)
    return CanonicalPeriod(f"{y}-{m:02d}", PeriodType.MONTH, st, date(y, m, last), fy_label_for(fy_start_year(st)),
                           st.strftime("%b %Y"))


def from_range(start: date, end: date) -> CanonicalPeriod | None:
    if end < start:
        return None
    last_day = calendar.monthrange(end.year, end.month)[1]
    if start.day == 1 and end.day == last_day:
        if start.month == 4 and end == date(start.year + 1, 3, 31):
            return fiscal_year(start.year)
        if (start.year, start.month) == (end.year, end.month):
            return _month(start.year, start.month)
        fy_start = fy_start_year(start)
        if fy_start == fy_start_year(end) and start.month in (4, 7, 10, 1) and \
                (end.year * 12 + end.month) - (start.year * 12 + start.month) == 2:
            q = {4: 1, 7: 2, 10: 3, 1: 4}[start.month]
            fy = fy_label_for(fy_start)
            return CanonicalPeriod(f"FY{fy}-Q{q}", PeriodType.QUARTER, start, end, fy, f"Q{q} FY {fy}")
    fy = fy_label_for(fy_start_year(end))
    return CanonicalPeriod(f"{start.isoformat()}..{end.isoformat()}", PeriodType.CUSTOM, start, end, fy,
                           f"{start:%d %b %Y} - {end:%d %b %Y}")


_MONTH_YEAR = re.compile(r"\b([A-Za-z]{3,9})[\s\-/,']*(\d{2}|\d{4})\b")
_MM_YYYY = re.compile(r"\b(0?[1-9]|1[0-2])\s*[/\-.]\s*(\d{4})\b")


def parse_month(text: str | None) -> CanonicalPeriod | None:
    """'March 2025' / 'Mar-25' / "Mar'25" / '03/2025' -> month period."""
    if not text:
        return None
    s = str(text)
    m = _MONTH_YEAR.search(s)
    if m and m.group(1).lower() in N.MONTHS:
        y = int(m.group(2))
        y = y if y > 99 else 2000 + y
        return _month(y, N.MONTHS[m.group(1).lower()])
    m = _MM_YYYY.search(s)
    if m:
        return _month(int(m.group(2)), int(m.group(1)))
    return None


def normalize_period_expression(text: str | None) -> CanonicalPeriod | None:
    """Best canonical reading of a free-text period ('FY 2024-25', 'March 2025', '01/04/2024 to 31/03/2025')."""
    if not text:
        return None
    dates = [N.parse_date(d) for d in N.DATE_IN_TEXT.findall(text)]
    dates = [d for d in dates if d]
    if len(dates) >= 2:
        return from_range(dates[0], dates[1])
    fy = parse_fiscal_year(text)
    if fy:
        return fy
    return parse_month(text)
