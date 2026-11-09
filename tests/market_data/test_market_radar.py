# 本文件测试证券过滤、映射分层与雷达评分排序，属于逐文件开发清单第八项。
"""Unit tests for market radar primitives."""

import unittest

from trendradar.market_data.models import Security, QuoteSnapshot, MappingEvidence, MarketEvent
from trendradar.market_data.security_master import normalize_security_code, validate_security_master
from trendradar.market_data.cross_market_mapper import discover_sector_evidence
from trendradar.market_data.opportunity_ranker import score_candidate, rank_candidates


class MarketRadarTests(unittest.TestCase):
    def test_normalize_code(self):
        self.assertEqual(normalize_security_code("600000.SH"), "600000")
        self.assertEqual(normalize_security_code("1"), "000001")
        with self.assertRaises(ValueError):
            normalize_security_code("bad-code")

    def test_filters_risk_and_inactive(self):
        securities = [
            Security("600000", "浦发银行"),
            Security("600001", "风险股", risk_flag=True),
            Security("600002", "停牌", listing_status="suspended"),
        ]
        self.assertEqual([s.code for s in validate_security_master(securities)], ["600000"])

    def test_event_rule_evidence(self):
        event = MarketEvent("e1", "rss", "Nvidia announces new GPU", "2026-01-01")
        evidence = discover_sector_evidence(event)
        self.assertTrue(any(e.target_code == "sector:半导体" for e in evidence))
        self.assertTrue(all(not e.verified for e in evidence))

    def test_ranking_prefers_activity_and_direct_relation(self):
        security = Security("600000", "示例")
        direct = [MappingEvidence("600000", "direct_business", .9, "source reviewed", verified=True)]
        quote = QuoteSnapshot("600000", "2026-01-01T14:33:00+08:00", change_pct=4.2, turnover=500_000_000, volume_ratio=2.1)
        candidate = score_candidate(security, direct, quote)
        self.assertEqual(candidate.tier, "A")
        self.assertEqual(candidate.causal_status, "verified")
        self.assertGreater(candidate.score, 50)
        self.assertEqual(rank_candidates([candidate], 10)[0].security.code, "600000")


if __name__ == "__main__":
    unittest.main()
