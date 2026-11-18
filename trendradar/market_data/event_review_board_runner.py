"""One-call orchestration for the five-role AI event review board.

Reviewer calls are intentionally sequential and deterministic. Any reviewer
failure leaves the board incomplete and prevents chair adjudication.
"""
from __future__ import annotations

from dataclasses import dataclass

from .ai_model_provider import ChatModelProvider
from .event_chief_reviewer_agent import (
    ChiefReviewerExecutionResult,
    run_chief_reviewer,
)
from .event_review_coordinator import (
    ReviewBoardResult,
    assemble_event_review_board,
)
from .event_review_models import REVIEWER_ROLES, ReviewEvidence
from .event_reviewer_agent import ReviewerExecutionResult, run_reviewer


@dataclass(frozen=True)
class EventReviewBoardExecutionResult:
    """Full execution trace, including partial reviewer and chair outcomes."""

    board: ReviewBoardResult
    reviewer_results: tuple[ReviewerExecutionResult, ...]
    chief_result: ChiefReviewerExecutionResult | None

    @property
    def status(self) -> str:
        if self.board.status == "incomplete":
            return "incomplete"
        if self.chief_result is not None and self.chief_result.adjudication is None:
            return "chief_error"
        if self.board.status == "adjudicated":
            return "adjudicated"
        return "ready_for_adjudication"


def run_event_review_board(
    *,
    provider: ChatModelProvider,
    reviewer_model: str,
    chief_model: str,
    event_id: str,
    event_payload: dict,
    evidence: tuple[ReviewEvidence, ...],
    reviewer_timeout_seconds: float = 45.0,
    reviewer_max_tokens: int = 2500,
    chief_timeout_seconds: float = 60.0,
    chief_max_tokens: int = 3000,
) -> EventReviewBoardExecutionResult:
    """Run four independent reviews, then chair adjudication when safe.

    This function performs no persistence and does not configure credentials.
    The caller supplies an already configured provider. Model failures remain
    explicit results and can never be converted into approval.
    """
    if not isinstance(reviewer_model, str) or not reviewer_model.strip():
        raise ValueError("reviewer_model must be a non-empty string")
    if not isinstance(chief_model, str) or not chief_model.strip():
        raise ValueError("chief_model must be a non-empty string")

    reviewer_results = tuple(
        run_reviewer(
            provider=provider,
            model=reviewer_model,
            role=role,
            event_id=event_id,
            event_payload=event_payload,
            evidence=evidence,
            timeout_seconds=reviewer_timeout_seconds,
            max_tokens=reviewer_max_tokens,
        )
        for role in REVIEWER_ROLES
    )
    assessments = tuple(item.assessment for item in reviewer_results)
    board = assemble_event_review_board(
        event_id=event_id,
        evidence=evidence,
        reviewer_assessments=assessments,
    )

    if board.status != "ready_for_adjudication":
        return EventReviewBoardExecutionResult(
            board=board,
            reviewer_results=reviewer_results,
            chief_result=None,
        )

    chief_result = run_chief_reviewer(
        provider=provider,
        model=chief_model,
        event_id=event_id,
        event_payload=event_payload,
        evidence=evidence,
        reviewer_assessments=assessments,
        timeout_seconds=chief_timeout_seconds,
        max_tokens=chief_max_tokens,
    )
    if chief_result.adjudication is None:
        return EventReviewBoardExecutionResult(
            board=board,
            reviewer_results=reviewer_results,
            chief_result=chief_result,
        )

    adjudicated_board = assemble_event_review_board(
        event_id=event_id,
        evidence=evidence,
        reviewer_assessments=assessments,
        adjudication=chief_result.adjudication,
    )
    return EventReviewBoardExecutionResult(
        board=adjudicated_board,
        reviewer_results=reviewer_results,
        chief_result=chief_result,
    )
