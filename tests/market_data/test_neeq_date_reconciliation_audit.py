# 本文件测试NEEQ日期差异核验和补充候选证据整理的只读边界。
"""Tests for NEEQ date reconciliation audit."""

import unittest

from trendradar.market_data.neeq_date_reconciliation_audit import build_neeq_date_reconciliation_audit


class NEEQDateReconciliationAuditTests(unittest.TestCase):
    def test_preserves_notice_and_official_dates_without_overwriting(self):
        review = {
            "parser_version": "v8",
            "items": [{
                "url": "https://www.neeq.com.cn/example.html",
                "notices": [{
                    "security_code": "831479",
                    "document_number": "2020-990",
                    "decision_date": "2021-01-05",
                    "announcement_date": "2021-01-05",
                    "stated_termination_date": "2021-01-15",
                    "short_name": "湘联股份",
                }],
                "comparisons": [{
                    "security_code": "831479",
                    "notice_document_number": "2020-990",
                    "notice_planned_termination_date": "2021-01-15",
                    "result_list_date": "2021-03-22",
                    "date_consistency_status": "mismatch_requires_follow_up",
                    "record_id": "1016",
                    "issues": ["result_date_vs_planned_date_mismatch"],
                }],
            }],
        }
        result = build_neeq_date_reconciliation_audit(review, {"items": []}, {"tasks": []})
        row = result["date_reconciliation_cases"][0]
        self.assertEqual(row["notice_planned_termination_date"], "2021-01-15")
        self.assertEqual(row["official_result_delisting_date"], "2021-03-22")
        self.assertEqual(row["date_difference_days"], 66)
        self.assertEqual(row["date_reconciliation_status"], "later_than_planned")
        self.assertEqual(row["notice_decision_date"], "2021-01-05")
        self.assertEqual(result["safety"]["database_writes"], 0)

    def test_creates_supplemental_candidate_without_mutating_manifest(self):
        coverage = {"items": [{
            "url": "https://www.neeq.com.cn/notice-2019-987.html",
            "notices": [{
                "security_code": "837689",
                "short_name": "圣保堂",
                "document_number": "2019-987",
                "coverage_classification": "candidate_absent_from_manifest",
            }],
        }]}
        manifest = {"tasks": []}
        result = build_neeq_date_reconciliation_audit({"items": []}, coverage, manifest)
        row = result["supplemental_candidate_evidence"][0]
        self.assertEqual(row["security_code"], "837689")
        self.assertEqual(row["notice_document_number"], "2019-987")
        self.assertFalse(row["confirmed_legal_fact"])
        self.assertFalse(row["formal_lifecycle_write_allowed"])
        self.assertEqual(manifest, {"tasks": []})

    def test_deduplicates_same_notice_identity_and_source(self):
        row = {"security_code": "837689", "document_number": "2019-987",
               "coverage_classification": "candidate_absent_from_manifest"}
        coverage = {"items": [{"url": "https://x/a", "notices": [row, row]}]}
        result = build_neeq_date_reconciliation_audit({}, coverage, {})
        self.assertEqual(result["summary"]["supplemental_candidate_count"], 1)


    def test_excludes_fully_matched_dates_and_keeps_missing_plan_for_review(self):
        review = {"items": [{
            "notices": [],
            "comparisons": [
                {"security_code": "111111", "notice_document_number": "N1",
                 "notice_planned_termination_date": "2020-01-10",
                 "result_list_date": "2020-01-10", "date_consistency_status": "matches"},
                {"security_code": "222222", "notice_document_number": "N2",
                 "notice_planned_termination_date": None,
                 "result_list_date": "2020-01-10", "date_consistency_status": "not_comparable"},
            ],
        }]}
        result = build_neeq_date_reconciliation_audit(review, {}, {})
        self.assertEqual(result["summary"]["date_case_count"], 1)
        self.assertEqual(result["summary"]["incomplete_date_evidence_count"], 1)
        self.assertEqual(result["date_reconciliation_cases"][0]["security_code"], "222222")
        self.assertEqual(result["date_reconciliation_cases"][0]["date_reconciliation_status"], "pending_manual_review")

    def test_empty_inputs_are_safe(self):
        result = build_neeq_date_reconciliation_audit({}, {}, {})
        self.assertEqual(result["summary"]["date_case_count"], 0)
        self.assertTrue(result["safety"]["read_only"])


if __name__ == "__main__":
    unittest.main()
