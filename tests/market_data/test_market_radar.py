# 本文件测试市场雷达与持久化可信关系库的集成、候选过滤及评分排序。
"""Unit tests for market radar orchestration with relation-store integration."""

import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.models import Security, QuoteSnapshot, MappingEvidence, MarketEvent
from trendradar.market_data.market_radar import MarketRadar
from trendradar.market_data.relation_store import CrossMarketRelationStore
from trendradar.market_data.security_master import normalize_security_code, validate_security_master
from trendradar.market_data.cross_market_mapper import discover_sector_evidence
from trendradar.market_data.opportunity_ranker import score_candidate, rank_candidates


class StubQuoteFetcher:
    def __init__(self):
        self.requested = []

    def fetch(self, codes):
        self.requested = list(codes)
        return [QuoteSnapshot(code, "2026-11-09T14:33:00+08:00", change_pct=2.0,
                              turnover=200_000_000, volume_ratio=1.5) for code in codes]


class MarketRadarTests(unittest.TestCase):
    def test_normalize_code(self):
        self.assertEqual(normalize_security_code("600000.SH"), "600000")
        self.assertEqual(normalize_security_code("1"), "000001")
        with self.assertRaises(ValueError):
            normalize_security_code("bad-code")

    def test_filters_risk_and_inactive(self):
        securities = [Security("600000", "浦发银行"), Security("600001", "风险股", risk_flag=True),
                      Security("600002", "停牌", listing_status="suspended")]
        self.assertEqual([s.code for s in validate_security_master(securities)], ["600000"])

    def test_event_rule_evidence(self):
        event = MarketEvent("e1", "rss", "Nvidia announces new GPU", "2026-01-01")
        evidence = discover_sector_evidence(event)
        self.assertTrue(any(e.target_code == "sector:半导体" for e in evidence))
        self.assertTrue(all(not e.verified for e in evidence))

    def test_ranking_prefers_activity_and_direct_relation(self):
        security = Security("600000", "示例")
        direct = [MappingEvidence("600000", "direct_business", .9, "source reviewed", verified=True)]
        quote = QuoteSnapshot("600000", "2026-01-01T14:33:00+08:00", change_pct=4.2,
                              turnover=500_000_000, volume_ratio=2.1)
        candidate = score_candidate(security, direct, quote)
        self.assertEqual(candidate.tier, "A")
        self.assertEqual(candidate.causal_status, "verified")
        self.assertGreater(candidate.score, 50)
        self.assertEqual(rank_candidates([candidate], 10)[0].security.code, "600000")

    def test_scan_uses_persisted_verified_relations(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CrossMarketRelationStore(str(Path(temp) / "market.db"))
            relation_id = store.propose(us_entity="NVIDIA", us_ticker="NVDA", cn_security_code="688012",
                sector_code="sector:半导体", mapping_type="supply_chain", direction="positive", confidence=.88,
                evidence="reviewed supplier relation", source="filing", source_url="https://evidence.example/1")
            store.review(relation_id, "verified", "reviewer", "source reviewed")
            fetcher = StubQuoteFetcher()
            radar = MarketRadar(fetcher, relation_store=store)
            event = MarketEvent("e1", "SEC EDGAR", "NVIDIA reports GPU demand", "2026-11-09T20:00:00+08:00")
            securities = {"688012": Security("688012", "中微公司")}
            results = radar.scan([event], securities)
            self.assertEqual([item.security.code for item in results], ["688012"])
            self.assertEqual(results[0].tier, "B")
            self.assertEqual(results[0].causal_status, "verified")
            self.assertEqual(fetcher.requested, ["688012"])

    def test_scan_does_not_use_pending_relation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CrossMarketRelationStore(str(Path(temp) / "market.db"))
            store.propose(us_entity="NVIDIA", us_ticker="NVDA", cn_security_code="688012",
                sector_code="sector:半导体", mapping_type="supply_chain", direction="positive", confidence=.88,
                evidence="unreviewed relation", source="filing", source_url="https://evidence.example/1")
            fetcher = StubQuoteFetcher()
            radar = MarketRadar(fetcher, relation_store=store)
            event = MarketEvent("e1", "SEC EDGAR", "NVIDIA reports GPU demand", "2026-11-09T20:00:00+08:00")
            results = radar.scan([event], {"688012": Security("688012", "中微公司")})
            self.assertEqual(results, [])
            self.assertEqual(fetcher.requested, [])


if __name__ == "__main__":
    unittest.main()
