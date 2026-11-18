"""Single-event AI review application service.

This layer composes model execution and immutable persistence. It deliberately
accepts prepared evidence and an already configured provider: data acquisition,
credential management, scheduling and downstream scoring remain separate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .ai_model_provider import ChatModelProvider
from .event_review_board_runner import (
    EventReviewBoardExecutionResult,
    run_event_review_board,
)
from .event_review_models import ReviewEvidence
from .event_review_repository import (
    EventReviewRepository,
    PersistedEventReviewRun,
)


@dataclass(frozen=True)
class EventReviewServiceResult:
    """Execution trace and its immutable persistence receipt."""

    execution: EventReviewBoardExecutionResult
    persisted: PersistedEventReviewRun
    operational_error_code: str = ""
    operational_error_message: str = ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_and_persist_event_review(
    *,
    provider: ChatModelProvider,
    repository: EventReviewRepository,
    reviewer_model: str,
    chief_model: str,
    event_id: str,
    event_payload: dict,
    evidence: tuple[ReviewEvidence, ...],
    clock: Callable[[], datetime] = _utc_now,
    review_run_id: str | None = None,
    reviewer_timeout_seconds: float = 45.0,
    reviewer_max_tokens: int = 2500,
    chief_timeout_seconds: float = 60.0,
    chief_max_tokens: int = 3000,
) -> EventReviewServiceResult:
    """Run one review board and persist its complete or blocked outcome.

    No implicit retries are performed. Callers must explicitly initiate a new
    run (with a new run ID) when retrying, preserving every prior attempt.
    """
    if not isinstance(repository, EventReviewRepository):
        raise TypeError("repository must be an EventReviewRepository")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id must be a non-empty string")
    event_id = event_id.strip()
    if not isinstance(event_payload, dict):
        raise TypeError("event_payload must be a dictionary")
    if not isinstance(evidence, tuple) or any(
        not isinstance(item, ReviewEvidence) for item in evidence
    ):
        raise TypeError("evidence must be a tuple of ReviewEvidence")
    if any(item.event_id != event_id for item in evidence):
        raise ValueError("all evidence event_id values must match event_id")

    started = clock()
    if not isinstance(started, datetime) or started.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime")

    execution = run_event_review_board(
        provider=provider,
        reviewer_model=reviewer_model,
        chief_model=chief_model,
        event_id=event_id,
        event_payload=event_payload,
        evidence=evidence,
        reviewer_timeout_seconds=reviewer_timeout_seconds,
        reviewer_max_tokens=reviewer_max_tokens,
        chief_timeout_seconds=chief_timeout_seconds,
        chief_max_tokens=chief_max_tokens,
    )

    operational_error_code = ""
    operational_error_message = ""
    try:
        completed = clock()
        if not isinstance(completed, datetime) or completed.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
    except Exception as exc:
        # The review has already run. Preserve it using a clearly marked
        # fallback timestamp rather than discarding the completed assessment.
        completed = _utc_now()
        if completed < started:
            completed = started
        operational_error_code = "completion_clock_failed"
        operational_error_message = (
            "completion clock failed; UTC fallback used "
            f"({type(exc).__name__})"
        )
    started_at = started.isoformat()
    completed_at = completed.isoformat()

    persisted = repository.save_run(
        execution=execution,
        event_payload=event_payload,
        started_at=started_at,
        completed_at=completed_at,
        review_run_id=review_run_id,
        operational_error_code=operational_error_code,
        operational_error_message=operational_error_message,
    )
    return EventReviewServiceResult(
        execution=execution,
        persisted=persisted,
        operational_error_code=operational_error_code,
        operational_error_message=operational_error_message,
    )
