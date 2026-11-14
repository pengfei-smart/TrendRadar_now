# 本文件测试NEEQ终止挂牌候选清单只折叠完全重复记录且保留原始证据。
"""Tests for conservative NEEQ termination candidate manifest generation."""

import unittest
from types import SimpleNamespace

from trendradar.market_data.neeq_termination_candidate_manifest import (
    build_neeq_termination_candidate_manifest,
)


def page(number, records):
    return SimpleNamespace(page_number=number, records=records)


class NEEQTerminationCandidateManifestTests(unittest.TestCase):
    def test_collapses_only_exact_duplicate_record_id_and_keeps_occurrences(self):
        row = {"id": 1622, "companyCode": "836758", "companyName": "摘牌奥吉"}
        pages = [page(0, [row]), page(1, [dict(row)])]
        result = build_neeq_termination_candidate_manifest(
            pages, {"status": "complete", "complete_coverage": True},
            generated_at="2026-11-14T00:00:00+00:00",
        )
        self.assertEqual(result["source_occurrence_count"], 2)
        self.assertEqual(result["candidate_count"], 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["deduplication_classification"], "exact_duplicate_collapsed")
        self.assertEqual(candidate["occurrence_count"], 2)
        self.assertEqual([item["page"] for item in candidate["occurrences"]], [0, 1])
        self.assertEqual(candidate["raw_record"], row)
        self.assertFalse(candidate["confirmed_legal_fact"])
        self.assertFalse(candidate["formal_lifecycle_write_allowed"])
        self.assertEqual(result["safety"]["database_writes"], 0)

    def test_keeps_distinct_record_ids_for_same_company_code(self):
        pages = [page(0, [
            {"id": 7, "companyCode": "000001", "companyName": "甲公司"},
            {"id": 8, "companyCode": "000001", "companyName": "甲公司"},
        ])]
        result = build_neeq_termination_candidate_manifest(
            pages, {"status": "complete", "complete_coverage": True},
        )
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(
            {item["record_id"] for item in result["candidates"]}, {"7", "8"}
        )
        self.assertEqual(result["company_code_groups"][0]["distinct_record_id_count"], 2)

    def test_keeps_conflicting_duplicate_id_occurrences_separate(self):
        pages = [page(0, [
            {"id": 9, "companyCode": "000009", "companyName": "甲公司"},
            {"id": 9, "companyCode": "000009", "companyName": "乙公司"},
        ])]
        result = build_neeq_termination_candidate_manifest(
            pages, {"status": "partial", "complete_coverage": False},
        )
        self.assertEqual(result["candidate_count"], 2)
        self.assertTrue(all(
            item["deduplication_classification"] == "conflicting_record_id_occurrence"
            for item in result["candidates"]
        ))

    def test_keeps_records_without_id_as_separate_review_items(self):
        row = {"companyCode": "000010", "companyName": "无ID公司"}
        result = build_neeq_termination_candidate_manifest(
            [page(0, [row, dict(row)])],
            {"status": "complete", "complete_coverage": True},
        )
        self.assertEqual(result["candidate_count"], 2)
        self.assertTrue(all(
            item["deduplication_classification"] == "missing_record_id_review"
            for item in result["candidates"]
        ))


if __name__ == "__main__":
    unittest.main()
