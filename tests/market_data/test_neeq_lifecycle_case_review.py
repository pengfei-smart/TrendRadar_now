# 本文件测试NEEQ生命周期证据复核报告的聚合、缺口识别和只读安全边界。
"""Tests for NEEQ lifecycle case review."""
import unittest

from trendradar.market_data.neeq_lifecycle_case_review import (
    build_neeq_lifecycle_case_review,
)


class NEEQLifecycleCaseReviewTests(unittest.TestCase):
    def register(self):
        def record(evidence_id, assertions, status="evidence_integrity_complete_pending_manual_review"):
            return {
                "evidence_id": evidence_id,
                "evidence_type": "issuer_announcement",
                "source_title": evidence_id,
                "source_url": "https://www.neeq.com.cn/example.pdf",
                "source_file": "example.pdf",
                "source_sha256": "a" * 64,
                "file_integrity_status": "sha256_match",
                "review_status": status,
                "source_authority_status": "pending_manual_source_verification",
                "date_assertions": [
                    {
                        "date_type": date_type,
                        "date_value": date_value,
                        "date_type_category": category,
                        "source_quote": quote,
                        "assertion_review_status": "pending_manual_review",
                    }
                    for date_type, date_value, category, quote in assertions
                ],
            }

        return {
            "register_version": "neeq_historical_evidence_register_v1",
            "dossiers": [
                {
                    "security_code": "834502",
                    "company_short_name": "富海银涛",
                    "notice_document_number": "2017-684",
                    "evidence_records": [
                        record("e1", [
                            ("transfer_suspension_date", "2017-12-13", "trading_status_event", "暂停转让"),
                            ("transfer_resumption_date", "2017-12-14", "trading_status_event", "恢复转让"),
                            ("planned_termination_date", "2017-12-21", "planned_event", "将于该日终止挂牌"),
                        ]),
                    ],
                },
                {
                    "security_code": "834606",
                    "company_short_name": "拥湾资产",
                    "notice_document_number": "2017-684",
                    "evidence_records": [
                        record("e2", [
                            ("transfer_suspension_date", "2017-12-13", "trading_status_event", "暂停转让"),
                            ("planned_termination_date", "2017-12-21", "planned_event", "将于该日终止挂牌"),
                        ]),
                    ],
                },
                {
                    "security_code": "834904",
                    "company_short_name": "银纪资产",
                    "notice_document_number": "2017-684",
                    "evidence_records": [
                        record("e3", [
                            ("transfer_resumption_date", "2017-12-14", "trading_status_event", "恢复转让"),
                            ("planned_termination_date", "2017-12-21", "planned_event", "将于该日终止挂牌"),
                        ]),
                    ],
                },
            ],
        }

    def test_reports_three_securities_and_preserves_evidence_timeline(self):
        result = build_neeq_lifecycle_case_review(self.register())
        self.assertEqual(result["summary"]["security_count"], 3)
        self.assertEqual(result["summary"]["evidence_record_count"], 3)
        self.assertEqual(result["summary"]["date_assertion_count"], 7)
        self.assertEqual(result["cases"][0]["timeline"][0]["date_value"], "2017-12-13")

    def test_does_not_infer_missing_resumption_for_834606(self):
        result = build_neeq_lifecycle_case_review(self.register())
        case = next(row for row in result["cases"] if row["security_code"] == "834606")
        self.assertFalse(case["coverage"]["explicit_transfer_resumption_evidence_found"])
        self.assertIn(
            "explicit_resumption_evidence_not_found",
            [flag["code"] for flag in case["review_flags"]],
        )

    def test_planned_termination_is_not_actual_termination_evidence(self):
        result = build_neeq_lifecycle_case_review(self.register())
        for case in result["cases"]:
            self.assertTrue(case["coverage"]["planned_termination_date_found"])
            self.assertFalse(case["coverage"]["actual_termination_result_evidence_found"])
            self.assertIn(
                "actual_termination_result_not_corroborated",
                [flag["code"] for flag in case["review_flags"]],
            )

    def test_reports_source_metadata_and_file_integrity_gaps(self):
        register = self.register()
        register["dossiers"][0]["evidence_records"][0]["review_status"] = "source_metadata_incomplete"
        register["dossiers"][1]["evidence_records"][0]["file_integrity_status"] = "sha256_mismatch"
        result = build_neeq_lifecycle_case_review(register)
        case_a = next(row for row in result["cases"] if row["security_code"] == "834502")
        case_b = next(row for row in result["cases"] if row["security_code"] == "834606")
        self.assertEqual(case_a["coverage"]["source_metadata_incomplete_record_count"], 1)
        self.assertEqual(case_b["coverage"]["file_integrity_problem_count"], 1)

    def test_missing_security_is_reported_without_fabricated_evidence(self):
        result = build_neeq_lifecycle_case_review(self.register(), security_codes=["999999"])
        self.assertEqual(result["cases"][0]["evidence_record_count"], 0)
        self.assertIn("no_evidence_records", [flag["code"] for flag in result["cases"][0]["review_flags"]])

    def test_all_outputs_remain_pending_and_read_only(self):
        result = build_neeq_lifecycle_case_review(self.register())
        self.assertTrue(result["safety"]["read_only"])
        self.assertEqual(result["safety"]["automatic_date_inferences"], 0)
        self.assertTrue(all(case["review_status"] == "pending_manual_review" for case in result["cases"]))
        self.assertTrue(all(not case["confirmed_legal_fact"] for case in result["cases"]))
        self.assertTrue(all(not case["formal_lifecycle_write_allowed"] for case in result["cases"]))

    def test_integrates_official_result_candidates_without_legal_confirmation(self):
        audit = {
            "cases": [
                {
                    "security_code": code,
                    "date_evidence_candidates": [{
                        "date_value": "2017-12-21",
                        "date_type": "official_result_delisting_date",
                        "classification_status": "candidate_pending_manual_review",
                        "source_url": "https://www.neeq.com.cn/terminationController/infoResultse.do",
                        "raw_response_sha256": (str(index) * 64)[:64],
                    }],
                    "pages": [{
                        "response_sha256": (str(index) * 64)[:64],
                        "raw_response_file": f"raw/{code}_page_0.jsonp",
                    }],
                }
                for index, code in enumerate(("834502", "834606", "834904"), start=1)
            ]
        }
        result = build_neeq_lifecycle_case_review(
            self.register(), official_result_audit=audit
        )
        self.assertEqual(result["summary"]["official_result_candidate_count"], 3)
        for case in result["cases"]:
            self.assertTrue(case["coverage"]["actual_termination_result_evidence_found"])
            self.assertEqual(case["official_result_candidate_count"], 1)
            candidate = case["official_result_evidence"][0]
            self.assertEqual(candidate["date_value"], "2017-12-21")
            self.assertEqual(candidate["assertion_review_status"], "candidate_pending_manual_review")
            self.assertFalse(case["confirmed_legal_fact"])
            self.assertFalse(case["formal_lifecycle_write_allowed"])
        case_606 = next(row for row in result["cases"] if row["security_code"] == "834606")
        self.assertFalse(case_606["coverage"]["explicit_transfer_resumption_evidence_found"])
        self.assertTrue(result["safety"]["read_only"])


if __name__ == "__main__":
    unittest.main()
