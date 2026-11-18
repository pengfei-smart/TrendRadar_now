"""Ranking and deduplication helpers for V1 opportunities.

The ranking layer is intentionally pure. It does not access databases,
network sources, schedulers, or notification channels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .opportunity_models import (
    IndustryOpportunityScore,
    SecurityOpportunityScore,
)


@dataclass(frozen=True)
class OpportunityRanking:
    """Immutable ranked opportunity list."""

    items: tuple[object, ...]
    total_candidates: int
    eligible_candidates: int


def _validate_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("limit must be an integer")
    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    return limit


def _rank_key(item: object) -> tuple[float, float, str]:
    return (
        -float(getattr(item, "score")),
        -float(getattr(item, "confidence")),
        str(getattr(item, "target_code")),
    )


def _is_better(left: object, right: object) -> bool:
    """Return True when left should represent the same target."""

    return _rank_key(left) < _rank_key(right)


def _deduplicate_by_target(
    scores: Iterable[object],
) -> tuple[object, ...]:
    """Keep the strongest score for each target code."""

    best_by_target: dict[str, object] = {}

    for item in scores:
        target_code = str(getattr(item, "target_code"))

        current = best_by_target.get(target_code)
        if current is None or _is_better(item, current):
            best_by_target[target_code] = item

    return tuple(best_by_target.values())


def deduplicate_industry_opportunities(
    scores: Iterable[IndustryOpportunityScore],
) -> tuple[IndustryOpportunityScore, ...]:
    """Deduplicate industry scores by industry code."""

    return _deduplicate_by_target(scores)  # type: ignore[return-value]


def deduplicate_security_opportunities(
    scores: Iterable[SecurityOpportunityScore],
) -> tuple[SecurityOpportunityScore, ...]:
    """Deduplicate security scores by security code."""

    return _deduplicate_by_target(scores)  # type: ignore[return-value]


def rank_industry_opportunities(
    scores: Iterable[IndustryOpportunityScore],
    *,
    limit: int = 10,
) -> OpportunityRanking:
    """Deduplicate, filter, and rank industry opportunities."""

    limit = _validate_limit(limit)
    candidates = tuple(scores)

    eligible = tuple(
        item
        for item in candidates
        if item.score > 0
    )

    deduplicated = deduplicate_industry_opportunities(eligible)
    ranked = tuple(sorted(deduplicated, key=_rank_key)[:limit])

    return OpportunityRanking(
        items=ranked,
        total_candidates=len(candidates),
        eligible_candidates=len(deduplicated),
    )


def rank_security_opportunities(
    scores: Iterable[SecurityOpportunityScore],
    *,
    limit: int = 20,
) -> OpportunityRanking:
    """Deduplicate, filter, and rank security opportunities."""

    limit = _validate_limit(limit)
    candidates = tuple(scores)

    eligible = tuple(
        item
        for item in candidates
        if item.score > 0
    )

    deduplicated = deduplicate_security_opportunities(eligible)
    ranked = tuple(sorted(deduplicated, key=_rank_key)[:limit])

    return OpportunityRanking(
        items=ranked,
        total_candidates=len(candidates),
        eligible_candidates=len(deduplicated),
    )


def rank_opportunities(
    industry_scores: Sequence[IndustryOpportunityScore],
    security_scores: Sequence[SecurityOpportunityScore],
    *,
    industry_limit: int = 10,
    security_limit: int = 20,
) -> tuple[OpportunityRanking, OpportunityRanking]:
    """Build both V1 opportunity rankings."""

    return (
        rank_industry_opportunities(
            industry_scores,
            limit=industry_limit,
        ),
        rank_security_opportunities(
            security_scores,
            limit=security_limit,
        ),
    )
