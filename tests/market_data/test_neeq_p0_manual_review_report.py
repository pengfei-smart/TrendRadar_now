# 本文件测试NEEQ P0人工审阅报告生成器的内容、只读性和安全边界。
"""Tests for the read-only NEEQ P0 manual review report builder."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.market_data.build_neeq_p0_manual_review_report import (
    ReadOnlyMarketStorage,
    build_report,
    render_markdown,
)
from trendradar.market_data.storage import MarketStorage


class NEEQP0ManualReviewReportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "market.db")
        self.storage = MarketStorage(self.db_path)
        cases = [
            ("neeq-result-836614-842", "836614", "奥其斯", "2019-09-06"),
            ("neeq-result-838047-841", "838047", "二中科技", "2019-09-06"),
            ("neeq-result-430134-844", "430134", "ST可来博", "2020-05-13"),
        ]
        self.storage.save_neeq_termination_cases([{
            "case_id": case_id,
            "company_code": code,
            "company_name_snapshot": name,
            "source_record_id": case_id.rsplit("-", 1)[-1],
            "source_api": "neeq_termination_result_api",
            "interface_result_date": result_date,
            "raw_record": {"companyCode": code},
        } for case_id, code, name, result_date in cases])
        procedures = [
            ("p-836614", "neeq-result-836614-842", "2019-07-29"),
            ("p-838047", "neeq-result-838047-841", "2019-07-09"),
            ("p-430134", "neeq-result-430134-844", "2020-02-25"),
        ]
        self.storage.save_neeq_termination_procedures([{
            "procedure_id": procedure_id,
            "case_id": case_id,
            "procedure_type": "conditional_termination",
            "event_date": event_date,
            "date_semantics": "conditional_termination",
            "date_status": "pending",
            "notes": "条件终止日期候选",
        } for procedure_id, case_id, event_date in procedures])
        self.storage.save_neeq_termination_evidence([{
            "evidence_id": "evidence-430134-sponsor",
            "evidence_kind": "sponsor_notice",
            "source_authority": "sponsor_official",
            "notice_number": "test-notice",
            "document_title": "主办券商公告",
            "source_url": "https://example.org/sponsor.pdf",
            "published_date": "2020-02-17",
            "retrieved_at": "2026-11-16T13:00:00+08:00",
            "evidence_status": "located",
            "content_excerpt": "测试摘要",
            "metadata": {"document_issuer": "测试主办券商"},
        }])
        self.storage.link_neeq_termination_evidence([{
            "link_id": "link-430134-sponsor",
            "case_id": "neeq-result-430134-844",
            "evidence_id": "evidence-430134-sponsor",
            "relation_type": "supports_case",
        }])
        self.storage.record_neeq_termination_evidence_search({
            "search_id": "search-430134-final",
            "case_id": "neeq-result-430134-844",
            "evidence_kind": "final_termination_notice",
            "search_status": "searched_not_found",
            "searched_at": "2026-11-16T13:10:00+08:00",
            "search_scope": "测试检索范围",
            "search_notes": "本次检索范围内未找到",
            "source_urls": ["https://example.org/search"],
            "reviewer": "tester",
        })
        self.storage.record_neeq_termination_evidence_search({
            "search_id": "search-430134-sponsor",
            "case_id": "neeq-result-430134-844",
            "evidence_kind": "sponsor_notice",
            "search_status": "found_not_registered",
            "searched_at": "2026-11-16T13:11:00+08:00",
            "search_scope": "测试公告检索范围",
            "search_notes": "已定位公告，登记后应转为registered",
            "source_urls": ["https://example.org/sponsor.pdf"],
            "reviewer": "tester",
        })

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_report_contains_exactly_three_p0_cases_and_mismatch_dates(self):
        report = build_report(self.storage, "2026-11-16T13:30:00+08:00")
        self.assertEqual(report["case_count"], 3)
        by_code = {item["company_code"]: item for item in report["cases"]}
        self.assertEqual(set(by_code), {"836614", "838047", "430134"})
        self.assertEqual(by_code["836614"]["conditional_termination_dates"], ["2019-07-29"])
        self.assertEqual(by_code["838047"]["conditional_termination_dates"], ["2019-07-09"])
        self.assertEqual(by_code["430134"]["conditional_termination_dates"], ["2020-02-25"])
        self.assertTrue(all(item["date_mismatch"] for item in report["cases"]))

    def test_report_includes_evidence_and_latest_search_status(self):
        report = build_report(self.storage, "2026-11-16T13:30:00+08:00")
        keli = next(item for item in report["cases"] if item["company_code"] == "430134")
        self.assertEqual(keli["evidence"][0]["evidence_id"], "evidence-430134-sponsor")
        self.assertEqual(keli["evidence"][0]["metadata"]["document_issuer"], "测试主办券商")
        sponsor_search = next(
            item for item in keli["evidence_searches"]
            if item["evidence_kind"] == "sponsor_notice"
        )
        self.assertEqual(sponsor_search["latest_status"], "registered")
        self.assertEqual(sponsor_search["latest_recorded_status"], "found_not_registered")
        self.assertIn("此后该类型证据已登记", sponsor_search["search_notes"])
        final_search = next(
            item for item in keli["evidence_searches"]
            if item["evidence_kind"] == "final_termination_notice"
        )
        self.assertEqual(final_search["latest_status"], "searched_not_found")
        self.assertEqual(len(keli["evidence_search_history"]), 2)
        self.assertEqual(
            next(
                item for item in keli["evidence_search_history"]
                if item["evidence_kind"] == "sponsor_notice"
            )["search_status"],
            "found_not_registered",
        )
        markdown = render_markdown(report)
        self.assertIn("检索记录原始状态：found_not_registered", markdown)
        self.assertIn("当前有效状态：registered", markdown)
        self.assertIn("formal_decision", keli["evidence_search_status"])

    def test_decision_procedure_link_warns_when_evidence_has_no_decision_date(self):
        self.storage.save_neeq_termination_procedures([{
            "procedure_id": "p-836614-decision",
            "case_id": "neeq-result-836614-842",
            "procedure_type": "decision_issued",
            "event_date": "2019-07-19",
            "date_semantics": "decision_issued",
            "date_status": "pending",
            "notes": "待核验决定日期",
        }])
        self.storage.save_neeq_termination_evidence([{
            "evidence_id": "evidence-836614-batch",
            "evidence_kind": "batch_notice",
            "source_authority": "neeq_official",
            "notice_number": "2019-1071",
            "document_title": "批次终止挂牌公告",
            "source_url": "https://example.org/2019-1071",
            "published_date": "",
            "retrieved_at": "2026-11-16T13:00:00+08:00",
            "evidence_status": "crosschecked",
            "content_excerpt": "",
            "metadata": {"decision_date": None},
        }])
        self.storage.link_neeq_termination_evidence([{
            "link_id": "link-836614-batch-decision",
            "case_id": "neeq-result-836614-842",
            "evidence_id": "evidence-836614-batch",
            "procedure_id": "p-836614-decision",
            "relation_type": "supports_procedure",
            "notes": "待核验原文",
        }])
        report = build_report(self.storage, "2026-11-16T13:30:00+08:00")
        case = next(item for item in report["cases"] if item["company_code"] == "836614")
        evidence = next(item for item in case["evidence"] if item["evidence_id"] == "evidence-836614-batch")
        link = evidence["case_links"][0]
        self.assertEqual(link["procedure_type"], "decision_issued")
        self.assertEqual(link["event_date"], "2019-07-19")
        self.assertIn("未记录决定日期", link["support_warning"])
        markdown = render_markdown(report)
        self.assertIn("关联核验提示：当前证据元数据未记录决定日期", markdown)
        self.assertIn("程序关联：`decision_issued`（2019-07-19）", markdown)

    def test_report_does_not_write_reviews_or_lifecycle_facts(self):
        before = {}
        with self.storage.connect() as connection:
            for table in (
                "neeq_termination_case", "neeq_termination_evidence",
                "neeq_termination_evidence_link", "neeq_termination_evidence_search",
                "neeq_termination_review", "security_master", "security_lifecycle_events",
            ):
                before[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        report = build_report(self.storage, "2026-11-16T13:30:00+08:00")
        self.assertFalse(report["global_safety"]["database_writes"])
        self.assertFalse(report["global_safety"]["review_records_written"])
        self.assertFalse(report["global_safety"]["formal_lifecycle_writes"])
        self.assertEqual(report["global_safety"]["actual_termination_facts_confirmed"], 0)
        self.assertTrue(all(not item["review_record_written"] for item in report["cases"]))
        after = {}
        with self.storage.connect() as connection:
            for table in before:
                after[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        self.assertEqual(after, before)

    def test_markdown_states_review_and_date_semantics_boundaries(self):
        report = build_report(self.storage, "2026-11-16T13:30:00+08:00")
        markdown = render_markdown(report)
        self.assertIn("NEEQ三个P0终止挂牌案件人工证据审阅报告", markdown)
        self.assertIn("836614 奥其斯", markdown)
        self.assertIn("838047 二中科技", markdown)
        self.assertIn("430134 ST可来博", markdown)
        self.assertIn("searched_not_found", markdown)
        self.assertIn("实际终止挂牌日期已确认：**否**", markdown)
        self.assertIn("人工复核记录写入：否", markdown)

    def test_report_runs_through_sqlite_read_only_adapter(self):
        readonly = ReadOnlyMarketStorage(self.db_path)
        report = build_report(readonly, "2026-11-16T13:30:00+08:00")
        self.assertEqual(report["case_count"], 3)
        with readonly.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM neeq_termination_case").fetchone()[0],
                3,
            )
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM neeq_termination_case")

    def test_missing_p0_case_fails_clearly(self):
        with self.storage.connect() as connection:
            connection.execute(
                "DELETE FROM neeq_termination_case WHERE company_code='836614'"
            )
        with self.assertRaisesRegex(ValueError, "案件不存在"):
            build_report(self.storage, "2026-11-16T13:30:00+08:00")


if __name__ == "__main__":
    unittest.main()
