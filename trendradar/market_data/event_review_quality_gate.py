"""Deterministic quality gate for AI event reviews before opportunity scoring.

A completed model workflow is not equivalent to an accepted event. This gate
requires complete, traceable reviewer coverage and an unqualified chair decision.
"""
from __future__ import annotations

from dataclasses import dataclass

from .event_review_coordinator import ReviewBoardResult
from .event_review_models import IMPACT_DIMENSIONS, REVIEWER_ROLES
from .event_review_temporal import is_pre_event_baseline


@dataclass(frozen=True)
class EventReviewQualityGateResult:
    """Machine-readable eligibility decision; never changes the source board."""

    event_id: str
    status: str
    eligible_for_scoring: bool
    reasons: tuple[str, ...]

    def __post_init__(self):
        if self.status not in {"passed", "blocked"}:
            raise ValueError("quality gate status must be passed or blocked")
        if self.eligible_for_scoring != (self.status == "passed"):
            raise ValueError("eligibility must agree with quality gate status")
        if self.status == "passed" and self.reasons:
            raise ValueError("passed quality gate cannot contain blocking reasons")


def evaluate_event_review_quality(
    board: ReviewBoardResult,
    *,
    event_payload: dict | None = None,
) -> EventReviewQualityGateResult:
    """Evaluate whether an adjudicated event may enter downstream scoring.

    Only an accepted chair decision can pass. Conditional, evidence-needed,
    rejected, incomplete and non-adjudicated boards are blocked. At least one
    primary source must be cited by the fact checker. Every reviewer must
    provide all four dimensions, their rationales and traceable evidence.
    """
    if not isinstance(board, ReviewBoardResult):
        raise TypeError("board must be a ReviewBoardResult")
    if event_payload is not None and not isinstance(event_payload, dict):
        raise TypeError("event_payload must be a dictionary or None")

    reasons: list[str] = []
    if board.status != "adjudicated" or board.adjudication is None:
        reasons.append("审核委员会尚未形成完整裁决")
    if board.status == "incomplete":
        reasons.append("至少一位审查员缺失或未完成")

    evidence_by_id = {item.evidence_id: item for item in board.evidence}
    if not board.evidence:
        reasons.append("缺少可追溯的事件证据")

    assessments_by_role = {
        item.reviewer_role: item for item in board.reviewer_assessments
    }
    for role in REVIEWER_ROLES:
        assessment = assessments_by_role.get(role)
        if assessment is None:
            reasons.append(f"缺少审查员意见：{role}")
            continue
        if assessment.completion_status != "complete":
            reasons.append(f"审查员未完整执行：{role}")
        score_dimensions = {name for name, _ in assessment.dimension_scores}
        rationale_dimensions = {name for name, _ in assessment.dimension_rationales}
        if score_dimensions != set(IMPACT_DIMENSIONS):
            reasons.append(f"审查员评分维度不完整：{role}")
        if rationale_dimensions != set(IMPACT_DIMENSIONS):
            reasons.append(f"审查员评分理由不完整：{role}")
        if not assessment.evidence_ids:
            reasons.append(f"审查员未引用证据：{role}")
        if any(evidence_id not in evidence_by_id for evidence_id in assessment.evidence_ids):
            reasons.append(f"审查员引用了未知证据：{role}")

    fact_checker = assessments_by_role.get("fact_checker")
    primary_evidence_ids = {
        item.evidence_id for item in board.evidence
        if item.evidence_kind == "primary"
    }
    if not primary_evidence_ids:
        reasons.append("缺少一级来源证据")
    elif fact_checker is None or not (
        set(fact_checker.evidence_ids) & primary_evidence_ids
    ):
        reasons.append("事实核查员未引用一级来源证据")

    adjudication = board.adjudication
    if adjudication is not None:
        if adjudication.status != "accepted":
            reasons.append(f"首席审查官裁决不是 accepted：{adjudication.status}")
        if set(adjudication.reviewer_roles_considered) != set(REVIEWER_ROLES):
            reasons.append("首席审查官未完整考虑四位审查员")
        final_dimensions = {name for name, _ in adjudication.final_dimension_scores}
        if final_dimensions != set(IMPACT_DIMENSIONS):
            reasons.append("首席审查官最终评分维度不完整")
        if adjudication.unresolved_issues:
            reasons.append("首席审查官仍保留未解决问题")
        if adjudication.required_followups:
            reasons.append("首席审查官仍要求补充后续证据或调查")
        if adjudication.dissenting_views:
            reasons.append("首席审查官仍记录未消解的审查分歧")
        final_scores = dict(adjudication.final_dimension_scores)
        if final_scores.get("surprise") is not None and not any(
            item.evidence_kind in {"primary", "secondary"}
            and "surprise" in item.supports_dimensions
            and event_payload is not None
            and is_pre_event_baseline(item, event_payload)
            for item in board.evidence
        ):
            reasons.append("超预期评分缺少发布时间早于事件发生时间的可追溯预期基准")

    normalized_reasons = tuple(dict.fromkeys(reasons))
    passed = not normalized_reasons
    return EventReviewQualityGateResult(
        event_id=board.event_id,
        status="passed" if passed else "blocked",
        eligible_for_scoring=passed,
        reasons=normalized_reasons,
    )
