# 本文件验证NEEQ公告标题分类只提供人工复核线索，不把标题当作正式法律事实。
"""Tests for conservative NEEQ notice title classification."""

import unittest

from trendradar.market_data.neeq_notice_classifier import classify_neeq_notice


class NEEQNoticeClassifierTests(unittest.TestCase):
    def test_risk_warning_takes_precedence_over_termination_phrase(self):
        result = classify_neeq_notice(
            "[券商公告]华搜股份:股票进入摘牌整理期交易并将被终止挂牌的第十次风险提示性公告"
        )
        self.assertEqual(result["category"], "delisting_arrangement_risk_notice")
        self.assertEqual(result["issuer_role_hint"], "broker_or_sponsor_notice")
        self.assertFalse(result["confirmed_legal_fact"])
        self.assertTrue(result["review_required"])

    def test_formal_termination_announcement_is_still_only_a_candidate(self):
        result = classify_neeq_notice(
            "[临时公告]摘牌商网:关于公司股票在全国中小企业股份转让系统终止挂牌的公告"
        )
        self.assertEqual(result["category"], "formal_termination_notice_candidate")
        self.assertEqual(result["issuer_role_hint"], "issuer_or_other_notice")
        self.assertFalse(result["confirmed_legal_fact"])

    def test_decision_notice_is_distinct_from_final_termination_notice(self):
        result = classify_neeq_notice("关于收到全国股转公司终止挂牌决定的公告")
        self.assertEqual(result["category"], "delisting_decision_notice_candidate")

    def test_unknown_title_fails_closed(self):
        result = classify_neeq_notice("关于公司年度报告的公告")
        self.assertEqual(result["category"], "unclassified")
        self.assertFalse(result["confirmed_legal_fact"])

    def test_rejects_non_string_title(self):
        with self.assertRaises(TypeError):
            classify_neeq_notice(None)


if __name__ == "__main__":
    unittest.main()
