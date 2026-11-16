# 本文件测试NEEQ案件时间线查询、人工复核追加保存及证据引用安全边界。
"""Tests for NEEQ termination case timeline and append-only review service."""

import tempfile
import unittest
from pathlib import Path

from trendradar.market_data.neeq_termination_review import (
    append_neeq_termination_case_review,
    get_neeq_termination_case_timeline,
    list_neeq_termination_review_queue,
)
from trendradar.market_data.storage import MarketStorage


class NEEQTerminationReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.temp_dir.name) / "market.db"))
        self.case_id = "neeq-result-838047-841"
        self.other_case_id = "neeq-result-836614-842"
        self.evidence_id = "neeq-evidence-2019-984"
        self.procedure_id = "neeq-procedure-838047-decision"
        self.storage.save_neeq_termination_cases([
            {
                "case_id": self.case_id, "company_code": "838047",
                "company_name_snapshot": "二中科技", "source_record_id": "841",
                "source_api": "neeq_termination_result_api",
                "interface_result_date": "2019-09-06",
                "raw_record": {"id": 841, "companyCode": "838047"},
            },
            {
                "case_id": self.other_case_id, "company_code": "836614",
                "company_name_snapshot": "奥其斯", "source_record_id": "842",
                "source_api": "neeq_termination_result_api",
                "interface_result_date": "2019-09-06",
                "raw_record": {"id": 842, "companyCode": "836614"},
            },
        ])
        self.storage.save_neeq_termination_evidence([{
            "evidence_id": self.evidence_id, "evidence_kind": "batch_notice",
            "source_authority": "neeq_official", "notice_number": "2019-984",
            "document_title": "2019-984终止挂牌公告",
            "source_url": "https://www.neeq.com.cn/important_news/200006230.html",
            "published_date": "2019-07-01",
            "retrieved_at": "2026-11-16T12:00:00+08:00",
            "evidence_status": "crosschecked",
        }])
        self.storage.save_neeq_termination_procedures([{
            "procedure_id": self.procedure_id, "case_id": self.case_id,
            "procedure_type": "decision_issued", "event_date": "2019-07-01",
            "date_semantics": "decision_issued", "date_status": "pending",
            "notes": "公告载明决定日期，仍待逐案复核。",
        }])
        self.storage.link_neeq_termination_evidence([{
            "link_id": "neeq-link-838047-984", "case_id": self.case_id,
            "evidence_id": self.evidence_id, "procedure_id": self.procedure_id,
            "relation_type": "supports_procedure",
        }])

    def tearDown(self):
        self.temp_dir.cleanup()

    def _review(self, **overrides):
        value = {
            "case_id": self.case_id,
            "evidence_id": self.evidence_id,
            "procedure_id": self.procedure_id,
            "review_status": "needs_more_evidence",
            "conclusion_code": "decision_evidence_only",
            "reviewer": "reviewer-1",
            "reviewed_at": "2026-11-16T13:00:00+08:00",
            "review_notes": "尚缺复核决定及最终终止挂牌公告。",
        }
        value.update(overrides)
        return value

    def test_timeline_contains_interface_candidate_evidence_procedure_and_review(self):
        review_id = append_neeq_termination_case_review(self.storage, self._review())
        dossier = get_neeq_termination_case_timeline(self.storage, self.case_id)
        self.assertEqual(dossier["case"]["company_code"], "838047")
        self.assertEqual(dossier["case"]["raw_record"]["id"], 841)
        self.assertEqual(len(dossier["evidence"]), 1)
        self.assertEqual(len(dossier["procedures"]), 1)
        self.assertEqual(dossier["reviews"][0]["review_id"], review_id)
        interface_event = next(
            item for item in dossier["timeline"]
            if item["timeline_type"] == "interface_result_date"
        )
        self.assertEqual(interface_event["date_status"], "unverified")
        self.assertEqual(interface_event["date_semantics"], "official_result_api_candidate")
        self.assertEqual(
            [item["event_date"] for item in dossier["timeline"]],
            sorted(item["event_date"] for item in dossier["timeline"]),
        )

    def test_reviews_are_append_only_and_keep_history(self):
        first = append_neeq_termination_case_review(self.storage, self._review())
        second = append_neeq_termination_case_review(self.storage, self._review(
            review_status="pending", reviewer="reviewer-2",
            reviewed_at="2026-11-16T14:00:00+08:00",
            review_notes="继续检索官方补充公告。",
        ))
        dossier = get_neeq_termination_case_timeline(self.storage, self.case_id)
        self.assertNotEqual(first, second)
        self.assertEqual(len(dossier["reviews"]), 2)
        self.assertEqual(dossier["reviews"][0]["reviewer"], "reviewer-1")
        self.assertEqual(dossier["reviews"][1]["reviewer"], "reviewer-2")

    def test_review_cannot_reference_evidence_from_another_case(self):
        with self.assertRaisesRegex(ValueError, "证据未关联"):
            append_neeq_termination_case_review(
                self.storage, self._review(case_id=self.other_case_id)
            )

    def test_review_cannot_reference_procedure_from_another_case(self):
        with self.assertRaisesRegex(ValueError, "程序节点不属于"):
            append_neeq_termination_case_review(
                self.storage, self._review(procedure_id="missing-procedure")
            )

    def test_review_cannot_confirm_actual_termination_fact(self):
        with self.assertRaisesRegex(ValueError, "不允许确认"):
            append_neeq_termination_case_review(
                self.storage, self._review(
                    review_status="verified",
                    conclusion_code="actual_termination_confirmed",
                )
            )
        dossier = get_neeq_termination_case_timeline(self.storage, self.case_id)
        self.assertEqual(dossier["reviews"], [])

    def test_review_requires_iso_datetime_and_reviewer(self):
        with self.assertRaisesRegex(ValueError, "ISO 8601"):
            append_neeq_termination_case_review(
                self.storage, self._review(reviewed_at="2019-99-99")
            )
        with self.assertRaisesRegex(ValueError, "reviewer"):
            append_neeq_termination_case_review(
                self.storage, self._review(reviewer="")
            )
        with self.assertRaisesRegex(ValueError, "时区偏移"):
            append_neeq_termination_case_review(
                self.storage, self._review(reviewed_at="2026-11-16T13:00:00")
            )

    def test_review_queue_prioritizes_date_mismatch_and_lists_missing_evidence(self):
        self.storage.save_neeq_termination_procedures([{
            "procedure_id": "neeq-procedure-838047-conditional",
            "case_id": self.case_id,
            "procedure_type": "conditional_termination",
            "event_date": "2019-07-09",
            "date_semantics": "conditional_termination",
            "date_status": "pending",
            "notes": "公告载明的条件终止日期。",
        }])
        queue = list_neeq_termination_review_queue(self.storage)
        self.assertEqual(queue[0]["case_id"], self.case_id)
        self.assertEqual(queue[0]["priority"], 0)
        self.assertTrue(queue[0]["date_mismatch"])
        self.assertEqual(queue[0]["mismatch_dates"], ["2019-07-09"])
        self.assertIn("final_termination_notice", queue[0]["missing_evidence_kinds"])
        self.assertFalse(queue[0]["actual_termination_confirmed"])
        self.assertTrue(any("复核申请受理" in action for action in queue[0]["next_actions"]))

    def test_background_batch_membership_does_not_count_as_formal_decision(self):
        self.storage.save_neeq_termination_evidence([{
            "evidence_id": "neeq-evidence-background",
            "evidence_kind": "batch_notice",
            "source_authority": "neeq_official",
            "notice_number": "2019-984",
            "document_title": "背景名单",
            "source_url": "https://www.neeq.com.cn/important_news/200006230.html",
            "published_date": "2019-07-01",
            "retrieved_at": "2026-11-16T12:00:00+08:00",
            "evidence_status": "crosschecked",
            "metadata": {"membership_type": "background_pending_company"},
        }])
        self.storage.link_neeq_termination_evidence([{
            "link_id": "neeq-link-background",
            "case_id": self.other_case_id,
            "evidence_id": "neeq-evidence-background",
            "relation_type": "context_only",
        }])
        queue = list_neeq_termination_review_queue(self.storage)
        other = next(item for item in queue if item["case_id"] == self.other_case_id)
        self.assertIn("formal_decision", other["missing_evidence_kinds"])

    def test_evidence_search_history_is_append_only_and_visible_in_queue(self):
        self.storage.record_neeq_termination_evidence_search({
            "search_id": "search-838047-review-not-found",
            "case_id": self.case_id,
            "evidence_kind": "review_decision",
            "search_status": "searched_not_found",
            "searched_at": "2026-11-16T15:00:00+08:00",
            "search_scope": "全国股转系统公开公告检索",
            "search_notes": "当前检索范围内未找到复核决定，不代表不存在。",
            "source_urls": ["https://www.neeq.com.cn/disclosure/termination_listing.html"],
            "reviewer": "tester",
        })
        self.storage.record_neeq_termination_evidence_search({
            "search_id": "search-838047-sponsor-found",
            "case_id": self.case_id,
            "evidence_kind": "sponsor_notice",
            "search_status": "found_not_registered",
            "searched_at": "2026-11-16T15:01:00+08:00",
            "search_scope": "主办券商公告检索",
            "source_urls": ["https://example.org/sponsor.pdf"],
            "reviewer": "tester",
        })
        dossier = get_neeq_termination_case_timeline(self.storage, self.case_id)
        self.assertEqual(len(dossier["evidence_searches"]), 2)
        queue = list_neeq_termination_review_queue(self.storage)
        case = next(item for item in queue if item["case_id"] == self.case_id)
        self.assertEqual(case["evidence_search_status"]["batch_notice"], "registered")
        self.assertEqual(case["evidence_search_status"]["review_decision"], "searched_not_found")
        self.assertEqual(case["evidence_search_status"]["sponsor_notice"], "found_not_registered")

    def test_evidence_search_rejects_naive_datetime_and_non_https_source(self):
        base = {
            "search_id": "search-invalid",
            "case_id": self.case_id,
            "evidence_kind": "final_termination_notice",
            "search_status": "searched_not_found",
            "searched_at": "2026-11-16T15:00:00",
            "source_urls": [],
        }
        with self.assertRaisesRegex(ValueError, "时区偏移"):
            self.storage.record_neeq_termination_evidence_search(base)
        base["searched_at"] = "2026-11-16T15:00:00+08:00"
        base["source_urls"] = ["http://example.org"]
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            self.storage.record_neeq_termination_evidence_search(base)

    def test_review_queue_validates_limit(self):
        with self.assertRaisesRegex(ValueError, "limit"):
            list_neeq_termination_review_queue(self.storage, limit=0)

    def test_missing_case_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "案件不存在"):
            get_neeq_termination_case_timeline(self.storage, "missing-case")
        with self.assertRaisesRegex(ValueError, "案件不存在"):
            append_neeq_termination_case_review(
                self.storage, self._review(case_id="missing-case")
            )


if __name__ == "__main__":
    unittest.main()
