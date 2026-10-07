"""Layout variants that do NOT match the default synthetic templates.

They imitate the variety seen in real Indian MSME documents: different bank column vocabularies,
two-line headers, headers repeated on every page, ruled grids with wrapped cells, single
"Amount" + Dr/Cr columns, T-format proprietor P&L, amounts in lakhs, ITR-V style rows with
unformatted numbers, mixed text/scanned PDFs and CamScanner-style image pages.
All entities and numbers are fictitious.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pymupdf as fitz

from app.document_ai.pdf_parser import build_lines
from app.document_ai.types import ParsedDocument, ParsedPage, Word
from app.models.enums import TextSource
from scripts.synthetic_docs import PdfWriter, inr, make_gstin

FONT = "helv"


# --------------------------------------------------------------------------- in-memory pages
def page_from_rows(rows: list[tuple[float, list[tuple[float, str]]]], page_number: int = 1, size: float = 10,
                   width: float = 595, height: float = 842, source: TextSource = TextSource.TEXT_LAYER,
                   conf: float | None = None) -> ParsedPage:
    """Build a ParsedPage with exact word boxes: rows = [(y, [(x, "text"), ...]), ...]."""
    cw = 0.5 * size
    words: list[Word] = []
    for y, items in rows:
        for x, text in items:
            cx = x
            for tok in text.split(" "):
                if tok:
                    words.append(Word(tok, cx, y, cx + cw * len(tok), y + size, conf))
                cx += cw * (len(tok) + 1)
    page = ParsedPage(page_number=page_number, width=width, height=height, text_source=source,
                      raw_text="\n".join(" ".join(t for _, t in items) for _, items in rows), words=words)
    if source == TextSource.OCR:
        page.ocr_confidence = conf
        page.is_scanned = True
    page.lines = build_lines(words, page_number)
    return page


def doc_from_pages(*pages: ParsedPage) -> ParsedDocument:
    return ParsedDocument(source_kind="pdf", pages=list(pages))


# --------------------------------------------------------------------------- bank statement variant B
def _txns(start: date, n: int, opening: Decimal, seed: int = 3) -> list[dict]:
    import random

    rng = random.Random(seed)
    out, bal, d = [], opening, start
    for i in range(n):
        d = d + timedelta(days=rng.randint(1, 5))
        if i % 3 == 0:
            amt = Decimal(rng.randint(10, 200) * 1000)
            bal -= amt
            out.append({"date": d, "desc": f"NEFT DR VENDOR {i:03d}", "ref": f"{rng.randint(100000, 999999)}",
                        "debit": amt, "credit": None, "balance": bal})
        else:
            amt = Decimal(rng.randint(20, 250) * 1000)
            bal += amt
            out.append({"date": d, "desc": f"IMPS CR CUSTOMER {i:03d}", "ref": f"{rng.randint(100000, 999999)}",
                        "debit": None, "credit": amt, "balance": bal})
    return out


def bank_variant_b(rows_per_page: int = 14, n: int = 34, wrap_every: int = 5) -> tuple[bytes, dict]:
    """Two-line header repeated on every page; 'Txn Date / Value Date / Description / Cheque No. /
    Withdrawal (Dr) / Deposit (Cr) / Balance (INR)'; amounts printed as '₹'-less Indian groups
    without decimals and with decimals mixed; wrapped narrations; no bank name printed (IFSC only)."""
    opening = Decimal("250000")
    txns = _txns(date(2024, 4, 1), n, opening)
    for k, t in enumerate(txns):
        if k % wrap_every == 2:
            t["wrap"] = f"INV-{1000 + k} PART SETTLEMENT"
    w = PdfWriter()
    X = {"txn": 40, "val": 98, "desc": 156, "chq": 318, "wd_r": 430, "dep_r": 500, "bal_r": 570}

    def page_header(first: bool) -> None:
        if first:
            w.text(40, "Customer Name : M/S SUNRISE TEXTILES", 9)
            w.line(13)
            w.text(40, "A/C No. : 00451020003377", 9)
            w.text(330, "IFS Code : HDFC0001234", 9)
            w.line(13)
            w.text(40, "Statement From : 01-04-2024 To : 31-03-2025", 9)
            w.line(20)
        else:
            w.text(40, f"Statement of account - page {w.doc.page_count}", 8)
            w.line(18)
        w.text(X["txn"], "Txn", 8, bold=True)
        w.text(X["val"], "Value", 8, bold=True)
        w.text(X["desc"], "Description", 8, bold=True)
        w.text(X["chq"], "Cheque", 8, bold=True)
        w.rtext(X["wd_r"], "Withdrawal", 8)
        w.rtext(X["dep_r"], "Deposit", 8)
        w.rtext(X["bal_r"], "Balance", 8)
        w.line(10)
        w.text(X["txn"], "Date", 8, bold=True)
        w.text(X["val"], "Date", 8, bold=True)
        w.text(X["chq"], "No.", 8, bold=True)
        w.rtext(X["wd_r"], "(Dr)", 8)
        w.rtext(X["dep_r"], "(Cr)", 8)
        w.rtext(X["bal_r"], "(INR)", 8)
        w.line(16)

    page_header(True)
    w.text(X["txn"], "01-04-2024", 8)
    w.text(X["desc"], "BALANCE B/F", 8)
    w.rtext(X["bal_r"], inr(opening), 8)
    w.line(13)
    count = 1
    for k, t in enumerate(txns):
        if count >= rows_per_page:
            w.new_page()
            page_header(False)
            count = 0
        ds = t["date"].strftime("%d-%m-%Y")
        w.text(X["txn"], ds, 8)
        w.text(X["val"], ds, 8)
        w.text(X["desc"], t["desc"], 8)
        w.text(X["chq"], t["ref"], 8)
        fmt = (lambda v: inr(v)) if k % 2 else (lambda v: inr(v, decimals=False))
        if t["debit"] is not None:
            w.rtext(X["wd_r"], fmt(t["debit"]), 8)
        if t["credit"] is not None:
            w.rtext(X["dep_r"], fmt(t["credit"]), 8)
        w.rtext(X["bal_r"], inr(t["balance"]), 8)
        w.line(13)
        count += 1
        if t.get("wrap"):
            w.text(X["desc"], t["wrap"], 8)
            w.line(13)
            count += 1
    expected = {"opening": opening, "closing": txns[-1]["balance"], "txns": txns,
                "pages": w.doc.page_count, "account": "00451020003377", "ifsc": "HDFC0001234"}
    return w.bytes(), expected


# --------------------------------------------------------------------------- bank statement variant C (ruled)
def bank_variant_c() -> tuple[bytes, dict]:
    """Ruled grid (pdfplumber path): 'S.No | Tran Date | Particulars | Amount | Balance', amounts
    with Dr/Cr suffix, balances with Cr, narration wrapped INSIDE a cell, header repeated on page 2."""
    cols = [40, 75, 150, 345, 455, 560]
    heads = ["S.No", "Tran Date", "Particulars", "Amount", "Balance"]
    opening = Decimal("455000")
    entries = [
        ("02/04/2024", ["NEFT CR SHAKTHI PUMPS"], Decimal("172445"), "Cr"),
        ("05/04/2024", ["RTGS DR KOVAI METALS", "INV 2024/118 PART PAY"], Decimal("285750"), "Dr"),
        ("09/04/2024", ["UPI CR 4321 ELGI EQUIP"], Decimal("12500"), "Cr"),
        ("12/04/2024", ["CHQ DR 004512 RENT"], Decimal("45000"), "Dr"),
        ("18/04/2024", ["NEFT CR TEXMO IND"], Decimal("125000"), "Cr"),
        ("22/04/2024", ["ACH DR LOAN EMI", "A/C 7788 APR-24"], Decimal("38250"), "Dr"),
        ("25/04/2024", ["IMPS CR ROOTS AUTO"], Decimal("98000"), "Cr"),
    ]
    doc = fitz.open()
    expected_rows = []
    bal = opening

    def grid_page(rows_spec):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((40, 50), "Kongu Co-operative Bank - Account Statement", fontsize=11, fontname="hebo")
        pg.insert_text((40, 66), "Account No: 50200031457788    IFSC: KCBL0000212", fontsize=9, fontname=FONT)
        pg.insert_text((40, 80), "Period: 01/04/2024 to 30/04/2024", fontsize=9, fontname=FONT)
        y = 95
        all_rows = [(heads, 18)] + rows_spec
        for cells, h in all_rows:
            for ci in range(5):
                pg.draw_rect(fitz.Rect(cols[ci], y, cols[ci + 1], y + h), width=0.5)
                lines = cells[ci] if isinstance(cells[ci], list) else [cells[ci]]
                for li, txt in enumerate(lines):
                    if txt:
                        pg.insert_text((cols[ci] + 3, y + 11 + li * 10), txt, fontsize=8, fontname=FONT)
            y += h

    page_rows = [[["", "01/04/2024", "Opening Balance", "", f"{inr(opening)} Cr"], 18]]
    for i, (d, narr, amt, side) in enumerate(entries):
        bal = bal + amt if side == "Cr" else bal - amt
        expected_rows.append({"date": d, "debit": amt if side == "Dr" else None,
                              "credit": amt if side == "Cr" else None, "balance": bal, "narr": " ".join(narr)})
        page_rows.append([[str(i + 1), d, narr, f"{inr(amt)} {side}", f"{inr(bal)} Cr"], 10 + 10 * len(narr)])
    grid_page([(r[0], r[1]) for r in page_rows[:5]])
    grid_page([(r[0], r[1]) for r in page_rows[5:]])
    return doc.tobytes(), {"opening": opening, "rows": expected_rows, "closing": bal}


# --------------------------------------------------------------------------- financial statements
def tformat_pl() -> bytes:
    """Proprietor 'Trading and Profit & Loss Account' in T-format (Dr side | Cr side)."""
    w = PdfWriter()
    w.text(150, "M/S SUNRISE TEXTILES (Proprietor: R. Kumar)", 11, bold=True)
    w.line(16)
    w.text(110, "Trading and Profit & Loss Account for the year ended 31st March 2025", 10)
    w.line(24)
    w.text(40, "Particulars", 9, bold=True)
    w.rtext(280, "Amount", 9)
    w.text(310, "Particulars", 9, bold=True)
    w.rtext(560, "Amount", 9)
    w.line(16)
    rows = [
        ("To Opening Stock", 350000, "By Sales", 6820000),
        ("To Purchases", 4092000, "By Closing Stock", 410000),
        ("To Gross Profit c/d", 2788000, None, None),
        ("To Salaries", 820000, "By Gross Profit b/d", 2788000),
        ("To Rent", 240000, "By Other Income", 45000),
        ("To Depreciation", 253000, None, None),
        ("To Interest on Loan", 185000, None, None),
        ("To Net Profit", 1335000, None, None),
    ]
    for l_lab, l_amt, r_lab, r_amt in rows:
        w.text(40, l_lab, 9)
        w.rtext(280, inr(l_amt), 9)
        if r_lab:
            w.text(310, r_lab, 9)
            w.rtext(560, inr(r_amt), 9)
        w.line(14)
    return w.bytes()


def pl_in_lakhs() -> bytes:
    w = PdfWriter()
    w.text(130, "ACME FORGINGS PRIVATE LIMITED", 11, bold=True)
    w.line(16)
    w.text(100, "Statement of Profit and Loss for the year ended 31 March 2025", 10)
    w.line(14)
    w.text(230, "(Rs. in Lakhs)", 9)
    w.line(22)
    w.text(40, "Particulars", 9, bold=True)
    w.text(300, "Note", 9, bold=True)
    w.rtext(450, "2024-25", 9)
    w.rtext(550, "2023-24", 9)
    w.line(16)
    for label, note, cur, prev in [
        ("Revenue from operations", "18", "682.40", "591.10"),
        ("Cost of materials consumed", "19", "409.20", "366.42"),
        ("Gross Profit", "", "273.20", "224.68"),
        ("Finance costs", "20", "18.50", "16.20"),
        ("Depreciation and amortisation expense", "21", "25.30", "23.10"),
        ("Profit before tax", "", "90.50", "61.08"),
        ("Tax expense", "", "22.80", "15.37"),
        ("Profit for the year", "", "67.70", "45.71"),
    ]:
        w.text(40, label, 9)
        if note:
            w.text(300, note, 9)
        w.rtext(450, cur, 9)
        w.rtext(550, prev, 9)
        w.line(14)
    return w.bytes()


def itr_v_unformatted() -> bytes:
    """ITR-V style: row numbers between label and figure, figures without digit grouping."""
    w = PdfWriter()
    w.text(120, "INDIAN INCOME TAX RETURN VERIFICATION FORM (ITR-V)", 11, bold=True)
    w.line(22)
    w.text(40, "Assessment Year", 9)
    w.text(200, "2025-26", 9)
    w.line(14)
    w.text(40, "PAN", 9)
    w.text(200, "AAECS4821K", 9)
    w.text(330, "Form No.", 9)
    w.text(420, "ITR-6", 9)
    w.line(14)
    w.text(40, "Name", 9)
    w.text(200, "SRI LAKSHMI PRECISION COMPONENTS PRIVATE LIMITED", 9)
    w.line(22)
    for label, num, val in [("Gross Total Income", "1", "905000"), ("Total Income", "2", "905000"),
                            ("Total Taxes Paid", "6", "228000")]:
        w.text(40, label, 9)
        w.text(360, num, 9)
        w.rtext(540, val, 9)
        w.line(14)
    return w.bytes()


# --------------------------------------------------------------------------- scanned / mixed
def image_page_pdf(src_pdf: bytes, page_index: int = 0, dpi: int = 110, watermark: str | None = None) -> fitz.Document:
    src = fitz.open(stream=src_pdf, filetype="pdf")
    out = fitz.open()
    pg = src[page_index]
    p = out.new_page(width=pg.rect.width, height=pg.rect.height)
    p.insert_image(p.rect, stream=pg.get_pixmap(dpi=dpi).tobytes("png"))
    if watermark:
        p.insert_text((40, pg.rect.height - 20), watermark, fontsize=7, fontname=FONT)
    return out


def mixed_pdf(text_pdf: bytes, scanned_source_pdf: bytes) -> bytes:
    """Page 1 has a native text layer, page 2 is an image-only scan."""
    out = fitz.open(stream=text_pdf, filetype="pdf")
    scan = image_page_pdf(scanned_source_pdf)
    out.insert_pdf(scan)
    return out.tobytes()


def camscanner_pdf(src_pdf: bytes) -> bytes:
    """Image page plus a thin native text layer (scanner-app watermark)."""
    return image_page_pdf(src_pdf, watermark="Scanned with CamScanner").tobytes()


def gstin_for(pan: str = "AAACS1234A", state: str = "27") -> str:
    return make_gstin(pan, state)
