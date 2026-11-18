# 本文件测试市场雷达运行记录与候选历史的持久化、证据序列化和推送计数。
"""Unit tests for radar result persistence."""

import sqlite3
import tempfile
from contextlib import closing
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
        self.assertEqual(result["candidates"][0]["quote_timestamp_quality"], "legacy_unknown")
        self.assertEqual(result["candidates"][0]["quote_source_timestamp"], "")

    def test_candidate_persists_quote_timestamp_provenance(self):
        run_id = self.store.start_run("2026-11-09T14:33:00+08:00")
        candidate = self.candidate("600000")
        candidate = Candidate(
            security=candidate.security, tier=candidate.tier, score=candidate.score,
            reasons=candidate.reasons, evidence=candidate.evidence,
            quote=QuoteSnapshot(
                "600000", "2026-11-09T14:33:00+08:00",
                source_timestamp="2026-11-09T14:33:00+08:00",
                timestamp_quality="provider", source="stub",
            ),
            causal_status=candidate.causal_status,
        )
        self.store.finish_run(run_id, [candidate], status="success")
        row = self.store.get_run(run_id)["candidates"][0]
        self.assertEqual(row["quote_source_timestamp"], "2026-11-09T14:33:00+08:00")
        self.assertEqual(row["quote_timestamp_quality"], "provider")

    def test_existing_result_database_migrates_quote_timestamp_audit_columns(self):
        legacy_path = Path(self.temp.name) / "legacy.db"
        with closing(sqlite3.connect(legacy_path)) as conn:
            conn.execute("""CREATE TABLE radar_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                run_type TEXT NOT NULL DEFAULT 'snapshot',
                status TEXT NOT NULL DEFAULT 'started',
                candidate_count INTEGER NOT NULL DEFAULT 0,
                pushed_count INTEGER NOT NULL DEFAULT 0,
                source_status TEXT NOT NULL DEFAULT '',
                quote_source TEXT NOT NULL DEFAULT '',
                quote_data_at TEXT NOT NULL DEFAULT '',
                duration_ms INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT ''
            )""")
            conn.execute("""CREATE TABLE radar_candidates (
                run_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                tier TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0,
                causal_status TEXT NOT NULL DEFAULT 'unverified',
                reasons TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                quote_timestamp TEXT NOT NULL DEFAULT '',
                pushed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(run_id,code)
            )""")
            conn.execute(
                "INSERT INTO radar_runs(run_id,run_at,status,candidate_count) VALUES(1,?, 'success', 1)",
                ("2026-11-09T14:33:00+08:00",),
            )
            conn.execute(
                """INSERT INTO radar_candidates
                (run_id,code,tier,score,quote_timestamp)
                VALUES(1,'600000','B',66.5,'2026-11-09T14:33:00+08:00')"""
            )
            conn.commit()

        migrated = RadarResultStore(str(legacy_path))
        row = migrated.get_run(1)["candidates"][0]
        self.assertEqual(row["quote_timestamp"], "2026-11-09T14:33:00+08:00")
        self.assertEqual(row["quote_source_timestamp"], "")
        self.assertEqual(row["quote_timestamp_quality"], "legacy_unknown")

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
