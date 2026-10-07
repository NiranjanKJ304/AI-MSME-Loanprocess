from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, JSONType, utcnow
from app.models._types import enum_column
from app.models.enums import JobStatus, JobType, ProcessingStage, StageStatus


class ProcessingJob(Base):
    """One pipeline run. Jobs are append-only history; derived data is replaced per run."""

    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=True
    )
    job_type: Mapped[JobType] = mapped_column(enum_column(JobType))
    status: Mapped[JobStatus] = mapped_column(enum_column(JobStatus), default=JobStatus.RUNNING)
    run_number: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    stages: Mapped[list[ProcessingStageRecord]] = relationship(
        back_populates="job",
        order_by="ProcessingStageRecord.sequence",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ProcessingStageRecord(Base):
    __tablename__ = "processing_stages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    stage: Mapped[ProcessingStage] = mapped_column(enum_column(ProcessingStage))
    status: Mapped[StageStatus] = mapped_column(enum_column(StageStatus))
    records_processed: Mapped[int] = mapped_column(Integer, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, default=0)
    warnings_count: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    job: Mapped[ProcessingJob] = relationship(back_populates="stages")
