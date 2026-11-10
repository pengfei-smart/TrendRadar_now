# 本文件验证跨市场事件映射的证据、可信度上限与已审核关系解析规则。
"""Unit tests for evidence-first cross-market mapping."""

import unittest

from trendradar.market_data.cross_market_mapper import (
    discover_sector_evidence,
    resolve_trusted_relations,
)
from trendradar.market_data.models import MappingEvidence, MarketEvent


class CrossMarketMapperTests(unittest.TestCase):
    def event(self, title, direction="positive"):
        return MarketEvent(
            event_id="evt-1",
            source="SEC EDGAR",
            title=title,
            published_at="2026-11-09T20:00:00+08:00",
            direction=direction,
            region="US",
            url="https://www.sec.gov/",
            summary="",
        )

    def test_semiconductor_event_produces_sector_evidence(self):
        evidence = discover_sector_evidence(self.event("NVIDIA reports GPU demand"))
        self.assertTrue(any(item.target_code == "sector:半导体" for item in evidence))
        self.assertTrue(all(item.verified is False for item in evidence))

    def test_event_direction_is_preserved_as_evidence_not_trade_advice(self):
        evidence = discover_sector_evidence(self.event("NVIDIA reports GPU demand", "negative"))
        self.assertTrue(evidence)
        self.assertTrue(all(item.direction == "negative" for item in evidence))

    def test_unmatched_event_returns_empty(self):
        self.assertEqual(discover_sector_evidence(self.event("Company announces routine filing")), [])

    def test_only_sector_evidence_is_resolved(self):
        non_sector = MappingEvidence(
            target_code="000001", mapping_type="direct_business", confidence=0.9,
            evidence="manual", verified=True,
        )
        self.assertEqual(resolve_trusted_relations([non_sector], {}), [])

    def test_resolved_confidence_is_capped_by_weaker_evidence(self):
        sector = MappingEvidence(
            target_code="sector:半导体", mapping_type="industry_sentiment", confidence=0.68,
            evidence="event text hit", source="SEC EDGAR", source_url="https://www.sec.gov/",
            direction="positive", verified=False,
        )
        relation = MappingEvidence(
            target_code="688012", mapping_type="supply_chain", confidence=0.91,
            evidence="reviewed supply relation", source="company filing",
            source_url="https://example.com/evidence", direction="neutral", verified=True,
        )
        result = resolve_trusted_relations([sector], {"半导体": [relation]})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].confidence, 0.68)
        self.assertEqual(result[0].direction, "positive")
        self.assertTrue(result[0].verified)

    def test_missing_trusted_relation_does_not_create_stock_candidate(self):
        sector = MappingEvidence(
            target_code="sector:创新药", mapping_type="industry_sentiment", confidence=0.6,
            evidence="keyword hit", verified=False,
        )
        self.assertEqual(resolve_trusted_relations([sector], {}), [])


if __name__ == "__main__":
    unittest.main()
