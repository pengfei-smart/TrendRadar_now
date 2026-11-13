# 本文件验证NEEQ候选审核工具的筛选、字段展示及审核边界。
"""Tests for the NEEQ lifecycle candidate review CLI helpers."""

import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.neeq_lifecycle_review import list_candidates, review_candidate
from trendradar.market_data.storage import MarketStorage


class NEEQLifecycleReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.tmp.name) / "market.db"))
        self.storage.save_neeq_issuer_identity_candidates([{
            "identity_candidate_id": "neeq-id-test-000001",
            "company_code": "870099",
            "company_name": "华搜股份",
            "disclosure_title": "摘牌整理期风险提示",
            "publish_date": "2026-11-13",
            "attachment_url": "https://www.neeq.com.cn/disclosure/test.pdf",
            "metadata_source_url": "https://www.neeq.com.cn/disclosureInfoController/companyAnnouncement.do",
            "metadata_response_sha256": "a" * 64,
            "retrieved_at": "2026-11-13T20:00:00+08:00",
            "raw_record": {"companyCd": "870099"},
        }])

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_identity_candidate_and_raw_metadata(self):
        result = list_candidates(self.storage, target="identity", company_code="870099")
        self.assertEqual(len(result["identity_candidates"]), 1)
        self.assertEqual(result["identity_candidates"][0]["raw_record"]["companyCd"], "870099")
        self.assertEqual(result["lifecycle_candidates"], [])

    def test_reviews_identity_candidate_without_promoting_master(self):
        result = review_candidate(
            self.storage, target="identity", candidate_id="neeq-id-test-000001",
            review_status="verified", reviewer="tester", review_notes="官方元数据与附件路径一致",
            reviewed_at="2026-11-13T21:00:00+08:00",
        )
        self.assertEqual(result["review_status"], "verified")
        row = self.storage.list_neeq_issuer_identity_candidates(company_code="870099")[0]
        self.assertEqual(row["review_status"], "verified")
        self.assertEqual(row["review_notes"], "官方元数据与附件路径一致")
        self.assertIsNone(self.storage.get_neeq_issuer("870099"))

    def test_rejects_invalid_target_and_empty_reviewer(self):
        with self.assertRaises(ValueError):
            review_candidate(
                self.storage, target="master", candidate_id="x",
                review_status="verified", reviewer="tester",
            )
        with self.assertRaises(ValueError):
            review_candidate(
                self.storage, target="identity", candidate_id="x",
                review_status="verified", reviewer=" ",
            )


if __name__ == "__main__":
    unittest.main()
