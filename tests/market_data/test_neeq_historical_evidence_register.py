# 本文件测试NEEQ历史证据原件登记、SHA256校验、日期主张留痕及只读安全边界。
"""Tests for NEEQ historical evidence register."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.neeq_historical_evidence_register import (
    build_neeq_historical_evidence_register,
)


class NEEQHistoricalEvidenceRegisterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "2017-684").mkdir()
        self.payload = b"%PDF-1.7\noriginal notice"
        (self.root / "2017-684" / "notice.pdf").write_bytes(self.payload)
        self.sha = hashlib.sha256(self.payload).hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def tracker(self):
        return {
            "tracker_version": "neeq_followup_evidence_tracker_v1",
            "tasks": [{
                "task_id": "case-834502",
                "security_code": "834502",
                "company_short_name": "富海银涛",
                "notice_document_number": "2017-684",
                "initial_notice_source_url": "https://www.neeq.com.cn/notice.pdf",
            }],
        }

    def record(self, **overrides):
        row = {
            "security_code": "834502",
            "notice_document_number": "2017-684",
            "evidence_type": "final_termination_announcement",
            "source_title": "终止挂牌公告",
            "source_url": "https://www.neeq.com.cn/notice.pdf",
            "source_document_number": "2017-684",
            "published_date": "2017-12-12",
            "source_file": "2017-684/notice.pdf",
            "source_sha256": self.sha,
            "date_assertions": [{
                "date_type": "formal_decision_date",
                "date_value": "2017-12-12",
                "source_quote": "公司于2017年12月12日收到决定",
                "page_number": 1,
            }],
        }
        row.update(overrides)
        return row

    def test_valid_original_and_date_assertion_are_registered_pending_review(self):
        result = build_neeq_historical_evidence_register(
            self.tracker(), [self.record()], evidence_root=self.root
        )
        item = result["dossiers"][0]["evidence_records"][0]
        self.assertEqual(item["file_integrity_status"], "sha256_match")
        self.assertEqual(item["source_sha256"], self.sha)
        self.assertEqual(item["review_status"], "evidence_integrity_complete_pending_manual_review")
        self.assertEqual(item["date_assertions"][0]["date_value"], "2017-12-12")
        self.assertFalse(item["confirmed_legal_fact"])

    def test_accepts_distinct_transfer_suspension_and_resumption_date_types(self):
        assertions = [
            {
                "date_type": "transfer_suspension_date",
                "date_value": "2017-12-13",
                "source_quote": "公司股票自2017年12月13日（周三）开市起暂停转让",
                "page_number": 1,
            },
            {
                "date_type": "transfer_resumption_date",
                "date_value": "2017-12-14",
                "source_quote": "银纪资产股票于2017年12月14日（星期四）起恢复转让",
                "page_number": 2,
            },
        ]
        result = build_neeq_historical_evidence_register(
            self.tracker(), [self.record(date_assertions=assertions)], evidence_root=self.root
        )
        item = result["dossiers"][0]["evidence_records"][0]
        self.assertTrue(all(row["date_type_valid"] for row in item["date_assertions"]))
        self.assertEqual(item["review_status"], "evidence_integrity_complete_pending_manual_review")

    def test_source_document_number_is_optional_for_un_numbered_sponsor_notice(self):
        result = build_neeq_historical_evidence_register(
            self.tracker(), [self.record(source_document_number=None)], evidence_root=self.root
        )
        item = result["dossiers"][0]["evidence_records"][0]
        self.assertEqual(item["review_status"], "evidence_integrity_complete_pending_manual_review")
        self.assertIsNone(item["source_document_number"])

    def test_detects_declared_sha256_mismatch(self):
        result = build_neeq_historical_evidence_register(
            self.tracker(), [self.record(source_sha256="0" * 64)], evidence_root=self.root
        )
        item = result["dossiers"][0]["evidence_records"][0]
        self.assertEqual(item["file_integrity_status"], "sha256_mismatch")
        self.assertEqual(item["review_status"], "file_integrity_mismatch")

    def test_rejects_path_traversal_outside_evidence_root(self):
        result = build_neeq_historical_evidence_register(
            self.tracker(), [self.record(source_file="../outside.pdf")], evidence_root=self.root
        )
        item = result["dossiers"][0]["evidence_records"][0]
        self.assertEqual(item["file_integrity_status"], "unsafe_or_missing_relative_path")

    def test_missing_source_file_is_not_treated_as_verified(self):
        result = build_neeq_historical_evidence_register(
            self.tracker(), [self.record(source_file="missing.pdf")], evidence_root=self.root
        )
        self.assertEqual(result["dossiers"][0]["evidence_records"][0]["file_integrity_status"], "source_file_missing")

    def test_invalid_date_or_missing_quote_stays_incomplete(self):
        result = build_neeq_historical_evidence_register(
            self.tracker(), [self.record(date_assertions=[{
                "date_type": "formal_decision_date",
                "date_value": "2017-02-31",
                "source_quote": "",
            }])], evidence_root=self.root
        )
        item = result["dossiers"][0]["evidence_records"][0]
        self.assertEqual(item["review_status"], "date_assertion_incomplete_pending_review")
        self.assertFalse(item["date_assertions"][0]["date_value_valid"])

    def test_rejects_non_https_source_url_as_incomplete_metadata(self):
        result = build_neeq_historical_evidence_register(
            self.tracker(), [self.record(source_url="http://example.com/notice.pdf")], evidence_root=self.root
        )
        item = result["dossiers"][0]["evidence_records"][0]
        self.assertFalse(item["source_url_is_https"])
        self.assertEqual(item["review_status"], "source_metadata_incomplete")

    def test_keeps_unmatched_security_notice_pair_unlinked(self):
        result = build_neeq_historical_evidence_register(
            self.tracker(), [self.record(security_code="834606")], evidence_root=self.root
        )
        self.assertEqual(result["summary"]["unlinked_evidence_count"], 1)
        self.assertEqual(result["dossiers"][0]["evidence_count"], 0)

    def test_deduplicates_same_original_and_evidence_type(self):
        row = self.record()
        result = build_neeq_historical_evidence_register(
            self.tracker(), [row, row], evidence_root=self.root
        )
        self.assertEqual(result["summary"]["evidence_record_count"], 1)

    def test_input_claims_cannot_promote_legal_fact_or_write_permission(self):
        result = build_neeq_historical_evidence_register(
            self.tracker(), [self.record(confirmed_legal_fact=True, formal_lifecycle_write_allowed=True)],
            evidence_root=self.root,
        )
        item = result["dossiers"][0]["evidence_records"][0]
        self.assertFalse(item["confirmed_legal_fact"])
        self.assertFalse(item["formal_lifecycle_write_allowed"])
        self.assertTrue(result["safety"]["read_only"])


if __name__ == "__main__":
    unittest.main()
