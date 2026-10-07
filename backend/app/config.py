"""Application settings, loaded from environment variables / backend/.env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI-MSME Loan Decision Intelligence Platform (Prototype)"
    # Local PostgreSQL install; set the real credentials in backend/.env
    database_url: str = "postgresql+psycopg://postgres@localhost:5432/msme_loans"
    storage_dir: Path = BACKEND_DIR.parent / "storage"
    cors_origins: str = "http://localhost:3000"

    # File validation
    max_file_size_mb: float = 25
    max_pdf_pages: int = 300
    min_image_dimension_px: int = 300
    allowed_extensions: str = ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.xlsx,.csv"

    # Confidence thresholds
    classification_confidence_threshold: float = 0.60
    field_confidence_threshold: float = 0.70

    # Bank statement validation
    balance_tolerance: float = 1.00

    # Reconciliation
    name_match_threshold: float = 0.85
    reconciliation_thresholds_file: str = ""

    # Canonical financial layer: facts of the same metric+period differing by more than this
    # relative amount (or INR 1) are recorded as conflicts
    fact_conflict_tolerance: float = 0.005

    # Transaction intelligence: JSON rule file replacing the default Indian-MSME rules
    txn_rules_file: str = ""

    # Financial health: JSON file replacing the default descriptive-indicator thresholds
    health_thresholds_file: str = ""

    # Forecasting: JSON file replacing the default history / backtest / interval settings
    forecast_config_file: str = ""

    # Repayment capacity: JSON file replacing the default history / stress / descriptive thresholds
    repayment_config_file: str = ""

    # Requirements policy
    requirements_policy_file: str = ""

    # OCR
    ocr_engine: str = "none"
    tesseract_cmd: str = ""
    ocr_languages: str = "eng"
    ocr_dpi: int = 300

    # LLM
    llm_provider: str = "none"
    llm_model: str = "claude-opus-5-5"
    llm_api_key: str = ""
    llm_timeout_seconds: float = 120
    llm_max_input_chars: int = 12000

    # Pipeline: a job still RUNNING after this many minutes is treated as stale
    stale_job_minutes: int = 30

    extraction_version: str = Field(default="0.1.0", description="Bumped when extractor logic changes")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_extension_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_extensions.split(",") if e.strip()}

    @property
    def max_file_size_bytes(self) -> int:
        return int(self.max_file_size_mb * 1024 * 1024)


@lru_cache
def get_settings() -> Settings:
    return Settings()
