# 测试 NEEQ dataType=0 公告批次交叉核验。
# -*- coding: utf-8 -*-
"""测试 NEEQ dataType=0 公告批次交叉核验报告。"""
import unittest

from scripts.market_data.audit_neeq_datatype0_notice_crosscheck import build_report


class NeeqDataType0NoticeCrosscheckTests(unittest.TestCase):
    def _candidate(self, code, record_id, data_type=0, date="2019-09-06"):
        return {
            "record_id": record_id, "company_code": code, "company_name": code,
            "delisting_date": date, "attachment_url": "https://www.neeq.com.cn/example",
            "raw_record_sha256": "a" * 64,
            "raw_record": {
                "id": int(record_id), "companyCode": code, "companyName": code,
                "dataType": data_type, "delistingReason": "测试原因", "createTime": {"time": 1},
            },
        }

    def _batch_report(self):
        return {
            "audit_version": "neeq_termination_batch_reconciliation_v2",
            "batch_memberships": [
                {
                    "security_code": "838047", "membership_type": "background_pending_company",
                    "batch_notice_number": "2019-984",
                    "source_attachment_url": "https://www.neeq.com.cn/important_news/200006230.html",
                    "issues": ["background_membership_is_not_a_formal_decision"],
                    "confirmed_legal_fact": False, "formal_lifecycle_write_allowed": False,
                },
                {
                    "security_code": "838047", "membership_type": "formal_decision_company",
                    "batch_notice_number": "2019-984",
                    "source_attachment_url": "https://www.neeq.com.cn/important_news/200006230.html",
                    "issues": [], "confirmed_legal_fact": False, "formal_lifecycle_write_allowed": False,
                },
            ],
            "candidate_coverage": [
                {
                    "security_code": "838047", "coverage_status": "matched_to_batch_notice",
                    "batch_notice_numbers": ["2019-984"],
                    "batch_membership_types": ["background_pending_company", "formal_decision_company"],
                    "has_formal_decision_membership": True, "has_background_membership": True,
                    "parsed_batch_membership_found": True,
                },
                {
                    "security_code": "999999", "coverage_status": "batch_coverage_not_exhaustive",
                    "batch_notice_numbers": [], "batch_membership_types": [],
                    "has_formal_decision_membership": False, "has_background_membership": False,
                    "parsed_batch_membership_found": False,
                },
            ],
        }

    def test_preserves_both_background_and_formal_memberships(self):
        report = build_report({"manifest": {"candidates": [self._candidate("838047", "841")]}}, self._batch_report())
        row = report["candidates"][0]
        self.assertEqual(len(row["batch_memberships"]), 2)
        self.assertEqual({x["membership_type"] for x in row["batch_memberships"]},
                         {"background_pending_company", "formal_decision_company"})
        self.assertEqual(report["summary"]["dual_background_and_formal_membership_codes"], ["838047"])

    def test_keeps_unmatched_as_not_proof_of_no_notice(self):
        report = build_report({"manifest": {"candidates": [self._candidate("999999", "900")]}}, self._batch_report())
        row = report["candidates"][0]
        self.assertEqual(row["batch_relation_status"], "no_parsed_batch_membership_found_not_proof_of_no_notice")
        self.assertIn("不代表历史上不存在公告", row["candidate_coverage"]["limitation"])

    def test_filters_only_data_type_zero(self):
        manifest = {"manifest": {"candidates": [
            self._candidate("838047", "841", 0), self._candidate("999999", "902", 1),
        ]}}
        report = build_report(manifest, self._batch_report())
        self.assertEqual(report["summary"]["data_type0_candidate_count"], 1)
        self.assertEqual([x["security_code"] for x in report["candidates"]], ["838047"])

    def test_adds_independent_later_decision_without_overwriting_batch_identity(self):
        report = build_report({"manifest": {"candidates": [self._candidate("836614", "842")]}}, self._batch_report())
        row = report["candidates"][0]
        self.assertEqual(row["supplemental_independent_decisions"][0]["notice_number"], "2019-1071")
        self.assertEqual(row["batch_memberships"], [])
        self.assertEqual(row["interface_result_date"], "2019-09-06")

    def test_adds_independently_crosschecked_notice_leads(self):
        candidates = [
            self._candidate("836654", "756"),
            self._candidate("839832", "768"),
            self._candidate("836554", "799"),
            self._candidate("430134", "844"),
            self._candidate("832115", "1067"),
            self._candidate("833028", "1068"),
            self._candidate("833292", "843"),
        ]
        report = build_report({"manifest": {"candidates": candidates}}, self._batch_report())
        by_code = {x["security_code"]: x for x in report["candidates"]}
        self.assertEqual(by_code["836654"]["supplemental_independent_decisions"][0]["notice_number"], "2019-1354")
        self.assertEqual(by_code["839832"]["supplemental_independent_decisions"][0]["conditional_termination_date"], "2019-10-28")
        self.assertEqual(by_code["836554"]["supplemental_independent_decisions"][0]["notice_number"], "2019-1708")
        self.assertEqual(by_code["430134"]["supplemental_independent_decisions"][0]["conditional_termination_date"], "2020-02-25")
        self.assertEqual(by_code["832115"]["supplemental_independent_decisions"][0]["reported_final_delisting_date"], "2021-10-21")
        self.assertEqual(by_code["833028"]["supplemental_leads"][0]["status"], "unverified_lead")
        self.assertEqual(by_code["833292"]["supplemental_independent_decisions"][0]["evidence_status"],
                         "third_party_notice_index_lead_not_officially_verified")

    def test_report_is_explicitly_read_only(self):
        report = build_report({"manifest": {"candidates": [self._candidate("838047", "841")]}}, self._batch_report())
        self.assertTrue(report["safety"]["read_only"])
        self.assertEqual(report["safety"]["database_writes"], 0)
        self.assertEqual(report["safety"]["formal_lifecycle_writes"], 0)
        self.assertFalse(report["safety"]["data_type_semantics_inferred"])
        self.assertFalse(report["candidates"][0]["confirmed_legal_fact"])


if __name__ == "__main__":
    unittest.main()
