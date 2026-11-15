# 本文件测试NEEQ批次名单交叉审计的批次分类、代码匹配和只读安全边界。
"""Tests for read-only NEEQ termination batch reconciliation."""

import unittest

from trendradar.market_data.neeq_termination_batch_reconciliation import (
    build_neeq_termination_batch_reconciliation,
)


def candidate(code, name, short, record_id, result_date="2019-09-06"):
    return {
        "company_code": code,
        "company_name": name,
        "company_short_name": short,
        "record_id": record_id,
        "delisting_date": result_date,
        "attachment_url": "https://www.neeq.com.cn/result.pdf",
        "confirmed_legal_fact": False,
        "formal_lifecycle_write_allowed": False,
    }


def notice(code, name, short, number, decision, planned):
    return {
        "security_code": code,
        "company_name": name,
        "short_name": short,
        "document_number": number,
        "announcement_date": decision,
        "decision_date": decision,
        "planned_termination_date": planned,
        "stated_termination_date": None,
        "suspension_date": None,
        "identity_source": "html_attachment_table",
    }


class NEEQTerminationBatchReconciliationTests(unittest.TestCase):
    def build(self, review_items, candidates, coverage_items=None):
        review = {"parser_version": "neeq_termination_notice_parser_v8", "items": review_items}
        coverage = {
            "audit_type": "neeq_notice_candidate_coverage",
            "items": coverage_items if coverage_items is not None else [],
        }
        manifest = {
            "source": "neeq_official_termination_result_list",
            "manifest": {"source": "neeq_official_termination_result_list",
                         "generated_at": "2026-11-15T00:00:00+00:00",
                         "candidates": candidates},
        }
        return build_neeq_termination_batch_reconciliation(
            review, coverage, manifest, generated_at="2026-11-15T00:00:00+00:00"
        )

    def test_background_membership_is_not_promoted_and_later_formal_batch_is_marked(self):
        result = self.build([
            {
                "attachment_id": "a1",
                "url": "https://www.neeq.com.cn/notice984.html",
                "notices": [],
                "contextual_identities": [{
                    "security_code": "831218", "short_name": "成丰股份",
                    "identity_source": "html_attachment_table",
                }],
            },
            {
                "attachment_id": "a2",
                "url": "https://www.neeq.com.cn/notice987.html",
                "notices": [notice(
                    "831218", "成丰股份有限公司", "成丰股份",
                    "2019-987", "2019-07-12", "2019-07-22",
                )],
                "contextual_identities": [],
            },
        ], [candidate("831218", "成丰股份有限公司", "成丰股份", "731", "2019-08-05")])
        rows = result["batch_memberships"]
        self.assertEqual(len(rows), 2)
        background = next(row for row in rows if row["membership_type"] == "background_pending_company")
        formal = next(row for row in rows if row["membership_type"] == "formal_decision_company")
        self.assertEqual(background["batch_notice_number"], None)
        self.assertEqual(formal["batch_notice_number"], "2019-987")
        self.assertEqual(formal["batch_progression_type"], "later_batch_decision_company")
        self.assertEqual(background["issues"], ["background_membership_is_not_a_formal_decision"])
        self.assertFalse(background["confirmed_legal_fact"])
        self.assertFalse(formal["formal_lifecycle_write_allowed"])

    def test_formal_decision_without_background_is_not_labelled_later_batch(self):
        result = self.build([{
            "attachment_id": "a",
            "url": "https://www.neeq.com.cn/notice.html",
            "notices": [notice("838047", "二中科技有限公司", "二中科技",
                               "2019-984", "2019-07-01", "2019-07-09")],
            "contextual_identities": [],
        }], [candidate("838047", "二中科技有限公司", "二中科技", "841")])
        row = result["batch_memberships"][0]
        self.assertEqual(row["membership_type"], "formal_decision_company")
        self.assertIsNone(row["batch_progression_type"])

    def test_same_security_can_keep_multiple_formal_batch_memberships(self):
        rows = [
            notice("000001", "甲公司股份有限公司", "甲公司", "2019-100", "2019-01-01", "2019-01-10"),
            notice("000001", "甲公司股份有限公司", "甲公司", "2019-200", "2019-02-01", "2019-02-10"),
        ]
        result = self.build([{
            "attachment_id": "a",
            "url": "https://www.neeq.com.cn/two.html",
            "notices": rows,
            "contextual_identities": [],
        }], [candidate("000001", "甲公司股份有限公司", "甲公司", "1")])
        self.assertEqual(len(result["batch_memberships"]), 2)
        self.assertEqual(
            {row["batch_notice_number"] for row in result["batch_memberships"]},
            {"2019-100", "2019-200"},
        )

    def test_name_conflict_is_reported_but_code_membership_is_retained(self):
        result = self.build([{
            "attachment_id": "a",
            "url": "https://www.neeq.com.cn/notice.html",
            "notices": [notice("000002", "乙公司股份有限公司", "乙公司",
                               "2019-300", "2019-03-01", "2019-03-10")],
            "contextual_identities": [],
        }], [candidate("000002", "丙公司股份有限公司", "丙公司", "2")])
        self.assertEqual(len(result["name_conflicts"]), 1)
        self.assertIn("company_name_mismatch", result["batch_memberships"][0]["issues"])
        self.assertEqual(result["batch_memberships"][0]["security_code"], "000002")

    def test_notice_absent_from_candidate_manifest_is_supplemental_review(self):
        result = self.build([{
            "attachment_id": "a",
            "url": "https://www.neeq.com.cn/notice.html",
            "notices": [notice("837689", "圣保堂股份有限公司", "圣保堂",
                               "2019-987", "2019-07-12", "2019-07-22")],
            "contextual_identities": [],
        }], [])
        row = result["batch_memberships"][0]
        self.assertEqual(row["coverage_classification"], "candidate_absent_from_manifest")
        self.assertEqual(result["summary"]["supplemental_notice_identity_count"], 1)
        self.assertFalse(row["confirmed_legal_fact"])

    def test_unresolved_identity_is_kept_separately(self):
        result = self.build([{
            "attachment_id": "a",
            "url": "https://www.neeq.com.cn/notice.html",
            "notices": [{"security_code": None, "short_name": None}],
            "contextual_identities": [],
        }], [])
        self.assertEqual(len(result["unresolved_identities"]), 1)
        self.assertEqual(result["summary"]["unresolved_identity_count"], 1)

    def test_candidate_without_batch_membership_is_reported_not_inferred(self):
        result = self.build([], [candidate("000003", "丁公司股份有限公司", "丁公司", "3")])
        row = result["candidate_coverage"][0]
        self.assertEqual(row["coverage_status"], "batch_coverage_not_exhaustive")
        self.assertFalse(row["parsed_batch_membership_found"])
        self.assertFalse(row["has_formal_decision_membership"])
        self.assertFalse(result["summary"]["candidate_batch_coverage_is_exhaustive"])

    def test_deduplicates_exact_same_membership_occurrence_only(self):
        item = {
            "attachment_id": "a",
            "url": "https://www.neeq.com.cn/notice.html",
            "notices": [
                notice("000004", "戊公司股份有限公司", "戊公司", "2019-400", "2019-04-01", "2019-04-10"),
                notice("000004", "戊公司股份有限公司", "戊公司", "2019-400", "2019-04-01", "2019-04-10"),
            ],
            "contextual_identities": [],
        }
        result = self.build([item], [candidate("000004", "戊公司股份有限公司", "戊公司", "4")])
        self.assertEqual(len(result["batch_memberships"]), 1)


    def test_delisted_display_prefix_is_classified_not_reported_as_identity_conflict(self):
        result = self.build([{
            "attachment_id": "a",
            "url": "https://www.neeq.com.cn/notice.html",
            "notices": [notice("000006", "甲公司股份有限公司", "甲公司",
                               "2019-600", "2019-06-01", "2019-06-10")],
            "contextual_identities": [],
        }], [candidate("000006", "摘牌甲公司", "摘牌甲公司", "6")])
        row = result["batch_memberships"][0]
        self.assertEqual(row["candidate_name_match_type"], "delisted_display_name")
        self.assertEqual(result["summary"]["candidate_name_conflict_count"], 0)
        self.assertEqual(result["summary"]["delisted_display_name_difference_count"], 1)
        self.assertEqual(len(result["display_name_differences"]), 1)

    def test_date_completeness_keeps_missing_decision_date_missing(self):
        missing_decision_notice = notice(
            "000007", "乙公司股份有限公司", "乙公司", "2019-700", "2019-07-01", "2019-07-09"
        )
        missing_decision_notice["decision_date"] = None
        result = self.build([{
            "attachment_id": "a",
            "url": "https://www.neeq.com.cn/notice.html",
            "notices": [missing_decision_notice],
            "contextual_identities": [],
        }], [candidate("000007", "乙公司股份有限公司", "乙公司", "7")])
        row = result["batch_memberships"][0]
        self.assertEqual(row["batch_announcement_date"], "2019-07-01")
        self.assertIsNone(row["formal_decision_date"])
        self.assertEqual(result["summary"]["date_completeness"]["formal_decision_date"]["missing_count"], 1)
        self.assertFalse(result["safety"]["batch_coverage_is_exhaustive"])

    def test_report_is_read_only_and_never_confirms_legal_facts(self):
        result = self.build([], [candidate("000005", "己公司股份有限公司", "己公司", "5")])
        self.assertEqual(result["safety"]["database_writes"], 0)
        self.assertEqual(result["safety"]["formal_lifecycle_writes"], 0)
        self.assertEqual(result["safety"]["legal_fact_confirmations"], 0)
        self.assertEqual(result["safety"]["background_membership_promotions"], 0)
        self.assertTrue(result["safety"]["read_only"])
        self.assertTrue(all(not row["confirmed_legal_fact"] for row in result["candidate_coverage"]))


if __name__ == "__main__":
    unittest.main()
