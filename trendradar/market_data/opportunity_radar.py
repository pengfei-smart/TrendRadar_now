"""Orchestration layer for the V1 opportunity radar.

The engine coordinates pure scoring and ranking functions. It deliberately
contains no data-access, network, database, scheduling, or notification logic.
"""

from dataclasses import dataclass
from typing import Iterable

from .industry_opportunity_scorer import score_industry_opportunity
from .opportunity_models import (
    IndustryOpportunityInput,
    IndustryOpportunityScore,
    SecurityOpportunityInput,
    SecurityOpportunityScore,
)
from .opportunity_ranking import (
    rank_industry_opportunities,
    rank_security_opportunities,
)
from .security_opportunity_scorer import score_security_opportunity


@dataclass(frozen=True)
class OpportunityRadarResult:
    """Immutable result of one V1 opportunity-radar calculation."""

    industry_board: tuple[IndustryOpportunityScore, ...]
    security_board: tuple[SecurityOpportunityScore, ...]
    industry_candidate_count: int
    security_candidate_count: int
    warnings: tuple[str, ...] = ()


class OpportunityRadarEngine:
    """Coordinate industry and security opportunity scoring."""

    def __init__(
        self,
        *,
        industry_limit: int = 10,
        security_limit: int = 20,
    ) -> None:
        if industry_limit <= 0:
            raise ValueError("industry_limit must be positive")
        if security_limit <= 0:
            raise ValueError("security_limit must be positive")

        self.industry_limit = industry_limit
        self.security_limit = security_limit

    def run(
        self,
        industry_inputs: Iterable[IndustryOpportunityInput],
        security_inputs: Iterable[SecurityOpportunityInput],
        *,
        warnings: Iterable[str] = (),
    ) -> OpportunityRadarResult:
        """Score, deduplicate, rank, and return both opportunity boards."""

        industry_scores = tuple(
            score_industry_opportunity(item)
            for item in industry_inputs
        )
        security_scores = tuple(
            score_security_opportunity(item)
            for item in security_inputs
        )

        ranked_industries = rank_industry_opportunities(
            industry_scores,
            limit=self.industry_limit,
        )
        ranked_securities = rank_security_opportunities(
            security_scores,
            limit=self.security_limit,
        )

        return OpportunityRadarResult(
            industry_board=ranked_industries.items,
            security_board=ranked_securities.items,
            industry_candidate_count=ranked_industries.eligible_candidates,
            security_candidate_count=ranked_securities.eligible_candidates,
            warnings=tuple(warnings),
        )
