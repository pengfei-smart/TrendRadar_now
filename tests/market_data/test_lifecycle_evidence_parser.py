# 本文件测试生命周期公告解析器对证券代码、事件类型和日期语义的提取。
"""Tests for rule-based Chinese lifecycle announcement parsing."""

import unittest

from trendradar.market_data.lifecycle_evidence_parser import (
    extract_security_codes,
    parse_lifecycle_announcement,
)


class LifecycleEvidenceParserTests(unittest.TestCase):
    def test_extracts_multiple_security_codes_without_duplicates(self):
        text = "证券代码：000765，原证券代码000765；相关代码400038、001267。"
        self.assertEqual(
            extract_security_codes(text),
            ("000765", "400038", "001267"),
        )

    def test_extracts_lifecycle_event_types(self):
        text = "公司股票暂停上市，后经批准恢复上市，最终终止上市并摘牌。"
        parsed = parse_lifecycle_announcement(text)
        self.assertEqual(
            set(parsed.event_types),
            {"suspension", "resumption", "delisting"},
        )

    def test_recognizes_neeq_termination_of_listing_as_delisting_candidate(self):
        parsed = parse_lifecycle_announcement("证券代码400001，关于终止挂牌的公告。")
        self.assertEqual(parsed.security_codes, ("400001",))
        self.assertIn("delisting", parsed.event_types)
        self.assertIn("终止挂牌", parsed.matched_terms)

    def test_preserves_distinct_date_semantics(self):
        text = (
            "公告日期：2024年3月1日。最后交易日为2024-03-08，"
            "终止上市日：2024/03/15。"
        )
        parsed = parse_lifecycle_announcement(text)
        by_type = {item.date_type: item.value for item in parsed.date_evidence}
        self.assertEqual(by_type["announcement_date"], "2024-03-01")
        self.assertEqual(by_type["last_trading_date"], "2024-03-08")
        self.assertEqual(by_type["delisting_date"], "2024-03-15")
        self.assertTrue(all(item.date_status == "unknown" for item in parsed.date_evidence))

    def test_unlabeled_date_does_not_inherit_previous_date_label(self):
        parsed = parse_lifecycle_announcement(
            "公告日期：2024年3月1日。相关日期为2024-03-08，终止上市日：2024/03/15。"
        )
        by_value = {item.value: item.date_type for item in parsed.date_evidence}
        self.assertEqual(by_value["2024-03-01"], "announcement_date")
        self.assertEqual(by_value["2024-03-08"], "unspecified")
        self.assertEqual(by_value["2024-03-15"], "delisting_date")

    def test_invalid_calendar_dates_are_ignored(self):
        parsed = parse_lifecycle_announcement("公告日期：2024年2月30日。")
        self.assertEqual(parsed.date_evidence, ())

    def test_does_not_invent_date_type_when_context_is_missing(self):
        parsed = parse_lifecycle_announcement("相关日期为2024年5月6日。")
        self.assertEqual(parsed.date_evidence[0].date_type, "unspecified")

    def test_empty_text_returns_empty_evidence(self):
        parsed = parse_lifecycle_announcement("  ")
        self.assertEqual(parsed.security_codes, ())
        self.assertEqual(parsed.event_types, ())
        self.assertEqual(parsed.date_evidence, ())

    def test_non_string_input_is_rejected(self):
        with self.assertRaises(TypeError):
            parse_lifecycle_announcement(None)


if __name__ == "__main__":
    unittest.main()
