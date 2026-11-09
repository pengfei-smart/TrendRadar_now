# 本文件验证跨市场关系库提议、审核状态流转、审计记录及可信关系过滤。
"""Unit tests for cross-market relation persistence and review."""

import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.relation_store import CrossMarketRelationStore


class RelationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = CrossMarketRelationStore(str(Path(self.temp.name) / "market.db"))

    def tearDown(self):
        self.temp.cleanup()

    def propose(self, url="https://evidence.example/relation"):
        return self.store.propose(
            us_entity="NVIDIA", us_ticker="nvda", cn_security_code="688012",
            mapping_type="supply_chain", direction="positive", confidence=0.83,
            evidence="sector:半导体; reviewed supplier relationship", source="company filing",
            source_url=url,
        )

    def test_proposal_defaults_to_pending(self):
        relation_id = self.propose()
        rows = self.store.list_by_status("pending")
        self.assertEqual([row["relation_id"] for row in rows], [relation_id])

    def test_duplicate_proposal_is_idempotent_and_not_auto_verified(self):
        relation_id = self.propose()
        second_id = self.propose()
        self.assertEqual(relation_id, second_id)
        self.assertEqual(len(self.store.list_by_status("pending")), 1)

    def test_review_verified_creates_audit_and_trusted_relation(self):
        relation_id = self.propose()
        self.store.review(relation_id, "verified", "reviewer-1", "evidence checked")
        self.assertEqual(self.store.list_by_status("verified")[0]["reviewer"], "reviewer-1")
        trusted = self.store.trusted_by_sector("半导体")
        self.assertEqual(len(trusted), 1)
        self.assertTrue(trusted[0].verified)
        with self.store.connect() as conn:
            audit = conn.execute("SELECT old_status,new_status,note FROM cross_market_map_audit").fetchone()
        self.assertEqual(tuple(audit), ("pending", "verified", "evidence checked"))

    def test_rejected_relation_is_not_trusted(self):
        relation_id = self.propose()
        self.store.review(relation_id, "rejected", "reviewer-1", "not substantiated")
        self.assertEqual(self.store.trusted_by_sector("半导体"), [])

    def test_invalid_confidence_and_review_status_rejected(self):
        with self.assertRaises(ValueError):
            self.store.propose(us_entity="NVDA", us_ticker="NVDA", cn_security_code="688012",
                mapping_type="supply_chain", direction="positive", confidence=1.1,
                evidence="sector:半导体")
        relation_id = self.propose()
        with self.assertRaises(ValueError):
            self.store.review(relation_id, "approved", "reviewer-1")

    def test_missing_relation_review_raises(self):
        with self.assertRaises(LookupError):
            self.store.review(999, "verified", "reviewer-1")


if __name__ == "__main__":
    unittest.main()
