# 本文件测试市场雷达与持久化可信关系库的集成、候选过滤、评分排序及行情门面返回契约。
"""Unit tests for market radar orchestration with relation/result-store integration."""

import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.models import Security, QuoteSnapshot, MappingEvidence, MarketEvent
from trendradar.market_data.market_radar import MarketRadar
from trendradar.market_data.relation_store import CrossMarketRelationStore
from trendradar.market_data.result_store import RadarResultStore
from trendradar.market_data.security_master import normalize_security_code, validate_security_master
from trendradar.market_data.cross_market_mapper import discover_sector_evidence
from trendradar.market_data.opportunity_ranker import score_candidate, rank_candidates


class StubQuoteFetcher:
    source_name = "stub"
    def __init__(self): self.requested = []
    def fetch(self, codes):
        self.requested = [item.code for item in codes]
        quotes = [QuoteSnapshot(code, "2026-11-09T14:33:00+08:00", change_pct=2.0, turnover=200_000_000, volume_ratio=1.5, source="stub") for code in self.requested]
        return quotes, "stub"


class MarketRadarTests(unittest.TestCase):
    def test_normalize_code(self):
        self.assertEqual(normalize_security_code("600000.SH"), "600000")
        self.assertEqual(normalize_security_code("1"), "000001")
        with self.assertRaises(ValueError): normalize_security_code("bad-code")

    def test_filters_risk_and_inactive(self):
        securities = [Security("600000", "浦发银行"), Security("600001", "风险股", risk_flag=True), Security("600002", "停牌", listing_status="suspended")]
        self.assertEqual([s.code for s in validate_security_master(securities)], ["600000"])

    def test_event_rule_evidence(self):
        evidence = discover_sector_evidence(MarketEvent("e1", "rss", "Nvidia announces new GPU", "2026-01-01"))
        self.assertTrue(any(e.target_code == "sector:半导体" for e in evidence))
        self.assertTrue(all(not e.verified for e in evidence))

    def test_ranking_prefers_activity_and_direct_relation(self):
        security = Security("600000", "示例")
        direct = [MappingEvidence("600000", "direct_business", .9, "source reviewed", verified=True)]
        quote = QuoteSnapshot("600000", "2026-01-01T14:33:00+08:00", change_pct=4.2, turnover=500_000_000, volume_ratio=2.1)
        candidate = score_candidate(security, direct, quote)
        self.assertEqual(candidate.tier, "A"); self.assertEqual(candidate.causal_status, "verified")
        self.assertGreater(candidate.score, 50); self.assertEqual(rank_candidates([candidate], 10)[0].security.code, "600000")

    def test_scan_uses_persisted_verified_relations(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CrossMarketRelationStore(str(Path(temp) / "market.db"))
            relation_id = store.propose(us_entity="NVIDIA", us_ticker="NVDA", cn_security_code="688012", sector_code="sector:半导体", mapping_type="supply_chain", direction="positive", confidence=.88, evidence="reviewed supplier relation", source="filing", source_url="https://evidence.example/1")
            store.review(relation_id, "verified", "reviewer", "source reviewed")
            fetcher = StubQuoteFetcher(); radar = MarketRadar(fetcher, relation_store=store)
            event = MarketEvent("e1", "SEC EDGAR", "NVIDIA reports GPU demand", "2026-11-09T20:00:00+08:00")
            results = radar.scan([event], {"688012": Security("688012", "中微公司")})
            self.assertEqual([item.security.code for item in results], ["688012"])
            self.assertEqual(results[0].tier, "B"); self.assertEqual(results[0].causal_status, "verified")
            self.assertEqual(fetcher.requested, ["688012"])

    def test_scan_does_not_use_pending_relation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CrossMarketRelationStore(str(Path(temp) / "market.db"))
            store.propose(us_entity="NVIDIA", us_ticker="NVDA", cn_security_code="688012", sector_code="sector:半导体", mapping_type="supply_chain", direction="positive", confidence=.88, evidence="unreviewed relation", source="filing", source_url="https://evidence.example/1")
            fetcher = StubQuoteFetcher(); radar = MarketRadar(fetcher, relation_store=store)
            event = MarketEvent("e1", "SEC EDGAR", "NVIDIA reports GPU demand", "2026-11-09T20:00:00+08:00")
            self.assertEqual(radar.scan([event], {"688012": Security("688012", "中微公司")}), [])
            self.assertEqual(fetcher.requested, [])

    def test_scan_and_persist_saves_all_ranked_candidates_and_push_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            store = RadarResultStore(str(Path(temp) / "market.db")); fetcher = StubQuoteFetcher()
            radar = MarketRadar(fetcher, push_limit=1, result_store=store)
            event = MarketEvent("e1", "rss", "Nvidia announces GPU", "2026-11-09T14:33:00+08:00")
            securities = {"688012": Security("688012", "半导体A"), "688013": Security("688013", "半导体B")}
            direct = {"e1": [MappingEvidence("688012", "direct_business", .9, "direct evidence", verified=True), MappingEvidence("688013", "supply_chain", .7, "supplier evidence", verified=True)]}
            selected = radar.scan_and_persist([event], securities, run_at="2026-11-09T14:33:00+08:00", direct_event_evidence=direct, source_status="quote:success", quote_source="stub")
            self.assertEqual(len(selected), 1)
            with store.connect() as conn:
                run = conn.execute("SELECT candidate_count,pushed_count,status,quote_source FROM radar_runs").fetchone()
                rows = conn.execute("SELECT code,pushed FROM radar_candidates ORDER BY score DESC").fetchall()
            self.assertEqual(tuple(run), (2, 1, "success", "stub")); self.assertEqual(len(rows), 2)
            self.assertEqual(sum(row["pushed"] for row in rows), 1)


if __name__ == "__main__":
    unittest.main()
