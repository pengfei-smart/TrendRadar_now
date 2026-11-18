"""Workflow coordinator for assembling an auditable AI event review board.

The coordinator validates reviewer coverage, evidence references and chair
adjudication. It does not call models, invent missing reviews, or decide event
importance. A board is only adjudicated when the chair decision is supplied.
"""
from __future__ import annotations

from dataclasses import dataclass

from .event_review_models import (
    REVIEWER_ROLES,
    ChiefAdjudication,
    ReviewEvidence,
    ReviewerAssessment,
)


@dataclass(frozen=True)
class ReviewBoardResult:
    """Immutable, validated review-board package for one event."""

    event_id: str
    status: str
    evidence: tuple[ReviewEvidence, ...]
    reviewer_assessments: tuple[ReviewerAssessment, ...]
    missing_reviewer_roles: tuple[str, ...]
    adjudication: ChiefAdjudication | None
    validation_warnings: tuple[str, ...]


def assemble_event_review_board(
    *,
    event_id: str,
    evidence: tuple[ReviewEvidence, ...],
    reviewer_assessments: tuple[ReviewerAssessment, ...],
    adjudication: ChiefAdjudication | None = None,
) -> ReviewBoardResult:
    """Validate and assemble one event's evidence, four reviews and chair ruling.

    Status values:
    - incomplete: one or more role assessments are absent or unfinished.
    - ready_for_adjudication: all four role assessments are complete.
    - adjudicated: all four role assessments and a valid chair ruling exist.

    The function intentionally permits incomplete packages so a scheduler can
    persist partial progress without treating it as an approval.
    """
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id must be a non-empty string")
    normalized_event_id = event_id.strip()

    if not isinstance(evidence, tuple) or any(
        not isinstance(item, ReviewEvidence) for item in evidence
    ):
        raise TypeError("evidence must be a tuple of ReviewEvidence")
    if not isinstance(reviewer_assessments, tuple) or any(
        not isinstance(item, ReviewerAssessment) for item in reviewer_assessments
    ):
        raise TypeError(
            "reviewer_assessments must be a tuple of ReviewerAssessment"
        )
    if adjudication is not None and not isinstance(adjudication, ChiefAdjudication):
        raise TypeError("adjudication must be a ChiefAdjudication or None")

    for item in evidence:
        if item.event_id != normalized_event_id:
            raise ValueError("evidence event_id does not match review board event_id")
    evidence_ids = [item.evidence_id for item in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("evidence_id values must be unique within a review board")

    for item in reviewer_assessments:
        if item.event_id != normalized_event_id:
            raise ValueError(
                "reviewer assessment event_id does not match review board event_id"
            )
        unknown_ids = set(item.evidence_ids) - set(evidence_ids)
        if unknown_ids:
            raise ValueError(
                f"reviewer assessment references unknown evidence IDs: {sorted(unknown_ids)}"
            )

    roles_seen: set[str] = set()
    for item in reviewer_assessments:
        if item.reviewer_role in roles_seen:
            raise ValueError(
                f"duplicate reviewer assessment for role: {item.reviewer_role}"
            )
        roles_seen.add(item.reviewer_role)

    missing_roles = tuple(role for role in REVIEWER_ROLES if role not in roles_seen)
    unfinished_roles = tuple(
        item.reviewer_role for item in reviewer_assessments
        if item.completion_status != "complete"
    )
    warnings: list[str] = []
    if missing_roles:
        warnings.append(
            "缺少审查员意见：" + ", ".join(missing_roles)
        )
    if unfinished_roles:
        warnings.append(
            "审查未完成或执行失败：" + ", ".join(unfinished_roles)
        )

    all_reviews_complete = not missing_roles and not unfinished_roles
    if not all_reviews_complete:
        if adjudication is not None:
            raise ValueError(
                "chair adjudication is not allowed before all four reviewer assessments complete"
            )
        return ReviewBoardResult(
            event_id=normalized_event_id,
            status="incomplete",
            evidence=evidence,
            reviewer_assessments=reviewer_assessments,
            missing_reviewer_roles=missing_roles,
            adjudication=None,
            validation_warnings=tuple(warnings),
        )

    if adjudication is None:
        return ReviewBoardResult(
            event_id=normalized_event_id,
            status="ready_for_adjudication",
            evidence=evidence,
            reviewer_assessments=reviewer_assessments,
            missing_reviewer_roles=(),
            adjudication=None,
            validation_warnings=(),
        )

    if adjudication.event_id != normalized_event_id:
        raise ValueError("adjudication event_id does not match review board event_id")
    considered = set(adjudication.reviewer_roles_considered)
    missing_considered = set(REVIEWER_ROLES) - considered
    if missing_considered:
        raise ValueError(
            "chair adjudication must consider all reviewer roles: "
            + ", ".join(sorted(missing_considered))
        )

    return ReviewBoardResult(
        event_id=normalized_event_id,
        status="adjudicated",
        evidence=evidence,
        reviewer_assessments=reviewer_assessments,
        missing_reviewer_roles=(),
        adjudication=adjudication,
        validation_warnings=(),
    )
