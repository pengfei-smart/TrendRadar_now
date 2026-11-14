# 本文件测试NEEQ公告候选关联覆盖审计的分类与只读安全边界。
"""Tests for NEEQ notice candidate coverage audit."""

import unittest

from trendradar.market_data.neeq_notice_coverage_audit import build_neeq_notice_coverage_audit


class NEEQNoticeCoverageAuditTests(unittest.TestCase):
    def test_classifies_local_global_missing_and_unresolved_identities(self):
        manifest = {"tasks": [
            {"attachment_id": "a", "url": "https://x/a", "linked_candidates": [{"company_code": "111111", "record_id": "1"}]},
            {"attachment_id": "b", "url": "https://x/b", "linked_candidates": [{"company_code": "222222", "record_id": "2"}]},
        ]}
        review = {"items": [{"attachment_id": "a", "url": "https://x/a", "notices": [
            {"security_code": "111111", "short_name": "甲", "identity_source": "html_attachment_table"},
            {"security_code": "222222", "short_name": "乙", "identity_source": "html_attachment_table"},
            {"security_code": "333333", "short_name": "丙", "identity_source": "html_attachment_table"},
            {"security_code": None, "identity_source": "unresolved"},
        ]}]}
        result = build_neeq_notice_coverage_audit(review, manifest)
        counts = result["coverage_classification_counts"]
        self.assertEqual(counts["linked_to_this_attachment"], 1)
        self.assertEqual(counts["candidate_exists_on_other_attachment"], 1)
        self.assertEqual(counts["candidate_absent_from_manifest"], 1)
        self.assertEqual(counts["identity_unresolved"], 1)
        self.assertEqual(result["safety"]["database_writes"], 0)
        self.assertFalse(result["items"][0]["notices"][0]["formal_lifecycle_write_allowed"])

    def test_empty_report_is_safe(self):
        result = build_neeq_notice_coverage_audit({"items": []}, {"tasks": []})
        self.assertEqual(result["notice_count"], 0)
        self.assertTrue(result["safety"]["read_only"])


if __name__ == "__main__":
    unittest.main()


    def test_v2_reports_reverse_candidate_gaps_and_duplicate_notice_codes(self):
        manifest = {"tasks": [{
            "attachment_id": "a",
            "url": "https://x/a",
            "linked_candidates": [
                {"company_code": "111111", "record_id": "1", "company_name": "甲公司"},
                {"company_code": "222222", "record_id": "2", "company_name": "乙公司"},
                {"company_code": "222222", "record_id": "3", "company_name": "乙公司"},
            ],
        }]}
        review = {"items": [{
            "attachment_id": "a",
            "url": "https://x/a",
            "notices": [
                {"security_code": "111111", "short_name": "甲"},
                {"security_code": "111111", "short_name": "甲"},
            ],
        }]}
        result = build_neeq_notice_coverage_audit(review, manifest)
        item = result["items"][0]
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["distinct_notice_security_code_count"], 1)
        self.assertEqual(result["repeated_notice_code_occurrence_count"], 1)
        self.assertEqual(item["repeated_notice_codes"][0]["occurrence_count"], 2)
        self.assertEqual(item["linked_candidate_codes_without_notice"], ["222222"])
        self.assertEqual(item["candidate_codes_without_notice"][0]["candidate_occurrence_count"], 2)
        self.assertFalse(item["candidate_codes_without_notice"][0]["formal_lifecycle_write_allowed"])
