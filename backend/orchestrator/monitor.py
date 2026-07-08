from pydantic import BaseModel
from typing import List, Optional
import time

class PipelineStatus(BaseModel):
    current_stage: str
    completed_stages: List[str]
    running_stage: Optional[str]
    failed_stage: Optional[str]
    retry_count: int
    execution_time_ms: float
    pipeline_health: str # Healthy, Degraded, Failed

class PipelineMonitor:
    def __init__(self):
        self.status = PipelineStatus(
            current_stage="Initialized",
            completed_stages=[],
            running_stage=None,
            failed_stage=None,
            retry_count=0,
            execution_time_ms=0.0,
            pipeline_health="Healthy"
        )
        self._start_time = None

    def start_pipeline(self):
        self._start_time = time.time()
        self.status.current_stage = "Starting"
        self.status.pipeline_health = "Healthy"

    def start_stage(self, stage_name: str):
        self.status.current_stage = stage_name
        self.status.running_stage = stage_name

    def complete_stage(self, stage_name: str):
        self.status.completed_stages.append(stage_name)
        self.status.running_stage = None

    def fail_stage(self, stage_name: str, retry: bool = False):
        self.status.failed_stage = stage_name
        self.status.running_stage = None
        self.status.pipeline_health = "Degraded" if retry else "Failed"
        if retry:
            self.status.retry_count += 1

    def finish_pipeline(self):
        if self._start_time:
            self.status.execution_time_ms = (time.time() - self._start_time) * 1000
        self.status.current_stage = "Completed" if self.status.pipeline_health != "Failed" else "Failed"

    def get_status(self) -> PipelineStatus:
        return self.status
