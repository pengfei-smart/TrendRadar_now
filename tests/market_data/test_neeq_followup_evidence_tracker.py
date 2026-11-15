# 本文件测试NEEQ后续公告证据追踪任务、证据关联和只读安全边界。
"""Tests for NEEQ follow-up evidence tracker."""
import unittest
from trendradar.market_data.neeq_followup_evidence_tracker import build_neeq_followup_evidence_tracker

class NEEQFollowupEvidenceTrackerTests(unittest.TestCase):
    def report(self):
        return {
            "audit_version": "neeq_date_reconciliation_audit_v1",
            "date_reconciliation_cases": [
                {"security_code":"831479","company_short_name":"湘联股份","notice_document_number":"2020-990",
                 "notice_decision_date":"2021-01-05","notice_announcement_date":"2021-01-05",
                 "notice_planned_termination_date":"2021-01-15","official_result_delisting_date":"2021-03-22",
                 "official_result_record_id":"1016","source_attachment_url":"https://www.neeq.com.cn/notice.pdf",
                 "date_reconciliation_status":"later_than_planned","date_difference_days":66},
                {"security_code":"834502","company_short_name":"富海银涛","notice_document_number":"2017-684",
                 "official_result_delisting_date":"2017-12-21","date_reconciliation_status":"pending_manual_review"},
            ],
            "supplemental_candidate_evidence": [
                {"security_code":"837689","company_short_name":"圣保堂","notice_document_number":"2019-987",
                 "source_attachment_url":"https://www.neeq.com.cn/notice.html",
                 "candidate_status":"supplemental_candidate_pending_manual_evidence_review"}
            ],
        }

    def test_builds_tasks_for_cases_and_supplemental_candidate(self):
        result = build_neeq_followup_evidence_tracker(self.report())
        self.assertEqual(result["summary"]["task_count"], 3)
        self.assertEqual(result["summary"]["high_priority_task_count"], 1)
        self.assertEqual(result["summary"]["supplemental_candidate_task_count"], 1)
        self.assertEqual({x["security_code"] for x in result["tasks"]}, {"831479","834502","837689"})
        self.assertTrue(result["safety"]["read_only"])

    def test_attaches_manual_evidence_by_security_and_notice(self):
        evidence = [{"security_code":"831479","notice_document_number":"2020-990",
            "evidence_type":"reconsideration_decision","source_title":"复核决定公告",
            "source_url":"https://www.neeq.com.cn/followup.pdf","source_document_number":"后续公告编号",
            "published_date":"2021-02-01","source_sha256":"a"*64,
            "related_dates":{"decision_date":"2021-02-01"}}]
        result = build_neeq_followup_evidence_tracker(self.report(), evidence)
        task = next(x for x in result["tasks"] if x["security_code"] == "831479")
        self.assertEqual(task["evidence_count"], 1)
        self.assertEqual(task["evidence_records"][0]["evidence_type"], "reconsideration_decision")
        self.assertEqual(task["evidence_records"][0]["review_status"], "pending_manual_review")
        self.assertFalse(task["evidence_records"][0]["confirmed_legal_fact"])

    def test_keeps_unmatched_or_invalid_evidence_visible(self):
        evidence = [{"security_code":"999999","evidence_type":"other_official_document"},
                    {"security_code":"831479","evidence_type":"guess"}, {"evidence_type":"other_official_document"}]
        result = build_neeq_followup_evidence_tracker(self.report(), evidence)
        self.assertEqual(result["summary"]["unlinked_evidence_count"], 3)
        self.assertEqual(result["summary"]["evidence_record_count"], 0)

    def test_deduplicates_identical_evidence_records(self):
        row = {"security_code":"831479","notice_document_number":"2020-990",
               "evidence_type":"other_official_document","source_url":"https://www.neeq.com.cn/doc.pdf",
               "source_sha256":"b"*64}
        result = build_neeq_followup_evidence_tracker(self.report(), [row,row])
        self.assertEqual(result["summary"]["evidence_record_count"], 1)

    def test_never_promotes_input_claims_to_legal_facts(self):
        row = {"security_code":"831479","notice_document_number":"2020-990",
               "evidence_type":"final_termination_announcement","confirmed_legal_fact":True,
               "formal_lifecycle_write_allowed":True}
        result = build_neeq_followup_evidence_tracker(self.report(), [row])
        task = next(x for x in result["tasks"] if x["security_code"] == "831479")
        self.assertFalse(task["evidence_records"][0]["confirmed_legal_fact"])
        self.assertFalse(task["formal_lifecycle_write_allowed"])

if __name__ == "__main__":
    unittest.main()
