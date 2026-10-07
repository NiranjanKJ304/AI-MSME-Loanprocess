"""Test configuration: isolated DB + temp storage, no OCR, no LLM.

By default tests run on a throwaway SQLite file. To run them against PostgreSQL, point
TEST_DATABASE_URL at a *dedicated, disposable* database (every test drops and recreates
all tables), e.g.
    TEST_DATABASE_URL=postgresql+psycopg://postgres:<pw>@localhost:5432/msme_loans_test pytest
Never point it at the application database.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="msme_tests_"))
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["STORAGE_DIR"] = str(_TMP / "storage")
os.environ["OCR_ENGINE"] = "none"
os.environ["LLM_PROVIDER"] = "none"
os.environ["REQUIREMENTS_POLICY_FILE"] = ""
os.environ["RECONCILIATION_THRESHOLDS_FILE"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import database  # noqa: E402
from app.database import Base  # noqa: E402
from app.document_ai.ocr import OCREngine, OCRResult, OCRWord, set_ocr_engine  # noqa: E402
from app.ingestion.storage import LocalStorage, set_storage  # noqa: E402
from app.llm import set_llm_provider  # noqa: E402
import app.models  # noqa: E402,F401
from scripts import synthetic_docs as S  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_state(tmp_path):
    Base.metadata.drop_all(database.engine)
    Base.metadata.create_all(database.engine)
    set_storage(LocalStorage(tmp_path / "storage"))
    set_ocr_engine(None)
    set_llm_provider(None)
    yield
    set_ocr_engine(None)
    set_llm_provider(None)


@pytest.fixture
def db():
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def business() -> S.Business:
    return S.Business()


@pytest.fixture
def write(tmp_path):
    """write(name, bytes) -> Path"""

    def _w(name: str, data: bytes) -> Path:
        p = tmp_path / name
        p.write_bytes(data)
        return p

    return _w


class FakeOCREngine(OCREngine):
    """Deterministic OCR stand-in: returns the words of a reference text PDF, scaled to the
    rendered page's pixel grid (so bounding boxes behave like real OCR output)."""

    name = "fake"

    def __init__(self, reference_pdf: bytes, dpi: int = 300, conf: float = 88.0):
        import pymupdf as fitz

        self.pages = []
        with fitz.open(stream=reference_pdf, filetype="pdf") as doc:
            for page in doc:
                self.pages.append([(w[4], w[:4]) for w in page.get_text("words")])
        self.zoom = dpi / 72.0
        self.conf = conf
        self.calls = 0

    def available(self) -> bool:
        return True

    def recognize(self, image) -> OCRResult:
        words = self.pages[min(self.calls, len(self.pages) - 1)]
        self.calls += 1
        z = self.zoom
        ocr_words = [OCRWord(t, (b[0] * z, b[1] * z, b[2] * z, b[3] * z), self.conf) for t, b in words]
        return OCRResult(" ".join(t for t, _ in words), ocr_words, self.conf, self.name)


@pytest.fixture
def fake_ocr():
    def _install(reference_pdf: bytes, conf: float = 88.0) -> FakeOCREngine:
        engine = FakeOCREngine(reference_pdf, conf=conf)
        set_ocr_engine(engine)
        return engine

    return _install


def create_app(client, applicant_type="PRIVATE_LIMITED", loan_type="TERM_LOAN", amount="2500000",
               name="Sri Lakshmi Precision Components Pvt Ltd"):
    r = client.post("/api/applications", json={
        "business_name": name, "applicant_type": applicant_type, "loan_type": loan_type,
        "requested_amount": amount, "loan_purpose": "CNC machine purchase and working capital",
    })
    assert r.status_code == 201, r.text
    return r.json()


def upload(client, app_id, files: dict[str, bytes], auto_process=True):
    payload = [("files", (name, data, "application/pdf" if name.endswith(".pdf") else "application/octet-stream"))
               for name, data in files.items()]
    r = client.post(f"/api/applications/{app_id}/documents", files=payload,
                    params={"auto_process": str(auto_process).lower()})
    assert r.status_code == 201, r.text
    return r.json()


def sample_documents(b: S.Business) -> dict[str, bytes]:
    return {
        "PAN_card.pdf": S.pan_card_pdf(b.pan, b.legal_name),
        "GST_REG06.pdf": S.gst_certificate_pdf(b),
        "GSTR9_FY2024-25.pdf": S.gst_return_pdf(b),
        "Udyam_certificate.pdf": S.udyam_pdf(b),
        "Certificate_of_Incorporation.pdf": S.incorporation_certificate_pdf(b),
        "ITR_AY2025-26.pdf": S.itr_pdf(b),
        "PL_FY2024-25.pdf": S.profit_loss_pdf(b),
        "Balance_Sheet_FY2024-25.pdf": S.balance_sheet_pdf(b),
        "Bank_statement_FY2024-25.pdf": S.bank_statement_pdf(S.default_bank_spec(b)),
    }
