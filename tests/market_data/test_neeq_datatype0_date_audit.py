# 测试 NEEQ dataType=0 日期对照审计及只读边界。
# -*- coding: utf-8 -*-
"""测试 NEEQ dataType=0 日期对照报告。"""
import unittest

from scripts.market_data.audit_neeq_datatype0_date_audit import build_report


class NeeqDataType0DateAuditTests(unittest.TestCase):
    def _candidate(self, **overrides):
        item = {
            "record_id": "841",
            "security_code": "838047",
            "company_name": "二中科技",
            "interface_result_date": "2019-09-06",
            "interface_create_time_epoch_ms": 1582277026000,
            "batch_relation_status": "formal_decision_membership_found",
            "batch_memberships": [
                {
                    "membership_type": "formal_decision_company",
                    "batch_notice_number": "2019-984",
                    "formal_decision_date": "2019-07-01",
                    "conditional_termination_date": "2019-07-09",
                    "source_attachment_url": "https://www.neeq.com.cn/important_news/200006230.html",
                    "review_status": "pending_manual_review",
                    "confirmed_legal_fact": False,
                }
            ],
            "supplemental_independent_decisions": [],
            "raw_record_sha256": "a" * 64,
        }
        item.update(overrides)
        return item

    def test_reports_date_delta_without_promoting_interface_date(self):
        report = build_report({
            "report_version": "neeq_datatype0_notice_crosscheck_v1",
            "candidates": [self._candidate()],
        })
        row = report["candidates"][0]
        self.assertEqual(row["comparison_status"], "interface_date_differs_from_conditional_termination_date")
        self.assertEqual(row["interface_date_minus_conditional_date_days"]["2019-07-09"], 59)
        self.assertEqual(row["interface_create_date_china"], "2020-02-21")
        self.assertEqual(row["interface_date_minus_create_date_days"], -168)
        self.assertIsNone(row["actual_termination_date"])
        self.assertFalse(row["actual_termination_date_confirmed"])

    def test_matches_conditional_date_but_does_not_confirm_actual_date(self):
        candidate = self._candidate(
            security_code="430136",
            interface_result_date="2020-09-17",
            interface_create_time_epoch_ms=1600279200000,
            batch_memberships=[],
            supplemental_independent_decisions=[{
                "notice_number": "2020-697",
                "decision_date": "2020-09-07",
                "conditional_termination_date": "2020-09-17",
                "source_url": "https://www.neeq.com.cn/example.pdf",
                "evidence_status": "official_notice_crosschecked",
            }],
        )
        report = build_report({"candidates": [candidate]})
        row = report["candidates"][0]
        self.assertEqual(row["comparison_status"], "interface_date_matches_conditional_termination_date")
        self.assertEqual(row["decision_dates"], ["2020-09-07"])
        self.assertIsNone(row["actual_termination_date"])
        self.assertEqual(report["summary"]["actual_termination_dates_confirmed"], 0)

    def test_missing_decision_evidence_is_not_treated_as_no_notice(self):
        candidate = self._candidate(batch_memberships=[], supplemental_independent_decisions=[])
        report = build_report({"candidates": [candidate]})
        self.assertEqual(
            report["candidates"][0]["comparison_status"],
            "no_parsed_decision_date_evidence",
        )
        self.assertIn("未匹配到已解析公告不代表历史上不存在公告", report["candidates"][0]["notes"])

    def test_safety_boundary_is_read_only(self):
        report = build_report({"candidates": [self._candidate()]})
        self.assertTrue(report["safety"]["read_only"])
        self.assertEqual(report["safety"]["database_writes"], 0)
        self.assertEqual(report["safety"]["formal_lifecycle_writes"], 0)
        self.assertFalse(report["safety"]["data_type_semantics_inferred"])
        self.assertFalse(report["safety"]["interface_date_promoted_to_actual_termination_date"])


if __name__ == "__main__":
    unittest.main()
