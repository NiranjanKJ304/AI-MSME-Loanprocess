"""Records every pipeline stage as a ProcessingStageRecord (+ audit entry).

Stages run through `JobRecorder.run_stage`, which guarantees that:
  * a RUNNING record is committed before work starts (visible to status polling),
  * any exception is caught, the transaction rolled back, and the stage marked FAILED
    with the error text - failures are never swallowed silently,
  * counts (records processed/failed) and warnings are persisted.
"""

from __future__ import annotations

import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.database import utcnow
from app.models import ProcessingJob, ProcessingStageRecord
from app.models.enums import JobStatus, JobType, ProcessingStage, StageStatus
from app.utils.logging import audit, get_logger

log = get_logger("pipeline")


@dataclass
class StageOutcome:
    records_processed: int = 0
    records_failed: int = 0
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    # A stage may complete but declare a soft failure (e.g. file invalid) without raising.
    failed: bool = False
    error: str | None = None

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


class StageFailed(Exception):
    """Raised by run_stage to stop the pipeline after a FAILED stage."""


class JobRecorder:
    def __init__(
        self,
        db: Session,
        *,
        application_id: uuid.UUID,
        document_id: uuid.UUID | None,
        job_type: JobType,
        run_number: int,
    ):
        self.db = db
        self.application_id = application_id
        self.document_id = document_id
        self._seq = 0
        self.job = ProcessingJob(
            application_id=application_id,
            document_id=document_id,
            job_type=job_type,
            run_number=run_number,
            status=JobStatus.RUNNING,
        )
        db.add(self.job)
        db.commit()
        self.job_id = self.job.id
        self.had_warnings = False
        self.had_failure = False

    def run_stage(
        self, stage: ProcessingStage, fn: Callable[[StageOutcome], Any]
    ) -> tuple[StageOutcome, Any]:
        self._seq += 1
        rec = ProcessingStageRecord(
            job_id=self.job_id,
            document_id=self.document_id,
            sequence=self._seq,
            stage=stage,
            status=StageStatus.RUNNING,
            started_at=utcnow(),
        )
        self.db.add(rec)
        self.db.commit()
        rec_id = rec.id

        outcome = StageOutcome()
        result: Any = None
        try:
            result = fn(outcome)
        except Exception as exc:  # noqa: BLE001 - every failure is recorded, then re-raised
            self.db.rollback()
            tb = traceback.format_exc(limit=8)
            log.exception(
                "stage failed",
                extra={"ctx": {"stage": stage.value, "document_id": str(self.document_id)}},
            )
            rec = self.db.get(ProcessingStageRecord, rec_id)
            assert rec is not None
            rec.status = StageStatus.FAILED
            rec.error = f"{type(exc).__name__}: {exc}"
            rec.records_processed = outcome.records_processed
            rec.records_failed = outcome.records_failed
            rec.warnings = outcome.warnings or None
            rec.warnings_count = len(outcome.warnings)
            rec.details = {**outcome.details, "traceback": tb}
            rec.finished_at = utcnow()
            audit(
                self.db,
                action="STAGE_FAILED",
                status="FAILED",
                application_id=self.application_id,
                document_id=self.document_id,
                stage=stage.value,
                error=rec.error,
            )
            self.db.commit()
            self.had_failure = True
            raise StageFailed(rec.error) from exc

        rec = self.db.get(ProcessingStageRecord, rec_id)
        assert rec is not None
        if outcome.failed:
            status = StageStatus.FAILED
            self.had_failure = True
        elif outcome.warnings or outcome.records_failed:
            status = StageStatus.COMPLETED_WITH_WARNINGS
            self.had_warnings = True
        else:
            status = StageStatus.COMPLETED
        rec.status = status
        rec.error = outcome.error
        rec.records_processed = outcome.records_processed
        rec.records_failed = outcome.records_failed
        rec.warnings = outcome.warnings or None
        rec.warnings_count = len(outcome.warnings)
        rec.details = outcome.details or None
        rec.finished_at = utcnow()
        audit(
            self.db,
            action=f"STAGE_{status.value}",
            status=status.value,
            application_id=self.application_id,
            document_id=self.document_id,
            stage=stage.value,
            error=outcome.error,
            details={
                "records_processed": outcome.records_processed,
                "records_failed": outcome.records_failed,
                "warnings": len(outcome.warnings),
            },
        )
        self.db.commit()
        if outcome.failed:
            raise StageFailed(outcome.error or f"{stage.value} failed")
        return outcome, result

    def skip_stage(self, stage: ProcessingStage, reason: str) -> None:
        self._seq += 1
        now = utcnow()
        self.db.add(
            ProcessingStageRecord(
                job_id=self.job_id,
                document_id=self.document_id,
                sequence=self._seq,
                stage=stage,
                status=StageStatus.SKIPPED,
                details={"reason": reason},
                started_at=now,
                finished_at=now,
            )
        )
        self.db.commit()

    def finish(self, error: str | None = None) -> ProcessingJob:
        job = self.db.get(ProcessingJob, self.job_id)
        assert job is not None
        if self.had_failure or error:
            job.status = JobStatus.FAILED
        elif self.had_warnings:
            job.status = JobStatus.COMPLETED_WITH_WARNINGS
        else:
            job.status = JobStatus.COMPLETED
        job.error = error
        job.finished_at = utcnow()
        self.db.commit()
        return job
