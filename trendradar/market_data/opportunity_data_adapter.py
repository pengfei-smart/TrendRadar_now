"""Pure adapters for converting market observations into V1 inputs.

This layer only normalizes already-available observations. It does not
access networks, databases, schedulers, or perform opportunity scoring.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .opportunity_input_builder import (
    build_industry_opportunity_input,
    build_security_opportunity_input,
)
from .opportunity_models import (
    IndustryOpportunityInput,
    SecurityOpportunityInput,
)


@dataclass(frozen=True)
class IndustryOpportunityObservation:
    """Already-observed facts used to build an industry opportunity input."""

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
class SecurityOpportunityObservation:
    """Already-observed facts used to build a security opportunity input."""

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


def _require_bool(value: object, field_name: str) -> bool:
    """Require an actual boolean instead of coercing arbitrary values."""

    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a boolean")

    return value


def adapt_industry_observation(
    observation: IndustryOpportunityObservation,
) -> IndustryOpportunityInput:
    """Convert one industry observation into the scoring input contract."""

    if not isinstance(observation, IndustryOpportunityObservation):
        raise TypeError(
            "observation must be an IndustryOpportunityObservation"
        )

    eligible = _require_bool(observation.eligible, "eligible")

    return build_industry_opportunity_input(
        industry_code=observation.industry_code,
        event_strength=observation.event_strength,
        evidence_type=observation.evidence_type,
        relevance_score=observation.relevance_score,
        transmission_distance=observation.transmission_distance,
        price_change_pct=observation.price_change_pct,
        volume_ratio=observation.volume_ratio,
        relative_strength_pct=observation.relative_strength_pct,
        breadth_ratio=observation.breadth_ratio,
        leader_confirmation_ratio=observation.leader_confirmation_ratio,
        age_hours=observation.age_hours,
        risk_flags=observation.risk_flags,
        eligible=eligible,
    )


def adapt_security_observation(
    observation: SecurityOpportunityObservation,
) -> SecurityOpportunityInput:
    """Convert one security observation into the scoring input contract."""

    if not isinstance(observation, SecurityOpportunityObservation):
        raise TypeError(
            "observation must be a SecurityOpportunityObservation"
        )

    direct_event = _require_bool(
        observation.direct_event,
        "direct_event",
    )
    eligible = _require_bool(observation.eligible, "eligible")

    return build_security_opportunity_input(
        security_code=observation.security_code,
        industry_code=observation.industry_code,
        industry_score=observation.industry_score,
        direct_event=direct_event,
        event_strength=observation.event_strength,
        price_change_pct=observation.price_change_pct,
        volume_ratio=observation.volume_ratio,
        relative_strength_pct=observation.relative_strength_pct,
        liquidity_ratio=observation.liquidity_ratio,
        market_confirmation_ratio=observation.market_confirmation_ratio,
        risk_flags=observation.risk_flags,
        eligible=eligible,
    )


def adapt_industry_observations(
    observations: Iterable[IndustryOpportunityObservation],
) -> tuple[IndustryOpportunityInput, ...]:
    """Adapt a collection of industry observations."""

    return tuple(
        adapt_industry_observation(observation)
        for observation in observations
    )


def adapt_security_observations(
    observations: Iterable[SecurityOpportunityObservation],
) -> tuple[SecurityOpportunityInput, ...]:
    """Adapt a collection of security observations."""

    return tuple(
        adapt_security_observation(observation)
        for observation in observations
    )
