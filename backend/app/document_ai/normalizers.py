"""Deterministic value parsing/normalisation (Indian formats). Returns None - never a guess."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b")
GSTIN_RE = re.compile(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b")
IFSC_RE = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")
UDYAM_RE = re.compile(r"\b(UDYAM-[A-Z]{2}-\d{2}-\d{7})\b")
CIN_RE = re.compile(r"\b([LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6})\b")
LLPIN_RE = re.compile(r"\b([A-Z]{3}-\d{4})\b")

# One amount token, e.g. "₹1,25,000", "1,25,000.00", "12,500 Dr", "12,500.00CR", "Rs.1,25,000/-",
# "(1,25,000)", "-1,25,000", "INR 1,25,000.00". Dr/Cr is captured but NOT applied as a sign here.
_AMOUNT_TOKEN = re.compile(
    r"""
    (?P<neg>\(|-)?\s*
    (?:₹|Rs\.?|INR)?\s*
    (?P<neg2>-)?\s*
    (?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)
    \s*(?:/-)?\s*\)?
    \s*(?P<drcr>dr|cr)?\.?
    """,
    re.VERBOSE | re.IGNORECASE,
)
_CUR = r"(?:₹|Rs\.?|INR)\s?"
_TAIL = r"(?![\d,]|\.\d)(?:\s?/-)?\)?(?:\s?(?i:dr|cr)\b\.?)?"
# Money-formatted numbers in running text: digit grouping, exactly 2 decimals, or a currency
# prefix. The lookahead rejects fragments of dates/identifiers such as "31.03" in "31.03.2025".
AMOUNT_IN_TEXT = re.compile(
    rf"(?<![\w/.-])\(?-?\s?(?:{_CUR})?\d{{1,3}}(?:,\d{{2,3}})+(?:\.\d{{1,2}})?{_TAIL}"
    rf"|(?<![\w/.-])\(?-?\s?(?:{_CUR})?\d+\.\d{{2}}{_TAIL}"
    rf"|(?<![\w/.-])\(?-?\s?{_CUR}\d+{_TAIL}"
)
# Plain integers (e.g. ITR-V "1250000", P&L without grouping). Years and small numbers are excluded
# by find_plain_numbers().
_PLAIN_NUMBER = re.compile(r"(?<![\w/.,-])-?\d{4,}(?![\w.,/-]|\.\d)")
_PERCENT = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}

_DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b"),  # dd/mm/yyyy
    re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})\b"),  # dd/mm/yy
    re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b"),  # yyyy-mm-dd / yyyy/mm/dd
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-/]+([A-Za-z]{3,9})[,\s\-/]+(\d{2,4})\b"),  # 05 Apr 2024
    re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b"),  # April 5, 2024
]
DATE_IN_TEXT = re.compile(
    r"\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}\b|\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?[\s\-/]+[A-Za-z]{3,9}[,\s\-/]+\d{2,4}\b"
)


def parse_amount(text: str | None) -> Decimal | None:
    """'68,20,000.00' -> 6820000.00 ; '(1,200)' / '-1,200' / '1,200 Dr' -> -1200 (Dr only if signed=True caller)."""
    if text is None:
        return None
    s = str(text).strip()
    if not s or s in {"-", "--", "—", "nil", "Nil", "NIL"}:
        return None
    m = _AMOUNT_TOKEN.fullmatch(s.replace(" ", " ").strip())
    if not m:
        return None
    try:
        value = Decimal(m.group("num").replace(",", ""))
    except InvalidOperation:
        return None
    if m.group("neg") or m.group("neg2"):
        value = -value
    return value


def amount_drcr(text: str | None) -> str | None:
    """'12,500 Dr' -> 'DR', '12,500.00CR' -> 'CR'."""
    if not text:
        return None
    m = re.search(r"(?<![A-Za-z])(Dr|Cr)\.?\s*$", str(text).strip(), re.IGNORECASE)
    return m.group(1).upper() if m else None


def find_amounts(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0).strip(), m.start(), m.end()) for m in AMOUNT_IN_TEXT.finditer(text)]


def find_plain_numbers(text: str, min_digits: int = 4) -> list[tuple[str, int, int]]:
    """Unformatted integers likely to be amounts (excludes 4-digit years 1900-2099)."""
    out = []
    for m in _PLAIN_NUMBER.finditer(text):
        tok = m.group(0)
        digits = tok.lstrip("-")
        if len(digits) < min_digits:
            continue
        if len(digits) == 4 and 1900 <= int(digits) <= 2099:
            continue
        out.append((tok, m.start(), m.end()))
    return out


def parse_percent(text: str | None) -> Decimal | None:
    if not text:
        return None
    m = _PERCENT.search(str(text))
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except InvalidOperation:
        return None


_UNIT_RE = re.compile(
    r"\bin\s+(?:₹\s*|rs\.?\s*|inr\s*|rupees\s*)?(lakhs?|lacs?|crores?|thousands?|'000|millions?)\b",
    re.IGNORECASE,
)
_UNIT_MULTIPLIER = [
    ("lakh", Decimal(100000)), ("lac", Decimal(100000)), ("crore", Decimal(10000000)),
    ("thousand", Decimal(1000)), ("'000", Decimal(1000)), ("million", Decimal(1000000)),
]


def detect_amount_unit(text: str) -> tuple[Decimal, str] | None:
    """'(Amount in ₹ Lakhs)' -> (100000, 'Lakhs'). Only an explicit 'in <unit>' statement counts."""
    m = _UNIT_RE.search(text or "")
    if not m:
        return None
    word = m.group(1).lower()
    for key, mult in _UNIT_MULTIPLIER:
        if word.startswith(key):
            return mult, m.group(1)
    return None


# --------------------------------------------------------------------------- OCR repair
# Position-aware correction of the usual OCR confusions inside fixed-shape identifiers.
# Shape: 'A' = letter, '9' = digit, '*' = letter or digit, '0' = the digit zero, else literal.
_TO_DIGIT = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "|": "1", "Z": "2", "S": "5", "B": "8",
             "G": "6", "T": "7"}
_TO_ALPHA = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G", "7": "T", "4": "A"}
ID_SHAPES = {
    "pan": ["AAAAA9999A"],
    "gstin": ["99AAAAA9999A*Z*"],
    "ifsc": ["AAAA0******"],
    "udyam": ["UDYAM-AA-99-9999999"],
}


def _repair_to_shape(s: str, shape: str) -> str | None:
    if len(s) != len(shape):
        return None
    out = []
    for ch, sh in zip(s, shape):
        if sh == "A":
            ch = _TO_ALPHA.get(ch, ch)
            if not ch.isalpha():
                return None
        elif sh == "9":
            ch = _TO_DIGIT.get(ch, ch)
            if not ch.isdigit():
                return None
        elif sh == "0":
            ch = _TO_DIGIT.get(ch, ch)
            if ch != "0":
                return None
        elif sh == "*":
            if not ch.isalnum():
                return None
        elif ch != sh:
            return None
        out.append(ch)
    return "".join(out)


def repair_identifier(raw: str, kind: str) -> str | None:
    """Recover a shape-conforming identifier from OCR text (O<->0, I<->1, S<->5, B<->8 ...), or None.

    Only used for OCR-sourced text: in a native PDF a malformed ID is a real defect and is
    reported, never corrected.
    """
    compact = re.sub(r"[\s.:]", "", raw.upper())
    for shape in ID_SHAPES.get(kind, []):
        n = len(shape)
        for i in range(0, max(1, len(compact) - n + 1)):
            fixed = _repair_to_shape(compact[i:i + n], shape)
            if fixed:
                return fixed
    return None


def _year(y: int) -> int:
    if y < 100:
        return 2000 + y if y < 70 else 1900 + y
    return y


def parse_date(text: str | None) -> date | None:
    """Day-first (Indian convention). Returns None when ambiguous or invalid."""
    if text is None:
        return None
    if isinstance(text, datetime):
        return text.date()
    if isinstance(text, date):
        return text
    s = str(text).strip()
    if not s:
        return None
    for i, pat in enumerate(_DATE_PATTERNS):
        m = pat.search(s)
        if not m:
            continue
        try:
            if i in (0, 1):
                d, mo, y = int(m.group(1)), int(m.group(2)), _year(int(m.group(3)))
            elif i == 2:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif i == 3:
                mo = MONTHS.get(m.group(2).lower())
                if mo is None:
                    continue
                d, y = int(m.group(1)), _year(int(m.group(3)))
            else:
                mo = MONTHS.get(m.group(1).lower())
                if mo is None:
                    continue
                d, y = int(m.group(2)), int(m.group(3))
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def normalize_whitespace(s: str | None) -> str | None:
    if s is None:
        return None
    out = re.sub(r"\s+", " ", s).strip()
    return out or None


def normalize_id(s: str | None) -> str | None:
    if s is None:
        return None
    out = re.sub(r"[\s]", "", s).upper()
    return out or None


_NAME_NOISE = re.compile(r"[^A-Z0-9& ]+")
_NAME_SUFFIXES = {
    "M/S": "",
    "MS": "",
    "PVT": "PRIVATE",
    "PRIVATE": "PRIVATE",
    "LTD": "LIMITED",
    "LIMITED": "LIMITED",
    "CO": "COMPANY",
    "&": "AND",
}
LEGAL_FORM_TOKENS = {"PRIVATE", "LIMITED", "LLP", "COMPANY", "AND", "THE", "OPC"}


def normalize_name(s: str | None) -> str | None:
    """Uppercase, strip punctuation/titles, canonicalise legal-form tokens."""
    if not s:
        return None
    up = s.upper().replace("M/S.", " ").replace("M/S", " ")
    up = _NAME_NOISE.sub(" ", up.replace(".", " "))
    tokens = []
    for t in up.split():
        t = _NAME_SUFFIXES.get(t, t)
        if t:
            tokens.append(t)
    return " ".join(tokens) or None


def name_core_tokens(s: str | None) -> list[str]:
    n = normalize_name(s)
    if not n:
        return []
    return [t for t in n.split() if t not in LEGAL_FORM_TOKENS]


def fy_label(d: date) -> str:
    """Financial year (Apr-Mar) label for a date, e.g. 2024-25."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


_FY_RE = re.compile(r"\b(?:FY\s*)?(20\d{2})\s*[-–/]\s*(\d{2}|20\d{2})\b")


def parse_fy(text: str | None) -> str | None:
    """'FY 2024-25' / '2024-2025' -> '2024-25'."""
    if not text:
        return None
    m = _FY_RE.search(text)
    if not m:
        return None
    start = int(m.group(1))
    end = m.group(2)
    end_full = int(end) if len(end) == 4 else 2000 + int(end)
    if end_full != start + 1:
        return None
    return f"{start}-{str(start + 1)[-2:]}"


def assessment_year_to_fy(ay: str | None) -> str | None:
    """AY 2025-26 corresponds to FY 2024-25."""
    fy = parse_fy(ay)
    if not fy:
        return None
    start = int(fy[:4]) - 1
    return f"{start}-{str(start + 1)[-2:]}"


def decimal_str(d: Decimal | None) -> str | None:
    if d is None:
        return None
    return f"{d.quantize(Decimal('0.01'))}"
