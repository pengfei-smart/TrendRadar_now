"""Typed input and output contracts for opportunity scoring.

The models intentionally contain no data access or scoring logic. Scorers consume
these immutable values and return immutable, explainable score results.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class IndustryOpportunityInput:
    """Normalized inputs required to score one industry opportunity.

    event_strength and relevance_score are both normalized to the inclusive
    0..1 interval; their scorer components scale these values to 20 and 15
    points respectively.
    """

    industry_code: str
    event_strength: float
    evidence_type: str
    relevance_score: float
    transmission_distance: int
    price_change_pct: float | None
    volume_ratio: float | None
    relative_strength_pct: float | None
    breadth_ratio: float | None
    leader_confirmation_ratio: float | None
    age_hours: float
    risk_flags: tuple[str, ...] = ()
    eligible: bool = True


@dataclass(frozen=True)
class SecurityOpportunityInput:
    """Normalized inputs required to score one security opportunity.

    event_strength is normalized to the inclusive 0..1 interval and scales
    to a maximum direct-event contribution of 20 points.
    """

    security_code: str
    industry_code: str
    industry_score: float
    direct_event: bool
    event_strength: float
    price_change_pct: float | None
    volume_ratio: float | None
    relative_strength_pct: float | None
    liquidity_ratio: float | None
    market_confirmation_ratio: float | None
    risk_flags: tuple[str, ...] = ()
    eligible: bool = True


@dataclass(frozen=True)
class IndustryOpportunityScore:
    """Explainable score result for one industry."""

    target_code: str
    score: float
    raw_score: float
    confidence: float
    opportunity_level: str
    opportunity_type: str
    event_score: float
    evidence_score: float
    relevance_score: float
    transmission_score: float
    market_confirmation_score: float
    freshness_score: float
    risk_adjustment: float
    explanation: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecurityOpportunityScore:
    """Explainable score result for one security."""

    target_code: str
    industry_code: str
    score: float
    raw_score: float
    confidence: float
    opportunity_level: str
    opportunity_type: str
    industry_opportunity_score: float
    event_score: float
    price_strength_score: float
    liquidity_score: float
    relative_strength_score: float
    market_confirmation_score: float
    risk_adjustment: float
    explanation: tuple[str, ...] = ()
