"""Structured reports for the independent market workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any


VALID_STAGE_STATUSES = {
    "success",
    "partial",
    "skipped",
    "failed",
    "not_started",
}
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class WorkflowStageResult:
    """Result of one independently executed workflow stage."""

    name: str
    status: str = "not_started"
    started_at: str = ""
    completed_at: str = ""
    message: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    error_type: str = ""
    error_message: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("stage name must be a non-empty string")
        if self.status not in VALID_STAGE_STATUSES:
            raise ValueError(f"unsupported stage status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketWorkflowReport:
    """Serializable summary of one complete market workflow execution."""

    run_id: str
    run_type: str = "scheduled"
    status: str = "not_started"
    started_at: str = ""
    completed_at: str = ""
    database: dict[str, Any] = field(default_factory=dict)
    stages: list[WorkflowStageResult] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_stage(self, stage: WorkflowStageResult) -> None:
        if not isinstance(stage, WorkflowStageResult):
            raise TypeError("stage must be a WorkflowStageResult")
        self.stages.append(stage)

    def add_error(
        self,
        *,
        stage: str,
        error: Exception,
    ) -> None:
        self.errors.append({
            "stage": stage,
            "error_type": type(error).__name__,
            "error_message": str(error),
        })


    def finalize(self, *, completed_at: str | None = None) -> None:
        """Derive overall status from stage outcomes.

        Errors are diagnostic records. Recoverable errors must not
        automatically turn a partial workflow into a failed workflow.
        """
        if completed_at is not None:
            self.completed_at = completed_at
        elif not self.completed_at:
            self.completed_at = datetime.now(_SHANGHAI_TZ).isoformat(
                timespec="seconds"
            )

        statuses = [stage.status for stage in self.stages]

        if "failed" in statuses:
            self.status = "failed"
        elif "partial" in statuses:
            self.status = "partial"
        elif not statuses or all(
            status in {"skipped", "not_started"} for status in statuses
        ):
            self.status = "skipped"
        else:
            self.status = "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_type": self.run_type,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "database": self.database,
            "stages": [stage.to_dict() for stage in self.stages],
            "errors": list(self.errors),
            "metadata": dict(self.metadata),
        }


__all__ = [
    "VALID_STAGE_STATUSES",
    "WorkflowStageResult",
    "MarketWorkflowReport",
]
