"""Synthetic MSME documents for tests and the end-to-end sample application.

All entities, numbers and identifiers are fictitious. GSTINs carry valid check digits
(computed) so that format/checksum validation can be exercised realistically.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import pymupdf as fitz

from app.validation.rules import gstin_check_char

FONT = "helv"
BOLD = "hebo"


def make_gstin(pan: str, state: str = "33", entity: str = "1") -> str:
    first14 = f"{state}{pan}{entity}Z"
    return first14 + gstin_check_char(first14)


def inr(v: Decimal | float | int, decimals: bool = True) -> str:
    """Indian digit grouping: 6820000 -> 68,20,000.00"""
    d = Decimal(str(v)).quantize(Decimal("0.01"))
    neg = d < 0
    whole, frac = f"{abs(d):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    s = f"{whole}.{frac}" if decimals else whole
    return f"-{s}" if neg else s


class PdfWriter:
    """Tiny helper: write text at positions, with right alignment and simple pagination."""

    def __init__(self, width: float = 595, height: float = 842):
        self.doc = fitz.open()
        self.width, self.height = width, height
        self.page = self.new_page()
        self.y = 60.0

    def new_page(self) -> fitz.Page:
        self.page = self.doc.new_page(width=self.width, height=self.height)
        self.y = 60.0
        return self.page

    def text(self, x: float, s: str, size: float = 10, bold: bool = False, y: float | None = None) -> None:
        self.page.insert_text((x, y if y is not None else self.y), s, fontsize=size, fontname=BOLD if bold else FONT)

    def rtext(self, right: float, s: str, size: float = 10, y: float | None = None) -> None:
        w = fitz.get_text_length(s, fontname=FONT, fontsize=size)
        self.text(right - w, s, size, y=y)

    def line(self, gap: float = 16) -> None:
        self.y += gap

    def kv(self, label: str, value: str, x_label: float = 50, x_value: float = 260, size: float = 10) -> None:
        self.text(x_label, label, size)
        self.text(x_value, value, size)
        self.line()

    def hrule(self) -> None:
        self.page.draw_line((40, self.y - 10), (self.width - 40, self.y - 10), width=0.5)

    def bytes(self, **save_kwargs) -> bytes:
        return self.doc.tobytes(**save_kwargs)


# --------------------------------------------------------------------------- business profile
@dataclass
class Business:
    legal_name: str = "SRI LAKSHMI PRECISION COMPONENTS PRIVATE LIMITED"
    trade_name: str = "SRI LAKSHMI PRECISION"
    pan: str = "AAECS4821K"
    state_code: str = "33"
    address: tuple[str, str] = ("Plot 14, SIDCO Industrial Estate, Kurichi", "Coimbatore, Tamil Nadu 641021")
    incorporation_date: str = "12/06/2016"
    cin: str = "U29299TZ2016PTC027645"
    udyam: str = "UDYAM-TN-03-0045821"
    bank_name: str = "Kongu Co-operative Bank"
    account_number: str = "50200031457788"
    ifsc: str = "KCBL0000212"
    fy: str = "2024-25"
    revenue: Decimal = Decimal("6820000")

    @property
    def gstin(self) -> str:
        return make_gstin(self.pan, self.state_code)


# --------------------------------------------------------------------------- identity documents
def pan_card_pdf(pan: str, name: str, date_of_incorporation: str = "12/06/2016") -> bytes:
    w = PdfWriter(400, 260)
    w.text(30, "INCOME TAX DEPARTMENT", 12, bold=True)
    w.text(260, "GOVT. OF INDIA", 10, bold=True)
    w.line(22)
    w.text(30, "Permanent Account Number Card", 10)
    w.line(20)
    w.text(30, pan, 14, bold=True)
    w.line(26)
    w.text(30, "Name", 8)
    w.line(14)
    w.text(30, name, 10)
    w.line(22)
    w.text(30, "Date of Incorporation/Formation", 8)
    w.line(14)
    w.text(30, date_of_incorporation, 10)
    return w.bytes()


def gst_certificate_pdf(b: Business, gstin: str | None = None, legal_name: str | None = None,
                        omit_legal_name: bool = False) -> bytes:
    w = PdfWriter()
    w.text(230, "Government of India", 12, bold=True)
    w.line(18)
    w.text(240, "Form GST REG-06", 11, bold=True)
    w.line(14)
    w.text(245, "[See Rule 10(1)]", 9)
    w.line(20)
    w.text(225, "Registration Certificate", 12, bold=True)
    w.line(30)
    w.kv("Registration Number:", gstin or b.gstin)
    w.line(6)
    if not omit_legal_name:
        w.kv("1.  Legal Name", legal_name or b.legal_name)
    w.kv("2.  Trade Name, if any", b.trade_name)
    w.kv("3.  Constitution of Business", "Private Limited Company")
    w.text(50, "4.  Address of Principal Place of Business", 10)
    w.line()
    w.text(260, b.address[0], 10)
    w.line(14)
    w.text(260, b.address[1], 10)
    w.line(18)
    w.kv("5.  Date of Liability", "01/07/2017")
    w.kv("6.  Period of Validity", "From 01/07/2017   To  Not Applicable")
    w.kv("7.  Type of Registration", "Regular")
    w.kv("8.  Particulars of Approving Authority", "Goods and Services Tax Network")
    w.line(10)
    w.text(50, "This is a system generated digitally signed Registration Certificate.", 8)
    return w.bytes()


def gst_return_pdf(b: Business, return_type: str = "GSTR-9", fy: str = "2024-25",
                   taxable: Decimal = Decimal("6695000"), tax: Decimal = Decimal("1205100")) -> bytes:
    w = PdfWriter()
    w.text(200, f"FORM {return_type} - Annual Return", 12, bold=True)
    w.line(30)
    w.kv("Financial Year:", fy)
    w.kv("GSTIN:", b.gstin)
    w.kv("Legal Name of the Registered Person:", b.legal_name, x_value=280)
    w.kv("Trade Name (if any):", b.trade_name)
    w.kv("Date of Filing:", "28/11/2025")
    w.line(10)
    w.text(50, "Details of Outward supplies", 10, bold=True)
    w.line(18)
    w.text(50, "Nature of Supplies", 9, bold=True)
    w.rtext(420, "Taxable Value", 9)
    w.rtext(540, "Tax", 9)
    w.line(16)
    w.text(50, "Outward taxable supplies", 9)
    w.rtext(420, inr(taxable), 9)
    w.rtext(540, inr(tax), 9)
    w.line(16)
    w.text(50, "Total Taxable Value", 9)
    w.rtext(420, inr(taxable), 9)
    w.line(16)
    w.text(50, "Total Tax Payable", 9)
    w.rtext(540, inr(tax), 9)
    return w.bytes()


def udyam_pdf(b: Business, name: str | None = None) -> bytes:
    w = PdfWriter()
    w.text(150, "Government of India", 12, bold=True)
    w.line(16)
    w.text(120, "Ministry of Micro, Small and Medium Enterprises", 11)
    w.line(22)
    w.text(170, "UDYAM REGISTRATION CERTIFICATE", 12, bold=True)
    w.line(30)
    w.kv("UDYAM REGISTRATION NUMBER", b.udyam)
    w.kv("NAME OF ENTERPRISE", name or b.legal_name)
    w.kv("TYPE OF ENTERPRISE", "MICRO (2024-25)")
    w.kv("MAJOR ACTIVITY", "MANUFACTURING")
    w.kv("SOCIAL CATEGORY OF ENTREPRENEUR", "GENERAL")
    w.kv("DATE OF INCORPORATION", b.incorporation_date)
    w.kv("DATE OF UDYAM REGISTRATION", "15/09/2020")
    w.kv("NIC 5 DIGIT CODE", "28221")
    return w.bytes()


def incorporation_certificate_pdf(b: Business) -> bytes:
    w = PdfWriter()
    w.text(180, "Government of India", 12, bold=True)
    w.line(16)
    w.text(160, "Ministry of Corporate Affairs", 11)
    w.line(16)
    w.text(150, "Registrar of Companies, Coimbatore", 10)
    w.line(24)
    w.text(175, "Certificate of Incorporation", 13, bold=True)
    w.line(30)
    w.kv("Corporate Identity Number:", b.cin)
    w.kv("Name of the Company:", b.legal_name)
    w.kv("Date of Incorporation:", b.incorporation_date)
    w.kv("Issued by:", "Registrar of Companies, Coimbatore")
    w.kv("Registered Address:", b.address[0])
    return w.bytes()


def itr_pdf(b: Business, name: str | None = None, pan: str | None = None, ay: str = "2025-26",
            turnover: Decimal | None = None) -> bytes:
    t = turnover if turnover is not None else b.revenue
    w = PdfWriter()
    w.text(130, "INDIAN INCOME TAX RETURN ACKNOWLEDGEMENT", 12, bold=True)
    w.line(30)
    w.kv("Assessment Year:", ay)
    w.kv("Form Number:", "ITR-6")
    w.kv("PAN:", pan or b.pan)
    w.kv("Name:", name or b.legal_name)
    w.kv("Acknowledgement Number:", "482931760251025")
    w.kv("Date of filing:", "25/10/2025")
    w.line(10)
    w.text(50, "Computation of total income and tax", 10, bold=True)
    w.line(20)
    rows = [
        ("Gross Turnover or Gross Receipts", t),
        ("Profits and gains of business or profession", Decimal("905000")),
        ("Gross Total Income", Decimal("905000")),
        ("Total Income", Decimal("905000")),
        ("Total Taxes Paid", Decimal("228000")),
    ]
    for label, v in rows:
        w.text(50, label, 10)
        w.rtext(540, inr(v, decimals=False), 10)
        w.line()
    return w.bytes()


# --------------------------------------------------------------------------- financial statements
PL_DEFAULT = [
    ("Revenue from Operations", "1", 6820000, 5910000),
    ("Other Income", "2", 45000, 38000),
    ("Total Revenue", "", 6865000, 5948000),
    ("EXPENSES", None, None, None),
    ("Cost of Materials Consumed", "3", 4092000, 3664200),
    ("Employee Benefit Expenses", "4", 820000, 735000),
    ("Other Expenses", "5", 610000, 545000),
    ("Gross Profit", "", 2728000, 2245800),
    ("EBITDA", "", 1343000, 1003800),
    ("Finance Costs", "6", 185000, 162000),
    ("Depreciation and Amortisation Expense", "7", 253000, 231000),
    ("Profit before Tax", "", 905000, 610800),
    ("Tax Expense", "", 228000, 153700),
    ("Profit after Tax", "", 677000, 457100),
]


def profit_loss_pdf(b: Business, items=None, fy_end: str = "31st March 2025") -> bytes:
    items = items or PL_DEFAULT
    w = PdfWriter()
    w.text(150, b.legal_name, 11, bold=True)
    w.line(18)
    w.text(130, f"Statement of Profit and Loss for the year ended {fy_end}", 10)
    w.line(28)
    w.text(50, "Particulars", 10, bold=True)
    w.text(300, "Note", 10, bold=True)
    w.rtext(450, "FY 2024-25", 10)
    w.rtext(550, "FY 2023-24", 10)
    w.line(20)
    for label, note, cur, prev in items:
        if cur is None:
            w.text(50, label, 10, bold=True)
        else:
            w.text(50, label, 10)
            if note:
                w.text(300, note, 10)
            w.rtext(450, inr(cur), 10)
            w.rtext(550, inr(prev), 10)
        w.line()
    w.line(10)
    w.text(50, "As per our report of even date. For and on behalf of the Board.", 8)
    return w.bytes()


BS_DEFAULT = [
    ("EQUITY AND LIABILITIES", None, None),
    ("Share Capital", 1000000, 1000000),
    ("Reserves and Surplus", 2145000, 1468000),
    ("Long-term Borrowings", 1200000, 1450000),
    ("Short-term Borrowings", 600000, 520000),
    ("Trade Payables", 845000, 760000),
    ("Other Current Liabilities", 310000, 285000),
    ("Total Equity and Liabilities", 6100000, 5483000),
    ("ASSETS", None, None),
    ("Property, Plant and Equipment", 2650000, 2480000),
    ("Inventories", 1180000, 1010000),
    ("Trade Receivables", 1395000, 1220000),
    ("Cash in Hand", 42000, 38000),
    ("Balances with Banks", 543750, 455000),
    ("Other Current Assets", 289250, 280000),
    ("Total Assets", 6100000, 5483000),
]


def balance_sheet_pdf(b: Business, items=None) -> bytes:
    items = items or BS_DEFAULT
    w = PdfWriter()
    w.text(150, b.legal_name, 11, bold=True)
    w.line(18)
    w.text(190, "Balance Sheet as at 31st March 2025", 10)
    w.line(28)
    w.text(50, "Particulars", 10, bold=True)
    w.rtext(450, "31.03.2025", 10)
    w.rtext(550, "31.03.2024", 10)
    w.line(20)
    for label, cur, prev in items:
        if cur is None:
            w.text(50, label, 10, bold=True)
        else:
            w.text(50, label, 10)
            w.rtext(450, inr(cur), 10)
            w.rtext(550, inr(prev), 10)
        w.line()
    return w.bytes()


# --------------------------------------------------------------------------- bank statement
@dataclass
class Txn:
    date: date
    narration: str
    ref: str
    debit: Decimal | None
    credit: Decimal | None
    balance: Decimal | None = None
    balance_text: str | None = None  # override printed balance (to inject errors)
    date_text: str | None = None  # override printed date
    extra_line: str | None = None  # wrapped narration on the next line


@dataclass
class BankStatementSpec:
    holder: str
    account_number: str
    ifsc: str
    bank_name: str
    start: date
    end: date
    opening: Decimal
    txns: list[Txn] = field(default_factory=list)

    @property
    def closing(self) -> Decimal:
        bal = self.opening
        for t in self.txns:
            bal = bal + (t.credit or 0) - (t.debit or 0)
        return bal


def generate_transactions(start: date, end: date, opening: Decimal, seed: int = 7,
                          monthly_sales: int = 520000) -> list[Txn]:
    rng = random.Random(seed)
    txns: list[Txn] = []
    bal = opening
    d = start
    month = 0
    customers = ["SHAKTHI PUMPS LTD", "ROOTS AUTO PARTS", "ELGI EQUIPMENTS", "TEXMO INDUSTRIES"]
    vendors = ["SAIL STEEL DEPOT", "KOVAI METALS", "TNEB POWER BILL", "SALARY BATCH"]
    while d <= end:
        days = [3, 9, 15, 21, 27]
        for i, day in enumerate(days):
            try:
                td = date(d.year, d.month, day)
            except ValueError:
                continue
            if td < start or td > end:
                continue
            if i % 2 == 0:
                amt = Decimal(rng.randint(int(monthly_sales * 0.25), int(monthly_sales * 0.45)))
                bal += amt
                txns.append(Txn(td, f"NEFT CR {customers[(month + i) % 4]}", f"N{rng.randint(10**8, 10**9 - 1)}",
                                None, amt, bal))
            else:
                amt = Decimal(rng.randint(int(monthly_sales * 0.45), int(monthly_sales * 0.65)))
                bal -= amt
                txns.append(Txn(td, f"RTGS DR {vendors[(month + i) % 4]}", f"R{rng.randint(10**8, 10**9 - 1)}",
                                amt, None, bal))
        month += 1
        d = (date(d.year, d.month, 28) + timedelta(days=4)).replace(day=1)
    return txns


def bank_statement_pdf(spec: BankStatementSpec, rows_per_page: int = 22, ruled: bool = False) -> bytes:
    w = PdfWriter()
    COLS = {"date": 40, "narration": 105, "ref": 250, "wd_r": 400, "dep_r": 480, "bal_r": 565}

    def header_block() -> None:
        w.text(40, spec.bank_name, 13, bold=True)
        w.line(18)
        w.text(40, "Statement of Account", 11, bold=True)
        w.line(22)
        w.kv("Account Holder Name:", spec.holder, 40, 170, 9)
        w.kv("Account Number:", spec.account_number, 40, 170, 9)
        w.kv("IFSC Code:", spec.ifsc, 40, 170, 9)
        w.kv("Statement Period:", f"From {spec.start:%d/%m/%Y} to {spec.end:%d/%m/%Y}", 40, 170, 9)
        w.line(8)

    def table_header() -> None:
        w.text(COLS["date"], "Date", 9, bold=True)
        w.text(COLS["narration"], "Narration", 9, bold=True)
        w.text(COLS["ref"], "Chq./Ref.No.", 9, bold=True)
        w.rtext(COLS["wd_r"], "Withdrawal Amt.", 9)
        w.rtext(COLS["dep_r"], "Deposit Amt.", 9)
        w.rtext(COLS["bal_r"], "Closing Balance", 9)
        if ruled:
            w.hrule()
        w.line(16)

    header_block()
    table_header()
    w.text(COLS["date"], f"{spec.start:%d/%m/%Y}", 9)
    w.text(COLS["narration"], "Opening Balance", 9)
    w.rtext(COLS["bal_r"], inr(spec.opening), 9)
    w.line(14)
    count = 1
    for t in spec.txns:
        if count >= rows_per_page:
            w.text(40, f"Page {w.doc.page_count}", 8, y=820)
            w.new_page()
            w.text(40, f"{spec.bank_name} - Statement of Account (continued)", 9)
            w.line(24)
            count = 0
        w.text(COLS["date"], t.date_text or f"{t.date:%d/%m/%Y}", 9)
        w.text(COLS["narration"], t.narration[:30], 9)
        w.text(COLS["ref"], t.ref, 9)
        if t.debit is not None:
            w.rtext(COLS["wd_r"], inr(t.debit), 9)
        if t.credit is not None:
            w.rtext(COLS["dep_r"], inr(t.credit), 9)
        bal_text = t.balance_text if t.balance_text is not None else (inr(t.balance) if t.balance is not None else "")
        if bal_text:
            w.rtext(COLS["bal_r"], bal_text, 9)
        w.line(14)
        count += 1
        if t.extra_line:
            w.text(COLS["narration"], t.extra_line, 9)
            w.line(14)
            count += 1
    w.line(6)
    w.text(COLS["narration"], "Closing Balance", 9)
    w.rtext(COLS["bal_r"], inr(spec.closing), 9)
    w.line(24)
    w.text(40, "This is a computer generated statement and does not require a signature.", 7)
    return w.bytes()


def default_bank_spec(b: Business, holder: str | None = None) -> BankStatementSpec:
    start, end = date(2024, 4, 1), date(2025, 3, 31)
    opening = Decimal("455000.00")
    txns = generate_transactions(start, end, opening)
    spec = BankStatementSpec(holder or "SRI LAKSHMI PRECISION COMPONENTS PVT LTD", b.account_number, b.ifsc,
                             b.bank_name, start, end, opening, txns)
    # Final sweep so the statement closes at the balance-sheet bank balance (31.03.2025).
    target = Decimal("543750.00")
    gap = target - spec.closing
    if gap:
        spec.txns.append(Txn(end, "SWEEP TRANSFER FD A/C", "S000000317",
                             -gap if gap < 0 else None, gap if gap > 0 else None, target))
    return spec


# --------------------------------------------------------------------------- degraded inputs
def scanned_pdf_from(pdf_bytes: bytes, dpi: int = 110, blur: bool = False) -> bytes:
    """Rasterise every page and rebuild an image-only PDF (no text layer)."""
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = fitz.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi)
        png = pix.tobytes("png")
        if blur:
            png = blurred_png(png)
        p = out.new_page(width=page.rect.width, height=page.rect.height)
        p.insert_image(p.rect, stream=png)
    return out.tobytes()


def blurred_png(png: bytes, k: int = 9) -> bytes:
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    img = cv2.GaussianBlur(img, (k, k), 0)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def page_png(pdf_bytes: bytes, dpi: int = 150) -> bytes:
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    return src[0].get_pixmap(dpi=dpi).tobytes("png")


def password_protected_pdf(pdf_bytes: bytes, password: str = "secret") -> bytes:
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    return src.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password + "-owner")


def corrupted_pdf(pdf_bytes: bytes) -> bytes:
    return b"%PDF-1.7\n" + bytes(random.Random(1).randrange(256) for _ in range(4000))


def unknown_document_pdf() -> bytes:
    w = PdfWriter()
    w.text(50, "Minutes of the weekly production meeting", 12, bold=True)
    w.line(20)
    w.text(50, "Attendees discussed shift timings and canteen arrangements.", 10)
    return w.bytes()
