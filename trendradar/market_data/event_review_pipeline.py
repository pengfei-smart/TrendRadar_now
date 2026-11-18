"""Database-to-review orchestration for one persisted market event.

This pipeline prepares conservative evidence, executes the review board and
persists its immutable audit record. It does not fetch news, configure secrets,
schedule work or invoke opportunity scoring.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .ai_model_provider import ChatModelProvider
from .event_review_input_builder import build_event_review_input
from .event_review_repository import EventReviewRepository
from .event_review_service import EventReviewServiceResult, run_and_persist_event_review
from .storage import MarketStorage
from .event_review_runtime import EventReviewRuntime


def review_persisted_market_event(
    *,
    storage: MarketStorage,
    repository: EventReviewRepository,
    provider: ChatModelProvider,
    reviewer_model: str,
    chief_model: str,
    event_id: str,
    clock: Callable[[], datetime] | None = None,
    review_run_id: str | None = None,
    failure_journal_path: str | Path | None = None,
    max_mapping_evidence: int = 100,
    max_excerpt_chars: int = 4000,
    reviewer_timeout_seconds: float = 45.0,
    reviewer_max_tokens: int = 2500,
    chief_timeout_seconds: float = 60.0,
    chief_max_tokens: int = 3000,
) -> EventReviewServiceResult:
    """Build inputs from SQLite, run the board once, and persist its outcome.

    The repository must target the same MarketStorage instance as the input
    builder, preventing a successful review from being written to a different
    database than the one supplying its event and evidence.
    """
    if not isinstance(storage, MarketStorage):
        raise TypeError("storage must be a MarketStorage instance")
    if not isinstance(repository, EventReviewRepository):
        raise TypeError("repository must be an EventReviewRepository")
    if repository.storage is not storage:
        raise ValueError("repository and input builder must use the same MarketStorage")

    prepared = build_event_review_input(
        storage=storage,
        event_id=event_id,
        max_mapping_evidence=max_mapping_evidence,
        max_excerpt_chars=max_excerpt_chars,
    )
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    return run_and_persist_event_review(
        provider=provider,
        repository=repository,
        reviewer_model=reviewer_model,
        chief_model=chief_model,
        event_id=prepared.event_id,
        event_payload=prepared.event_payload,
        evidence=prepared.evidence,
        review_run_id=review_run_id,
        failure_journal_path=failure_journal_path,
        reviewer_timeout_seconds=reviewer_timeout_seconds,
        reviewer_max_tokens=reviewer_max_tokens,
        chief_timeout_seconds=chief_timeout_seconds,
        chief_max_tokens=chief_max_tokens,
        **kwargs,
    )

def review_persisted_market_event_with_runtime(
    *,
    storage: MarketStorage,
    repository: EventReviewRepository,
    runtime: EventReviewRuntime,
    event_id: str,
    **pipeline_options: Any,
) -> EventReviewServiceResult:
    """Run one event through a fresh per-event budget and shared global guard.

    Existing callers may continue injecting a provider directly through
    review_persisted_market_event().
    """
    if not isinstance(runtime, EventReviewRuntime):
        raise TypeError("runtime must be an EventReviewRuntime")
    return review_persisted_market_event(
        storage=storage,
        repository=repository,
        provider=runtime.provider_for_event(),
        reviewer_model=runtime.reviewer_model,
        chief_model=runtime.chief_model,
        event_id=event_id,
        **pipeline_options,
    )


__all__ = [
    "review_persisted_market_event",
    "review_persisted_market_event_with_runtime",
]
