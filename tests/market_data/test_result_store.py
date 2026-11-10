# 本文件测试市场雷达运行记录与候选历史的持久化、证据序列化和推送计数。
"""Unit tests for radar result persistence."""

import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.models import Candidate, Security, QuoteSnapshot, MappingEvidence
from trendradar.market_data.result_store import RadarResultStore


class RadarResultStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RadarResultStore(str(Path(self.temp.name) / "market.db"))

    def tearDown(self):
        self.temp.cleanup()

    def candidate(self, code, tier="B", score=66.5, verified=False):
        return Candidate(
            security=Security(code, "测试证券"), tier=tier, score=score,
            reasons=["产业链关联候选", "成交活跃度加分"],
            evidence=[MappingEvidence(code, "supply_chain", .8, "supplier evidence",
                source="filing", source_url="https://evidence.example", direction="positive", verified=verified)],
            quote=QuoteSnapshot(code, "2026-11-09T14:33:00+08:00", change_pct=2.1,
                turnover=100_000_000, volume_ratio=1.4, source="stub", delayed=True),
            causal_status="verified" if verified else "unverified",
        )

    def test_run_persists_all_candidates_and_evidence(self):
        run_id = self.store.start_run("2026-11-09T14:33:00+08:00")
        candidates = [self.candidate("688012", verified=True), self.candidate("600000", tier="C", score=42)]
        self.store.finish_run(run_id, candidates, status="success", pushed_codes=["688012"],
            source_status="sec:success;quote:success", quote_source="stub",
            quote_data_at="2026-11-09T14:33:00+08:00", duration_ms=123)
        result = self.store.get_run(run_id)
        self.assertEqual(result["run"]["candidate_count"], 2)
        self.assertEqual(result["run"]["pushed_count"], 1)
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["candidates"][0]["code"], "688012")
        self.assertIn("source_url", result["candidates"][0]["evidence_json"])
        self.assertEqual(result["candidates"][0]["pushed"], 1)
        self.assertEqual(result["candidates"][1]["pushed"], 0)

    def test_failed_run_can_be_persisted_without_candidates(self):
        run_id = self.store.start_run("2026-11-09T14:33:00+08:00", notes="quote provider unavailable")
        self.store.finish_run(run_id, [], status="failed", source_status="quote:failed", notes="provider timeout")
        result = self.store.get_run(run_id)
        self.assertEqual(result["run"]["status"], "failed")
        self.assertEqual(result["run"]["candidate_count"], 0)

    def test_invalid_status_and_missing_run_raise(self):
        run_id = self.store.start_run("2026-11-09T14:33:00+08:00")
        with self.assertRaises(ValueError):
            self.store.finish_run(run_id, [], status="ok")
        with self.assertRaises(LookupError):
            self.store.finish_run(999, [], status="success")

    def test_push_count_only_counts_candidates_in_this_run(self):
        run_id = self.store.start_run("2026-11-09T14:33:00+08:00")
        self.store.finish_run(run_id, [self.candidate("688012")], status="success", pushed_codes=["688012", "000001"])
        self.assertEqual(self.store.get_run(run_id)["run"]["pushed_count"], 1)


if __name__ == "__main__":
    unittest.main()
