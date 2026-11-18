"""Pure builders for V1 opportunity-scoring inputs.

The builders normalize already-available observations into the immutable
OpportunityInput contracts. They do not access databases, networks, or
perform opportunity scoring.
"""

from __future__ import annotations

from typing import Iterable

from .opportunity_models import (
    IndustryOpportunityInput,
    SecurityOpportunityInput,
)


def _normalize_code(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")

    normalized = value.strip().upper()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")

    return normalized


def _validate_unit_interval(value: float, field_name: str) -> float:
    value = float(value)

    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be between 0 and 1")

    return value


def _validate_non_negative(value: float, field_name: str) -> float:
    value = float(value)

    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")

    return value


def _normalize_optional_float(
    value: float | None,
    field_name: str,
) -> float | None:
    if value is None:
        return None

    return float(value)


def _normalize_risk_flags(
    risk_flags: Iterable[str],
) -> tuple[str, ...]:
    normalized: list[str] = []

    for flag in risk_flags:
        if not isinstance(flag, str):
            raise TypeError("risk_flags must contain strings")

        value = flag.strip()
        if value and value not in normalized:
            normalized.append(value)

    return tuple(normalized)


def build_industry_opportunity_input(
    *,
    industry_code: str,
    event_strength: float,
    evidence_type: str,
    relevance_score: float,
    transmission_distance: int,
    price_change_pct: float | None,
    volume_ratio: float | None,
    relative_strength_pct: float | None,
    breadth_ratio: float | None,
    leader_confirmation_ratio: float | None,
    age_hours: float,
    risk_flags: Iterable[str] = (),
    eligible: bool = True,
) -> IndustryOpportunityInput:
    """Build one normalized industry opportunity input."""

    industry_code = _normalize_code(industry_code, "industry_code")

    event_strength = _validate_unit_interval(
        event_strength,
        "event_strength",
    )
    relevance_score = _validate_unit_interval(
        relevance_score,
        "relevance_score",
    )

    if not isinstance(evidence_type, str):
        raise TypeError("evidence_type must be a string")

    evidence_type = evidence_type.strip().lower()
    if not evidence_type:
        raise ValueError("evidence_type must not be empty")

    if isinstance(transmission_distance, bool):
        raise TypeError("transmission_distance must be an integer")

    if not isinstance(transmission_distance, int):
        raise TypeError("transmission_distance must be an integer")

    if transmission_distance < 0:
        raise ValueError("transmission_distance must be non-negative")

    age_hours = _validate_non_negative(age_hours, "age_hours")

    if breadth_ratio is not None:
        breadth_ratio = _validate_unit_interval(
            breadth_ratio,
            "breadth_ratio",
        )

    if leader_confirmation_ratio is not None:
        leader_confirmation_ratio = _validate_unit_interval(
            leader_confirmation_ratio,
            "leader_confirmation_ratio",
        )

    return IndustryOpportunityInput(
        industry_code=industry_code,
        event_strength=event_strength,
        evidence_type=evidence_type,
        relevance_score=relevance_score,
        transmission_distance=transmission_distance,
        price_change_pct=_normalize_optional_float(
            price_change_pct,
            "price_change_pct",
        ),
        volume_ratio=_normalize_optional_float(
            volume_ratio,
            "volume_ratio",
        ),
        relative_strength_pct=_normalize_optional_float(
            relative_strength_pct,
            "relative_strength_pct",
        ),
        breadth_ratio=breadth_ratio,
        leader_confirmation_ratio=leader_confirmation_ratio,
        age_hours=age_hours,
        risk_flags=_normalize_risk_flags(risk_flags),
        eligible=bool(eligible),
    )


def build_security_opportunity_input(
    *,
    security_code: str,
    industry_code: str,
    industry_score: float,
    direct_event: bool,
    event_strength: float,
    price_change_pct: float | None,
    volume_ratio: float | None,
    relative_strength_pct: float | None,
    liquidity_ratio: float | None,
    market_confirmation_ratio: float | None,
    risk_flags: Iterable[str] = (),
    eligible: bool = True,
) -> SecurityOpportunityInput:
    """Build one normalized security opportunity input."""

    security_code = _normalize_code(security_code, "security_code")
    industry_code = _normalize_code(industry_code, "industry_code")

    industry_score = float(industry_score)
    if not 0.0 <= industry_score <= 100.0:
        raise ValueError("industry_score must be between 0 and 100")

    event_strength = _validate_unit_interval(
        event_strength,
        "event_strength",
    )

    if not isinstance(direct_event, bool):
        raise TypeError("direct_event must be a boolean")

    if market_confirmation_ratio is not None:
        market_confirmation_ratio = _validate_unit_interval(
            market_confirmation_ratio,
            "market_confirmation_ratio",
        )

    return SecurityOpportunityInput(
        security_code=security_code,
        industry_code=industry_code,
        industry_score=industry_score,
        direct_event=direct_event,
        event_strength=event_strength,
        price_change_pct=_normalize_optional_float(
            price_change_pct,
            "price_change_pct",
        ),
        volume_ratio=_normalize_optional_float(
            volume_ratio,
            "volume_ratio",
        ),
        relative_strength_pct=_normalize_optional_float(
            relative_strength_pct,
            "relative_strength_pct",
        ),
        liquidity_ratio=_normalize_optional_float(
            liquidity_ratio,
            "liquidity_ratio",
        ),
        market_confirmation_ratio=market_confirmation_ratio,
        risk_flags=_normalize_risk_flags(risk_flags),
        eligible=bool(eligible),
    )
