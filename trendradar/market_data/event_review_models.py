"""Typed, auditable contracts for an AI event-review board.

These contracts describe review evidence, independent reviewer assessments and
chief-reviewer decisions. They do not call AI models or automatically approve
events. Model output must be validated and adjudicated before downstream use.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


REVIEW_ROLES = (
    "fact_checker",
    "macro_policy_analyst",
    "industry_chain_analyst",
    "market_expectations_analyst",
    "chief_reviewer",
)
REVIEWER_ROLES = REVIEW_ROLES[:-1]
EVIDENCE_KINDS = frozenset({"primary", "secondary", "contradictory", "context"})
REVIEW_RECOMMENDATIONS = frozenset({"support", "concern", "abstain"})
REVIEW_COMPLETION_STATUSES = frozenset({"complete", "insufficient_evidence", "error"})
ADJUDICATION_STATUSES = frozenset({
    "accepted", "conditional", "needs_evidence", "rejected",
})
IMPACT_DIMENSIONS = ("severity", "scope", "duration", "surprise")


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _unit_interval(value: float, field: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{field} must be a number, not bool")
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"{field} must be a number") from exc
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field} must be finite and between 0 and 1")
    return normalized


def _dimension_pairs(values: tuple[tuple[str, float | None], ...], field: str):
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple of (dimension, score) pairs")
    seen: set[str] = set()
    normalized = []
    for pair in values:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise TypeError(f"{field} entries must be 2-item tuples")
        name, score = pair
        if name not in IMPACT_DIMENSIONS:
            raise ValueError(f"{field} contains unknown dimension: {name!r}")
        if name in seen:
            raise ValueError(f"{field} contains duplicate dimension: {name}")
        seen.add(name)
        normalized.append((name, None if score is None else _unit_interval(score, f"{field}.{name}")))
    return tuple(normalized)


@dataclass(frozen=True)
class ReviewEvidence:
    """A traceable source excerpt supplied to reviewers."""

    evidence_id: str
    event_id: str
    source: str
    source_url: str
    excerpt: str
    evidence_kind: str
    published_at: str = ""
    captured_at: str = ""
    supports_dimensions: tuple[str, ...] = ()

    def __post_init__(self):
        for field in ("evidence_id", "event_id", "source", "excerpt", "evidence_kind"):
            object.__setattr__(self, field, _required_text(getattr(self, field), field))
        if self.evidence_kind not in EVIDENCE_KINDS:
            raise ValueError(f"evidence_kind must be one of {sorted(EVIDENCE_KINDS)}")
        if not isinstance(self.source_url, str):
            raise TypeError("source_url must be a string")
        if not isinstance(self.supports_dimensions, tuple):
            raise TypeError("supports_dimensions must be a tuple")
        normalized = tuple(dict.fromkeys(
            str(item).strip().lower() for item in self.supports_dimensions
        ))
        if any(item not in IMPACT_DIMENSIONS for item in normalized):
            raise ValueError("supports_dimensions contains an unknown dimension")
        object.__setattr__(self, "supports_dimensions", normalized)


@dataclass(frozen=True)
class ReviewerAssessment:
    """One role's independent assessment; scores are suggestions, not approvals."""

    event_id: str
    reviewer_role: str
    recommendation: str
    completion_status: str
    dimension_scores: tuple[tuple[str, float | None], ...]
    dimension_rationales: tuple[tuple[str, str], ...]
    evidence_ids: tuple[str, ...]
    confidence: float
    summary: str
    concerns: tuple[str, ...] = ()
    model_provider: str = ""
    model_name: str = ""
    model_version: str = ""
    prompt_version: str = ""

    def __post_init__(self):
        object.__setattr__(self, "event_id", _required_text(self.event_id, "event_id"))
        if self.reviewer_role not in REVIEWER_ROLES:
            raise ValueError("reviewer_role must be a non-chair review role")
        if self.recommendation not in REVIEW_RECOMMENDATIONS:
            raise ValueError("invalid reviewer recommendation")
        if self.completion_status not in REVIEW_COMPLETION_STATUSES:
            raise ValueError("invalid review completion_status")
        object.__setattr__(
            self, "dimension_scores",
            _dimension_pairs(self.dimension_scores, "dimension_scores"),
        )
        if not isinstance(self.dimension_rationales, tuple):
            raise TypeError("dimension_rationales must be a tuple")
        rationale_names = set()
        normalized_rationales = []
        for pair in self.dimension_rationales:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise TypeError("dimension_rationales entries must be 2-item tuples")
            name, rationale = pair
            if name not in IMPACT_DIMENSIONS or name in rationale_names:
                raise ValueError("invalid or duplicate dimension rationale")
            rationale_names.add(name)
            normalized_rationales.append((name, _required_text(rationale, f"rationale.{name}")))
        object.__setattr__(self, "dimension_rationales", tuple(normalized_rationales))
        if not isinstance(self.evidence_ids, tuple):
            raise TypeError("evidence_ids must be a tuple")
        evidence_ids = tuple(dict.fromkeys(
            _required_text(item, "evidence_id") for item in self.evidence_ids
        ))
        object.__setattr__(self, "evidence_ids", evidence_ids)
        if not isinstance(self.concerns, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.concerns
        ):
            raise ValueError("concerns must be a tuple of non-empty strings")
        object.__setattr__(self, "confidence", _unit_interval(self.confidence, "confidence"))
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))
        for field in ("model_provider", "model_name", "model_version", "prompt_version"):
            value = getattr(self, field)
            if not isinstance(value, str):
                raise TypeError(f"{field} must be a string")
            object.__setattr__(self, field, value.strip())


@dataclass(frozen=True)
class ChiefAdjudication:
    """Final chair decision with explicit unresolved issues and dissent."""

    event_id: str
    status: str
    final_dimension_scores: tuple[tuple[str, float | None], ...]
    rationale: str
    reviewer_roles_considered: tuple[str, ...]
    dissenting_views: tuple[str, ...] = ()
    unresolved_issues: tuple[str, ...] = ()
    required_followups: tuple[str, ...] = ()
    chair_model_provider: str = ""
    chair_model_name: str = ""
    chair_model_version: str = ""
    prompt_version: str = ""

    def __post_init__(self):
        object.__setattr__(self, "event_id", _required_text(self.event_id, "event_id"))
        if self.status not in ADJUDICATION_STATUSES:
            raise ValueError("invalid adjudication status")
        object.__setattr__(
            self, "final_dimension_scores",
            _dimension_pairs(self.final_dimension_scores, "final_dimension_scores"),
        )
        object.__setattr__(self, "rationale", _required_text(self.rationale, "rationale"))
        if not isinstance(self.reviewer_roles_considered, tuple):
            raise TypeError("reviewer_roles_considered must be a tuple")
        roles = tuple(dict.fromkeys(self.reviewer_roles_considered))
        if any(role not in REVIEWER_ROLES for role in roles):
            raise ValueError("reviewer_roles_considered contains an unknown reviewer")
        object.__setattr__(self, "reviewer_roles_considered", roles)
        for field in ("dissenting_views", "unresolved_issues", "required_followups"):
            values = getattr(self, field)
            if not isinstance(values, tuple) or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                raise ValueError(f"{field} must be a tuple of non-empty strings")
        for field in (
            "chair_model_provider", "chair_model_name",
            "chair_model_version", "prompt_version",
        ):
            value = getattr(self, field)
            if not isinstance(value, str):
                raise TypeError(f"{field} must be a string")
            object.__setattr__(self, field, value.strip())
        if self.status == "accepted" and self.unresolved_issues:
            raise ValueError("accepted adjudication cannot contain unresolved issues")
