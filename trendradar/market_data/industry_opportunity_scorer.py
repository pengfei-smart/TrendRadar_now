"""V1 balanced industry opportunity scorer.

The scorer is deliberately pure: it consumes normalized inputs and returns an
explainable score. It does not access storage, providers, or ranking state.
"""
from __future__ import annotations

from trendradar.market_data.opportunity_models import (
    IndustryOpportunityInput,
    IndustryOpportunityScore,
)

_EVIDENCE_SCORE = {
    "direct": 15.0,
    "official_name": 12.0,
    "upstream": 9.0,
    "downstream": 9.0,
    "legacy_unknown": 5.0,
}

_MIN_BREADTH_COVERAGE_RATIO = 0.60

_LEVELS = (
    (85.0, "S"),
    (75.0, "A"),
    (65.0, "B"),
    (55.0, "C"),
    (0.0, "D"),
)


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _normalized(value: float | None, low: float, high: float) -> float:
    if value is None:
        return 0.0
    if high <= low:
        raise ValueError("归一化区间必须满足 high > low")
    return _clamp((value - low) / (high - low))


def _level(score: float) -> str:
    for threshold, level in _LEVELS:
        if score >= threshold:
            return level
    return "D"


def _market_confirmation(item: IndustryOpportunityInput) -> float:
    """Return 0..25 using five equally explicit market observations."""

    price = _normalized(item.price_change_pct, -3.0, 6.0) * 6.0
    volume = _normalized(item.volume_ratio, 0.7, 2.5) * 5.0
    relative = _normalized(item.relative_strength_pct, -2.0, 5.0) * 5.0
    coverage = item.quote_coverage_ratio
    breadth = (
        _clamp(item.breadth_ratio or 0.0) * 5.0
        if coverage is not None and coverage >= _MIN_BREADTH_COVERAGE_RATIO
        else 0.0
    )
    leaders = _clamp(item.leader_confirmation_ratio or 0.0) * 4.0
    return price + volume + relative + breadth + leaders


def _risk_adjustment(item: IndustryOpportunityInput) -> float:
    adjustment = 0.0
    for flag in item.risk_flags:
        if flag in {"data_stale", "data_missing"}:
            adjustment -= 2.0
        elif flag in {"low_liquidity", "weak_breadth"}:
            adjustment -= 3.0
        elif flag in {"high_volatility", "conflicting_signal"}:
            adjustment -= 4.0
        else:
            adjustment -= 1.0
    return max(-15.0, adjustment)


def score_industry_opportunity(
    item: IndustryOpportunityInput,
) -> IndustryOpportunityScore:
    """Score one industry using the balanced V1 95-point base plus risk."""

    if not item.industry_code.strip():
        raise ValueError("行业代码不能为空")
    if not 0.0 <= item.event_strength <= 1.0:
        raise ValueError("event_strength必须在0到1之间")
    if not 0.0 <= item.relevance_score <= 1.0:
        raise ValueError("relevance_score必须在0到1之间")
    if (
        item.quote_coverage_ratio is not None
        and not 0.0 <= item.quote_coverage_ratio <= 1.0
    ):
        raise ValueError("quote_coverage_ratio必须在0到1之间")
    if not item.eligible:
        return IndustryOpportunityScore(
            target_code=item.industry_code,
            score=0.0,
            raw_score=0.0,
            confidence=0.0,
            opportunity_level="D",
            opportunity_type="ineligible",
            event_score=0.0,
            evidence_score=0.0,
            relevance_score=0.0,
            transmission_score=0.0,
            market_confirmation_score=0.0,
            freshness_score=0.0,
            risk_adjustment=-15.0,
            explanation=("候选行业未通过资格过滤",),
        )

    event_score = _clamp(item.event_strength) * 20.0
    evidence_score = _EVIDENCE_SCORE.get(item.evidence_type, 5.0)
    relevance_score = _clamp(item.relevance_score) * 15.0
    transmission_score = max(0.0, 10.0 - min(item.transmission_distance, 3) * 3.0)
    market_confirmation_score = _market_confirmation(item)

    if item.age_hours < 0:
        raise ValueError("事件年龄不能为负数")
    freshness_score = 10.0 * _clamp(1.0 - item.age_hours / 48.0)
    risk_adjustment = _risk_adjustment(item)

    raw_score = (
        event_score
        + evidence_score
        + relevance_score
        + transmission_score
        + market_confirmation_score
        + freshness_score
    )
    score = max(0.0, min(100.0, raw_score + risk_adjustment))

    breadth_confidence = (
        _clamp(item.breadth_ratio or 0.0)
        * _clamp(item.quote_coverage_ratio)
        if item.quote_coverage_ratio is not None
        else 0.0
    )
    confidence_parts = [
        _clamp(item.event_strength),
        _clamp(item.relevance_score),
        breadth_confidence,
        _clamp(item.leader_confirmation_ratio or 0.0),
    ]
    confidence = sum(confidence_parts) / len(confidence_parts)

    opportunity_type = (
        "event_driven"
        if item.evidence_type in {"direct", "official_name"}
        else "transmission_driven"
    )
    explanation_parts = [
        (
            "事件证据为直接/官方名称关联"
            if item.evidence_type in {"direct", "official_name"}
            else "机会主要来自产业传导"
        )
    ]
    if (
        item.quote_coverage_ratio is None
        or item.quote_coverage_ratio < _MIN_BREADTH_COVERAGE_RATIO
    ):
        explanation_parts.append("行业行情覆盖率不足60%，上涨广度不计入市场确认分")
    explanation = tuple(explanation_parts)
    return IndustryOpportunityScore(
        target_code=item.industry_code,
        score=round(score, 2),
        raw_score=round(raw_score, 2),
        confidence=round(confidence, 4),
        opportunity_level=_level(score),
        opportunity_type=opportunity_type,
        event_score=round(event_score, 2),
        evidence_score=round(evidence_score, 2),
        relevance_score=round(relevance_score, 2),
        transmission_score=round(transmission_score, 2),
        market_confirmation_score=round(market_confirmation_score, 2),
        freshness_score=round(freshness_score, 2),
        risk_adjustment=round(risk_adjustment, 2),
        explanation=(explanation,),
    )


class IndustryOpportunityScorer:
    """Small object wrapper for dependency injection and future configuration."""

    def score(self, item: IndustryOpportunityInput) -> IndustryOpportunityScore:
        return score_industry_opportunity(item)
