"""Pure V1 scorer for individual security opportunities.

The scorer is intentionally independent from storage and market-data access.
"""
from __future__ import annotations

from trendradar.market_data.opportunity_models import (
    SecurityOpportunityInput,
    SecurityOpportunityScore,
)


_RISK_PENALTIES = {
    "data_stale": -3.0,
    "data_missing": -4.0,
    "low_liquidity": -4.0,
    "high_volatility": -3.0,
    "weak_market": -2.0,
    "conflicting_signal": -3.0,
}


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, float(value)))


def _scaled(value: float | None, low: float, high: float, maximum: float) -> float:
    if value is None:
        return 0.0
    if high <= low:
        raise ValueError("评分区间不合法")
    return round(_clamp((float(value) - low) / (high - low)) * maximum, 2)


def _level(score: float) -> str:
    if score >= 85:
        return "S"
    if score >= 70:
        return "A"
    if score >= 55:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def _risk_adjustment(flags: tuple[str, ...]) -> float:
    return max(-15.0, sum(_RISK_PENALTIES.get(flag, -1.0) for flag in flags))


def score_security_opportunity(item: SecurityOpportunityInput) -> SecurityOpportunityScore:
    if not item.security_code.strip():
        raise ValueError("security_code不能为空")
    if not item.industry_code.strip():
        raise ValueError("industry_code不能为空")
    if not 0 <= item.industry_score <= 100:
        raise ValueError("industry_score必须在0到100之间")
    if not 0.0 <= item.event_strength <= 1.0:
        raise ValueError("event_strength必须在0到1之间")

    if not item.eligible:
        return SecurityOpportunityScore(
            target_code=item.security_code,
            industry_code=item.industry_code,
            score=0.0,
            raw_score=0.0,
            confidence=0.0,
            opportunity_level="D",
            opportunity_type="ineligible",
            industry_opportunity_score=0.0,
            event_score=0.0,
            price_strength_score=0.0,
            liquidity_score=0.0,
            relative_strength_score=0.0,
            market_confirmation_score=0.0,
            risk_adjustment=0.0,
            explanation=("个股不符合当前机会池准入条件",),
        )

    industry_score = round(_clamp(item.industry_score) * 0.25, 2)
    event_score = round(
        _clamp(item.event_strength if item.direct_event else 0.0) * 20.0,
        2,
    )
    price_strength_score = _scaled(item.price_change_pct, 0.0, 10.0, 20.0)
    liquidity_score = _scaled(item.liquidity_ratio, 1.0, 3.0, 10.0)
    relative_strength_score = _scaled(item.relative_strength_pct, 0.0, 10.0, 15.0)
    market_confirmation_score = _scaled(
        item.market_confirmation_ratio, 0.0, 1.0, 10.0
    )

    raw_score = round(
        industry_score
        + event_score
        + price_strength_score
        + liquidity_score
        + relative_strength_score
        + market_confirmation_score,
        2,
    )
    risk_adjustment = round(_risk_adjustment(item.risk_flags), 2)
    score = round(_clamp(raw_score + risk_adjustment), 2)

    observed = sum(
        value is not None
        for value in (
            item.price_change_pct,
            item.volume_ratio,
            item.relative_strength_pct,
            item.liquidity_ratio,
            item.market_confirmation_ratio,
        )
    )
    confidence = round(_clamp(50.0 + observed * 10.0 - len(item.risk_flags) * 5.0), 2)

    if item.direct_event and item.industry_score >= 70:
        opportunity_type = "mixed"
    elif item.direct_event:
        opportunity_type = "direct_event"
    elif item.industry_score >= 70:
        opportunity_type = "industry_driven"
    else:
        opportunity_type = "technical_confirmation"

    explanation = (
        f"行业机会贡献 {industry_score:.1f}/25",
        f"个股事件贡献 {event_score:.1f}/20",
        f"价格强度贡献 {price_strength_score:.1f}/20",
        f"流动性贡献 {liquidity_score:.1f}/10",
        f"相对强度贡献 {relative_strength_score:.1f}/15",
        f"市场确认贡献 {market_confirmation_score:.1f}/10",
        f"风险调整 {risk_adjustment:.1f}",
    )

    return SecurityOpportunityScore(
        target_code=item.security_code,
        industry_code=item.industry_code,
        score=score,
        raw_score=raw_score,
        confidence=confidence,
        opportunity_level=_level(score),
        opportunity_type=opportunity_type,
        industry_opportunity_score=industry_score,
        event_score=event_score,
        price_strength_score=price_strength_score,
        liquidity_score=liquidity_score,
        relative_strength_score=relative_strength_score,
        market_confirmation_score=market_confirmation_score,
        risk_adjustment=risk_adjustment,
        explanation=explanation,
    )


class SecurityOpportunityScorer:
    """Object wrapper kept for symmetry with the industry scorer."""

    def score(self, item: SecurityOpportunityInput) -> SecurityOpportunityScore:
        return score_security_opportunity(item)
