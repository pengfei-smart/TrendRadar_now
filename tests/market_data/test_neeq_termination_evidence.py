# 本文件验证NEEQ终止挂牌证据评估的身份、日期及公告性质边界。
"""Tests for conservative NEEQ termination evidence assessment."""

import unittest

from trendradar.market_data.neeq_termination_evidence import assess_neeq_termination_evidence


def candidate(title, codes, dates, issuer_code="832480", issuer_hint=True):
    payload = {
        "parsed_evidence": {"security_codes": codes, "date_evidence": dates},
    }
    if issuer_hint:
        payload["issuer_identity_hint"] = {
            "issuer_code": issuer_code,
            "match_method": "exact_attachment_path",
            "identity_resolution_status": "metadata_linked_pending_review",
        }
    return {"source_title": title, "payload_json": payload}


class NEEQTerminationEvidenceTests(unittest.TestCase):
    def test_formal_notice_with_matching_body_code_and_delisting_date_is_review_candidate(self):
        result = assess_neeq_termination_evidence(candidate(
            "[临时公告]摘牌商网:关于公司股票终止挂牌的公告",
            ["832480"],
            [{"value": "2026-11-04", "date_type": "delisting_date",
              "date_status": "unknown", "context": "公司股票将于2026年11月4日终止挂牌"}],
        ))
        self.assertEqual(result["outcome"], "candidate_for_human_legal_review")
        self.assertFalse(result["confirmed_legal_fact"])
        self.assertFalse(result["formal_lifecycle_write_allowed"])

    def test_broker_risk_warning_is_never_final_proof(self):
        result = assess_neeq_termination_evidence(candidate(
            "[券商公告]华搜股份:股票将被终止挂牌的风险提示公告",
            ["870099"],
            [{"value": "2026-11-04", "date_type": "delisting_date",
              "date_status": "unknown", "context": "将于2026年11月4日终止挂牌"}],
            issuer_code="870099",
        ))
        self.assertEqual(result["outcome"], "risk_or_intermediary_notice_not_final_proof")

    def test_metadata_linked_without_body_code_requires_identity_review(self):
        result = assess_neeq_termination_evidence(candidate(
            "[临时公告]华搜股份:关于公司股票终止挂牌的公告",
            [],
            [{"value": "2026-11-04", "date_type": "delisting_date",
              "date_status": "unknown", "context": "公司股票将于2026年11月4日终止挂牌"}],
            issuer_code="870099",
        ))
        self.assertEqual(result["outcome"], "identity_review_required")
        self.assertEqual(result["identity_status"], "official_metadata_linked_identity_pending_review")

    def test_last_trading_day_is_not_delisting_date(self):
        result = assess_neeq_termination_evidence(candidate(
            "[临时公告]摘牌商网:关于公司股票终止挂牌的公告",
            ["832480"],
            [{"value": "2026-11-03", "date_type": "delisting_date",
              "date_status": "unknown", "context": "2026年11月3日系摘牌整理期最后一个交易日"}],
        ))
        self.assertEqual(result["outcome"], "explicit_termination_date_not_established")
        self.assertEqual(result["delisting_date_evidence"], [])

    def test_missing_identity_hint_fails_closed(self):
        result = assess_neeq_termination_evidence(candidate(
            "[临时公告]摘牌商网:关于公司股票终止挂牌的公告",
            ["832480"],
            [{"value": "2026-11-04", "date_type": "delisting_date",
              "date_status": "unknown", "context": "公司股票将于2026年11月4日终止挂牌"}],
            issuer_hint=False,
        ))
        self.assertEqual(result["outcome"], "identity_review_required")

    def test_non_formal_notice_does_not_qualify(self):
        result = assess_neeq_termination_evidence(candidate(
            "关于收到终止挂牌决定的公告", ["832480"],
            [{"value": "2026-11-04", "date_type": "delisting_date",
              "date_status": "unknown", "context": "将于2026年11月4日终止挂牌"}],
        ))
        self.assertEqual(result["outcome"], "formal_termination_notice_title_not_established")


if __name__ == "__main__":
    unittest.main()
