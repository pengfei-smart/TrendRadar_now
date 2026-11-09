# 本文件实现机会雷达候选分层与透明规则评分，属于逐文件开发清单第八项。
"""Transparent, rule-based opportunity ranking."""

from typing import Iterable, Optional

from trendradar.market_data.models import Candidate, QuoteSnapshot, Security, MappingEvidence


def score_candidate(
    security: Security,
    evidence: Iterable[MappingEvidence],
    quote: Optional[QuoteSnapshot],
) -> Candidate:
    """Score a candidate using evidence strength and observable activity.

    The score is a discovery ranking only; it must not be interpreted as a
    forecast or a recommendation to trade.
    """
    evidence_list = list(evidence)
    score = 0.0
    reasons: list[str] = []
    tier = "C"
    best_confidence = max((e.confidence for e in evidence_list), default=0.0)

    if any(e.mapping_type == "direct_business" for e in evidence_list):
        score += 35
        tier = "A"
        reasons.append("事件直接业务关联")
    elif any(e.mapping_type == "supply_chain" for e in evidence_list):
        score += 22
        tier = "B"
        reasons.append("产业链关联候选")
    elif evidence_list:
        score += 12
        reasons.append("行业/宏观传导候选")

    score += min(20.0, best_confidence * 20.0)
    if best_confidence:
        reasons.append(f"映射可信度 {best_confidence:.0%}")

    if quote:
        if quote.change_pct is not None:
            score += min(15.0, abs(quote.change_pct) * 1.5)
            reasons.append(f"涨跌幅 {quote.change_pct:+.2f}%")
        if quote.turnover is not None and quote.turnover > 0:
            score += min(15.0, quote.turnover / 100_000_000 * 3)
            reasons.append("成交活跃度加分")
        if quote.volume_ratio is not None and quote.volume_ratio > 1:
            score += min(10.0, (quote.volume_ratio - 1) * 3)
            reasons.append(f"量比 {quote.volume_ratio:.2f}")

    causal_status = "verified" if any(e.verified for e in evidence_list) else "unverified"
    return Candidate(
        security=security,
        tier=tier,
        score=round(score, 2),
        reasons=reasons,
        evidence=evidence_list,
        quote=quote,
        causal_status=causal_status,
    )


def rank_candidates(candidates: Iterable[Candidate], limit: int = 10) -> list[Candidate]:
    """Stable descending score order with security code as tie-breaker."""
    if limit <= 0:
        return []
    return sorted(candidates, key=lambda c: (-c.score, c.security.code))[:limit]
