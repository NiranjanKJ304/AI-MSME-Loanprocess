"""Pre-extraction file validation.

Every check produces an explicit result; a file is VALID only when no check FAILs.
Nothing about an invalid file is silently ignored - the full report is persisted.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import get_settings

PDF = "application/pdf"
PNG = "image/png"
JPEG = "image/jpeg"
TIFF = "image/tiff"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV = "text/csv"

EXTENSION_MIME = {
    ".pdf": PDF,
    ".png": PNG,
    ".jpg": JPEG,
    ".jpeg": JPEG,
    ".tif": TIFF,
    ".tiff": TIFF,
    ".xlsx": XLSX,
    ".csv": CSV,
}
IMAGE_MIMES = {PNG, JPEG, TIFF}

# Declared MIME types browsers commonly send for these files; anything else => warning only.
GENERIC_DECLARED = {"application/octet-stream", "binary/octet-stream", "", None}


@dataclass
class Check:
    code: str
    status: str  # PASS | FAIL | WARNING
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "status": self.status, "message": self.message}


@dataclass
class FileValidationReport:
    checks: list[Check] = field(default_factory=list)
    detected_mime: str | None = None
    page_count: int | None = None
    is_encrypted: bool = False
    pages_without_text: list[int] = field(default_factory=list)
    image_quality: dict[str, Any] | None = None

    def add(self, code: str, status: str, message: str) -> None:
        self.checks.append(Check(code, status, message))

    @property
    def is_valid(self) -> bool:
        return not any(c.status == "FAIL" for c in self.checks)

    @property
    def errors(self) -> list[Check]:
        return [c for c in self.checks if c.status == "FAIL"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == "WARNING"]

    def error_message(self) -> str | None:
        errs = self.errors
        if not errs:
            return None
        return "; ".join(f"{c.code}: {c.message}" for c in errs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "detected_mime": self.detected_mime,
            "page_count": self.page_count,
            "is_encrypted": self.is_encrypted,
            "pages_without_text": self.pages_without_text,
            "image_quality": self.image_quality,
            "checks": [c.as_dict() for c in self.checks],
        }


def sniff_mime(path: Path) -> str | None:
    with open(path, "rb") as f:
        head = f.read(2048)
    if not head:
        return None
    if b"%PDF-" in head[:1024]:
        return PDF
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return TIFF
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                if any(n.startswith("xl/") for n in z.namelist()):
                    return XLSX
        except zipfile.BadZipFile:
            return None
        return "application/zip"
    if b"\x00" not in head:
        try:
            head.decode("utf-8")
            return CSV
        except UnicodeDecodeError:
            try:
                head.decode("latin-1")
                return CSV
            except UnicodeDecodeError:
                return None
    return None


def validate_file(path: Path, filename: str, declared_mime: str | None = None) -> FileValidationReport:
    settings = get_settings()
    report = FileValidationReport()

    if not path.exists():
        report.add("FILE_EXISTS", "FAIL", "Uploaded file not found in storage")
        return report
    report.add("FILE_EXISTS", "PASS", "File present")

    size = path.stat().st_size
    if size == 0:
        report.add("FILE_SIZE", "FAIL", "File is empty (0 bytes)")
        return report
    if size > settings.max_file_size_bytes:
        report.add(
            "FILE_SIZE",
            "FAIL",
            f"File is {size / 1048576:.1f} MB; limit is {settings.max_file_size_mb} MB",
        )
        return report
    report.add("FILE_SIZE", "PASS", f"{size} bytes")

    ext = Path(filename).suffix.lower()
    if ext not in settings.allowed_extension_set:
        report.add(
            "EXTENSION",
            "FAIL",
            f"Extension '{ext or '(none)'}' not allowed. Allowed: {sorted(settings.allowed_extension_set)}",
        )
        return report
    report.add("EXTENSION", "PASS", ext)

    detected = sniff_mime(path)
    report.detected_mime = detected
    if detected is None or detected not in EXTENSION_MIME.values():
        report.add(
            "MIME_TYPE",
            "FAIL",
            f"File content is not a supported type (detected: {detected or 'unknown/binary'})",
        )
        return report
    expected = EXTENSION_MIME.get(ext)
    if expected != detected:
        report.add(
            "MIME_EXTENSION_MATCH",
            "WARNING",
            f"Extension '{ext}' suggests {expected} but content is {detected}; processing as {detected}",
        )
    else:
        report.add("MIME_EXTENSION_MATCH", "PASS", detected)
    if declared_mime not in GENERIC_DECLARED and declared_mime != detected:
        report.add(
            "DECLARED_MIME",
            "WARNING",
            f"Client declared '{declared_mime}', content is '{detected}'",
        )

    if detected == PDF:
        _validate_pdf(path, report)
    elif detected in IMAGE_MIMES:
        _validate_image(path, detected, report)
    elif detected == XLSX:
        _validate_xlsx(path, report)
    elif detected == CSV:
        _validate_csv(path, report)
    return report


def _validate_pdf(path: Path, report: FileValidationReport) -> None:
    import pymupdf as fitz

    settings = get_settings()
    try:
        doc = fitz.open(path)
    except Exception as exc:  # corrupted / not a PDF
        # The parser's message embeds the server-side storage path, so only the error type is exposed.
        report.add("PDF_READABLE", "FAIL", f"PDF could not be opened - file is corrupted or not a PDF ({type(exc).__name__})")
        return
    with doc:
        if doc.needs_pass:
            report.is_encrypted = True
            if not doc.authenticate(""):
                report.add(
                    "PDF_PASSWORD",
                    "FAIL",
                    "PDF is password-protected. Ask the applicant for an unprotected copy.",
                )
                return
            report.add("PDF_PASSWORD", "WARNING", "PDF is encrypted but opens without a user password")
        elif doc.is_encrypted:
            report.is_encrypted = True
            report.add("PDF_PASSWORD", "WARNING", "PDF has owner-password restrictions")
        else:
            report.add("PDF_PASSWORD", "PASS", "Not password-protected")

        if getattr(doc, "is_repaired", False):
            report.add(
                "PDF_STRUCTURE",
                "WARNING",
                "PDF structure was damaged and has been repaired on open; verify content",
            )
        report.page_count = doc.page_count
        if doc.page_count == 0:
            report.add("PDF_PAGE_COUNT", "FAIL", "PDF has no pages")
            return
        if doc.page_count > settings.max_pdf_pages:
            report.add(
                "PDF_PAGE_COUNT",
                "FAIL",
                f"PDF has {doc.page_count} pages; limit is {settings.max_pdf_pages}",
            )
            return
        report.add("PDF_PAGE_COUNT", "PASS", f"{doc.page_count} page(s)")

        unreadable: list[int] = []
        for i in range(doc.page_count):
            try:
                page = doc.load_page(i)
                text = page.get_text("text")
                if len(text.strip()) < 20:
                    report.pages_without_text.append(i + 1)
            except Exception:
                unreadable.append(i + 1)
        if unreadable:
            report.add(
                "PDF_PAGES_READABLE",
                "FAIL",
                f"Pages could not be read (corrupted): {unreadable}",
            )
            return
        report.add("PDF_PAGES_READABLE", "PASS", "All pages readable")
        if report.pages_without_text:
            report.add(
                "PDF_TEXT_LAYER",
                "WARNING",
                f"Pages without a text layer (scanned - OCR required): {report.pages_without_text}",
            )
        else:
            report.add("PDF_TEXT_LAYER", "PASS", "Text layer present on all pages")

    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            _ = len(pdf.pages)
    except Exception as exc:
        report.add("PDF_TABLE_PARSER", "WARNING", f"Table parser could not open PDF: {exc}")


def _validate_image(path: Path, mime: str, report: FileValidationReport) -> None:
    import cv2
    import numpy as np

    from app.document_ai.image_quality import assess_image_quality, decode_image_file

    settings = get_settings()
    frames = 1
    img = decode_image_file(str(path))
    if mime == TIFF:
        ok, mats = cv2.imreadmulti(str(path))
        if ok and mats:
            frames = len(mats)
            img = img if img is not None else mats[0]
        if img is None:
            # cv2 cannot read paths with non-ASCII chars on Windows; fall back to buffer decode
            data = np.fromfile(str(path), dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        report.add("IMAGE_READABLE", "FAIL", "Image could not be decoded (corrupted or unsupported)")
        return
    report.page_count = frames
    report.add("IMAGE_READABLE", "PASS", f"{img.shape[1]}x{img.shape[0]} px")
    h, w = img.shape[:2]
    if min(h, w) < settings.min_image_dimension_px:
        report.add(
            "IMAGE_RESOLUTION",
            "FAIL",
            f"Image is {w}x{h}px; minimum dimension is {settings.min_image_dimension_px}px",
        )
        return
    quality = assess_image_quality(img)
    report.image_quality = quality
    if quality["issues"]:
        report.add(
            "IMAGE_QUALITY",
            "WARNING",
            f"Image quality issues: {', '.join(quality['issues'])} (score {quality['quality_score']})",
        )
    else:
        report.add("IMAGE_QUALITY", "PASS", f"quality score {quality['quality_score']}")
    report.pages_without_text = list(range(1, frames + 1))


def _validate_xlsx(path: Path, report: FileValidationReport) -> None:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        report.add("XLSX_READABLE", "FAIL", f"Workbook could not be opened: {exc}")
        return
    try:
        sheets = wb.sheetnames
        report.page_count = len(sheets)
        if not sheets:
            report.add("XLSX_READABLE", "FAIL", "Workbook has no sheets")
            return
        report.add("XLSX_READABLE", "PASS", f"{len(sheets)} sheet(s)")
    finally:
        wb.close()


def _validate_csv(path: Path, report: FileValidationReport) -> None:
    import pandas as pd

    try:
        df = pd.read_csv(path, nrows=200, dtype=str, encoding_errors="replace")
    except Exception as exc:
        report.add("CSV_READABLE", "FAIL", f"CSV could not be parsed: {exc}")
        return
    if df.shape[1] < 2:
        report.add("CSV_READABLE", "WARNING", "CSV has fewer than 2 columns")
    else:
        report.add("CSV_READABLE", "PASS", f"{df.shape[1]} columns")
    report.page_count = 1
