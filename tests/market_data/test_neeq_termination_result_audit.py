# 本文件测试NEEQ终止挂牌结果分页覆盖率和重复记录审计逻辑。
"""Tests for NEEQ termination-result pagination audit."""

import unittest
from types import SimpleNamespace

from trendradar.market_data.neeq_termination_result_audit import audit_neeq_termination_pages


def page(number, total_pages=2, total_elements=2, records=None):
    return SimpleNamespace(
        page_number=number, page_size=1, total_pages=total_pages,
        total_elements=total_elements, status_code=200,
        records=records if records is not None else [{"companyCode": f"{number:06d}", "id": number}],
    )


class TerminationResultAuditTests(unittest.TestCase):
    def test_marks_complete_contiguous_unique_full_scan(self):
        result = audit_neeq_termination_pages([page(0), page(1)])
        self.assertTrue(result["complete_coverage"])
        self.assertEqual(result["fetched_record_count"], 2)

    def test_marks_partial_when_page_missing(self):
        result = audit_neeq_termination_pages([page(0), page(2, total_pages=3, total_elements=3)])
        self.assertFalse(result["complete_coverage"])
        self.assertEqual(result["missing_pages_in_fetched_range"], [1])

    def test_detects_duplicate_record_ids_and_company_codes(self):
        duplicate = page(1, records=[{"companyCode": "000000", "id": 0}])
        result = audit_neeq_termination_pages([page(0), duplicate])
        self.assertTrue(result["complete_coverage"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["exact_duplicate_record_ids"], ["0"])
        self.assertEqual(result["conflicting_duplicate_record_ids"], [])
        self.assertEqual(
            result["duplicate_record_id_classifications"]["0"]["classification"],
            "exact_duplicate",
        )
        self.assertEqual(result["duplicate_company_codes"], ["000000"])
        self.assertEqual(result["duplicate_record_ids"], ["0"])
        self.assertEqual(len(result["duplicate_record_details"]), 2)
        self.assertEqual(
            {row["page"] for row in result["duplicate_record_details"]},
            {0, 1},
        )
        self.assertTrue(all(row["duplicate_company_code"] for row in result["duplicate_record_details"]))
        self.assertTrue(all(row["duplicate_record_id"] for row in result["duplicate_record_details"]))


    def test_conflicting_duplicate_record_id_blocks_complete_coverage(self):
        first = page(0, total_pages=2, total_elements=2, records=[
            {"companyCode": "000001", "companyName": "甲公司", "id": 7}
        ])
        second = page(1, total_pages=2, total_elements=2, records=[
            {"companyCode": "000001", "companyName": "乙公司", "id": 7}
        ])
        result = audit_neeq_termination_pages([first, second])
        self.assertFalse(result["complete_coverage"])
        self.assertEqual(result["conflicting_duplicate_record_ids"], ["7"])
        self.assertEqual(result["exact_duplicate_record_ids"], [])
        self.assertEqual(
            result["duplicate_record_id_classifications"]["7"]["classification"],
            "conflicting_duplicate",
        )
        self.assertEqual(
            result["duplicate_record_id_classifications"]["7"]["pages"],
            [0, 1],
        )
        self.assertEqual(
            {row["duplicate_classification"] for row in result["duplicate_record_details"]},
            {"conflicting_duplicate"},
        )

    def test_duplicate_company_code_with_distinct_record_ids_is_not_record_id_conflict(self):
        first = page(0, total_pages=2, total_elements=2, records=[
            {"companyCode": "000001", "companyName": "甲公司", "id": 7}
        ])
        second = page(1, total_pages=2, total_elements=2, records=[
            {"companyCode": "000001", "companyName": "甲公司", "id": 8}
        ])
        result = audit_neeq_termination_pages([first, second])
        self.assertTrue(result["complete_coverage"])
        self.assertEqual(result["duplicate_company_codes"], ["000001"])
        self.assertEqual(result["duplicate_record_ids"], [])
        self.assertEqual(
            {row["duplicate_classification"] for row in result["duplicate_record_details"]},
            {"duplicate_company_code_only"},
        )

    def test_reports_attachment_coverage_separately(self):
        sample_page = SimpleNamespace(
            page_number=0, page_size=3, total_pages=1, total_elements=3,
            status_code=200, records=[
            {"companyCode": "870099", "id": "1",
             "delistingNotice": "https://www.neeq.com.cn/a.pdf"},
            {"companyCode": "832480", "id": "2",
             "delistingNotice": "https://www.neeq.com.cn/important_news/2.html"},
            {"companyCode": "831610", "id": "3", "delistingNotice": ""},
            ],
        )
        result = audit_neeq_termination_pages([sample_page])
        self.assertTrue(result["complete_coverage"])
        self.assertFalse(result["attachment_coverage_complete"])
        self.assertEqual(result["attachment_url_count"], 2)
        self.assertEqual(result["records_missing_attachment_url"], 1)
        self.assertEqual(result["attachment_kind_counts"],
                         {"html": 1, "missing": 1, "pdf": 1})

    def test_rejected_records_block_complete_coverage(self):
        rejected = SimpleNamespace(
            page_number=0, page_size=1, total_pages=1, total_elements=1,
            status_code=200, records=[{"companyCode": "870099", "id": 1}],
            rejected_record_count=1,
            record_validation_errors=("record_1:untrusted attachment",),
        )
        result = audit_neeq_termination_pages([rejected])
        self.assertFalse(result["complete_coverage"])
        self.assertIn("page_0_rejected_records_1", result["metadata_errors"])

    def test_detects_pagination_metadata_drift(self):
        result = audit_neeq_termination_pages([page(0), page(1, total_pages=3)])
        self.assertFalse(result["complete_coverage"])
        self.assertIn("total_pages_changed_between_pages", result["metadata_errors"])


if __name__ == "__main__":
    unittest.main()
