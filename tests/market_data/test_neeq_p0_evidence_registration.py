# 本文件测试P0 NEEQ专项证据登记脚本的预览、事务写入、幂等性和正式事实隔离。
"""Tests for the safe P0 NEEQ evidence registration workflow."""

import tempfile
import unittest
from pathlib import Path

from scripts.market_data.register_neeq_p0_evidence import apply_payload, build_payload
from trendradar.market_data.neeq_termination_review import list_neeq_termination_review_queue
from trendradar.market_data.storage import MarketStorage


class NEEQP0EvidenceRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.temp_dir.name) / "market.db"))
        cases = [
            ("neeq-result-836614-842", "836614", "奥其斯", "842"),
            ("neeq-result-838047-841", "838047", "二中科技", "841"),
            ("neeq-result-430134-844", "430134", "ST可来博", "844"),
        ]
        self.storage.save_neeq_termination_cases([{
            "case_id": case_id,
            "company_code": code,
            "company_name_snapshot": name,
            "source_record_id": record_id,
            "source_api": "neeq_termination_result_api",
            "interface_result_date": "2019-09-06",
            "raw_record": {"id": int(record_id), "companyCode": code},
        } for case_id, code, name, record_id in cases])
        self.storage.save_neeq_termination_evidence([
            {
                "evidence_id": "existing-2019-984",
                "evidence_kind": "batch_notice",
                "source_authority": "neeq_official",
                "notice_number": "2019-984",
                "document_title": "2019-984",
                "source_url": "https://www.neeq.com.cn/important_news/200006230.html",
                "published_date": "2019-07-01",
                "retrieved_at": "2026-11-16T10:00:00+08:00",
                "metadata": {"membership_type": "formal_decision_company"},
            },
            {
                "evidence_id": "existing-430134-decision",
                "evidence_kind": "formal_decision",
                "source_authority": "neeq_official",
                "notice_number": "股转系统发〔2020〕110号",
                "document_title": "ST可来博终止挂牌决定",
                "source_url": "https://www.neeq.com.cn/uploads/1/file/public/202002/20200217153226_t75f1e2e80.pdf",
                "published_date": "2020-02-13",
                "retrieved_at": "2026-11-16T10:00:00+08:00",
            },
        ])
        self.storage.save_neeq_termination_procedures([{
            "procedure_id": "existing-838047-decision",
            "case_id": "neeq-result-838047-841",
            "procedure_type": "decision_issued",
            "event_date": "2019-07-01",
            "date_semantics": "decision_issued",
            "date_status": "pending",
        }])
        self.storage.link_neeq_termination_evidence([
            {
                "link_id": "existing-838047-link",
                "case_id": "neeq-result-838047-841",
                "evidence_id": "existing-2019-984",
                "procedure_id": "existing-838047-decision",
                "relation_type": "supports_procedure",
            },
            {
                "link_id": "existing-430134-link",
                "case_id": "neeq-result-430134-844",
                "evidence_id": "existing-430134-decision",
                "relation_type": "supports_case",
            },
        ])

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_payload_preview_contains_three_cases_and_eighteen_search_states(self):
        payload = build_payload("2026-11-16T16:00:00+08:00")
        self.assertEqual(len(payload["evidence"]), 6)
        self.assertEqual(len(payload["links"]), 6)
        self.assertEqual(len(payload["searches"]), 18)
        self.assertEqual(
            sum(item["evidence_kind"] == "review_application" for item in payload["searches"]),
            4,
        )
        self.assertTrue(all(item["source_url"].startswith("https://") for item in payload["evidence"]))
        self.assertFalse(any(item["evidence_kind"] == "final_termination_notice" for item in payload["evidence"]))

    def test_apply_is_idempotent_and_does_not_write_formal_lifecycle_facts(self):
        payload = build_payload("2026-11-16T16:00:00+08:00")
        first = apply_payload(self.storage, payload)
        second = apply_payload(self.storage, payload)
        self.assertEqual(first, {"evidence": 6, "links": 6, "searches": 18})
        self.assertEqual(second, first)
        queue = list_neeq_termination_review_queue(self.storage, limit=100)
        osiqi = next(item for item in queue if item["company_code"] == "836614")
        erzhong = next(item for item in queue if item["company_code"] == "838047")
        keli = next(item for item in queue if item["company_code"] == "430134")
        self.assertEqual(osiqi["evidence_search_status"]["sponsor_notice"], "registered")
        self.assertEqual(osiqi["evidence_search_status"]["formal_decision"], "searched_not_found")
        self.assertEqual(erzhong["evidence_search_status"]["formal_decision"], "registered")
        self.assertEqual(keli["evidence_search_status"]["sponsor_notice"], "registered")
        with self.storage.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM neeq_termination_evidence").fetchone()[0], 8)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM neeq_termination_evidence_link").fetchone()[0], 8)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM neeq_termination_evidence_search").fetchone()[0], 18)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM security_lifecycle_events").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM security_master").fetchone()[0], 0)
            followup = connection.execute(
                """SELECT published_date, document_title, content_excerpt, metadata_json
                FROM neeq_termination_evidence
                WHERE evidence_id='neeq-evidence-836614-sponsor-20190829'"""
            ).fetchone()
            self.assertEqual(followup["published_date"], "2019-08-29")
            self.assertIn("仍应于2019年8月30日前披露", followup["content_excerpt"])
            self.assertIn("程序矛盾", followup["metadata_json"])
            date_sources = connection.execute(
                """SELECT evidence_id, published_date, source_url
                FROM neeq_termination_evidence
                WHERE evidence_id IN (
                    'neeq-evidence-836614-sponsor-20190719',
                    'neeq-evidence-836614-sponsor-20190722-dfcfw'
                )"""
            ).fetchall()
            self.assertEqual(
                {row["evidence_id"]: row["published_date"] for row in date_sources},
                {
                    "neeq-evidence-836614-sponsor-20190719": "2019-07-19",
                    "neeq-evidence-836614-sponsor-20190722-dfcfw": "2019-07-22",
                },
            )

    def test_apply_rejects_unbacked_registered_search_status(self):
        payload = build_payload("2026-11-16T16:00:00+08:00")
        payload["searches"][0]["search_status"] = "registered"
        with self.assertRaisesRegex(ValueError, "没有对应案件证据"):
            apply_payload(self.storage, payload)
        with self.storage.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM neeq_termination_evidence").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM neeq_termination_evidence_search").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
