# 本文件测试NEEQ分页公告的本地证券身份匹配、生命周期标题筛选和扫描覆盖率计算。
"""Tests for local NEEQ announcement identity matching and scan coverage."""

import unittest

from trendradar.market_data.neeq_lifecycle_scanner import (
    lifecycle_records,
    match_neeq_announcement_records,
    normalize_issuer_name,
    page_coverage,
)


class NEEQLifecycleScannerTests(unittest.TestCase):
    def test_normalizes_full_width_punctuation_and_spacing(self):
        self.assertEqual(normalize_issuer_name(" 摘牌　商网（股份） "), "摘牌商网股份")

    def test_matches_exact_code_and_normalized_exact_names(self):
        records = [
            {"companyCd": "832480", "companyName": "摘牌商网"},
            {"companyCd": "870099", "companyName": "华搜股份"},
            {"companyCd": "872478", "companyName": "毅达科技"},
            {"companyCd": "000000", "companyName": "摘牌商网科技"},
        ]
        matched = match_neeq_announcement_records(
            records,
            target_codes=("832480",),
            target_names=("华搜 股份",),
        )
        self.assertEqual([row["companyCd"] for row in matched], ["832480", "870099"])
        self.assertEqual(matched[0]["_identity_match_methods"], ["exact_security_code"])
        self.assertEqual(
            matched[1]["_identity_match_methods"],
            ["exact_normalized_company_name"],
        )

    def test_does_not_use_substring_or_fuzzy_name_matching(self):
        records = [{"companyCd": "000000", "companyName": "摘牌商网科技"}]
        self.assertEqual(
            match_neeq_announcement_records(records, target_names=("摘牌商网",)),
            [],
        )

    def test_filters_lifecycle_titles_without_claiming_legal_status(self):
        rows = lifecycle_records([
            {
                "companyCd": "832480",
                "companyName": "摘牌商网",
                "disclosureTitle": "关于股票进入摘牌整理期的第十次风险提示公告",
                "publishDate": "2026-11-02",
                "destFilePath": "/disclosure/sample.pdf",
                "fileExt": "pdf",
            },
            {
                "companyCd": "832480",
                "companyName": "摘牌商网",
                "disclosureTitle": "关于召开股东大会的通知",
            },
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company_code"], "832480")
        self.assertEqual(rows[0]["record"]["publishDate"], "2026-11-02")

    def test_calculates_partial_and_complete_page_coverage(self):
        partial = page_coverage(5602, 20, 10)
        self.assertEqual(partial["total_pages"], 281)
        self.assertEqual(partial["coverage_status"], "partial")
        complete = page_coverage(40, 20, 2)
        self.assertEqual(complete["coverage_ratio"], 1.0)
        self.assertEqual(complete["coverage_status"], "complete")

    def test_unknown_page_size_is_not_reported_as_complete(self):
        result = page_coverage(100, 0, 1)
        self.assertEqual(result["coverage_status"], "partial")
        self.assertEqual(result["coverage_ratio"], 0.0)

    def test_rejects_negative_coverage_inputs(self):
        with self.assertRaises(ValueError):
            page_coverage(-1, 20, 1)


if __name__ == "__main__":
    unittest.main()
