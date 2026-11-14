# 本文件测试NEEQ终止挂牌公告解析、候选匹配及日期差异提示，不确认正式法律事实。
"""Tests for NEEQ termination notice parsing and review-only cross-checks."""

import unittest

from trendradar.market_data.neeq_termination_notice_parser import (
    build_neeq_notice_review_report,
    crosscheck_neeq_notice_task,
    parse_neeq_termination_notice_text,
)


NOTICE = """- 1 -
股转系统公告
2020
696 号
浙江新复大海洋生物科技股份有限公司（证券简称：新复大；证券代码：839401；主办券商：中泰证券）未能按照规定时间披露2019年半年度报告。根据业务规则，我司于2020年9月7日对新复大作出终止其股票挂牌的决定。
如新复大未在规定期限内提交复核申请或复核申请未被受理，我司将自2020年9月17日起终止其股票挂牌。
全国中小企业股份转让系统有限责任公司
2020年9月7日
"""


class NEEQTerminationNoticeParserTests(unittest.TestCase):
    def test_parses_identity_document_number_and_distinct_dates(self):
        result = parse_neeq_termination_notice_text(NOTICE)
        self.assertEqual(result["notice_count"], 1)
        notice = result["notices"][0]
        self.assertEqual(notice["document_number"], "2020-696")
        self.assertEqual(notice["security_code"], "839401")
        self.assertEqual(notice["short_name"], "新复大")
        self.assertEqual(notice["company_name"], "浙江新复大海洋生物科技股份有限公司")
        self.assertEqual(notice["decision_date"], "2020-09-07")
        self.assertEqual(notice["announcement_date"], "2020-09-07")
        self.assertEqual(notice["planned_termination_date"], "2020-09-17")
        self.assertEqual(notice["termination_reason"], "2019年半年度报告")
        self.assertTrue(notice["conditional_termination_language"])

    def test_parses_pdf_text_with_spaces_inside_chinese_phrases(self):
        text = """股 转 系 统 公 告
2020
695 号
武汉博奇玉宇环保股份有限公司 （证券简称： 玉宇环保； 证
券代码：831100； 主办券商： 国信证券） 未能按照规定时间披露
2019 年半年度报告。根据业务规则，我司于2020 年 9 月 7 日
对玉宇环保作出终止其股票挂牌的决定。
如玉宇环保未提交复核申请，我司将自 2020 年 9 月 17 日起终止其股票挂牌。
全国中小企业股份转让系统有限责任公司
2020 年 9 月 7 日"""
        notice = parse_neeq_termination_notice_text(text)["notices"][0]
        self.assertEqual(notice["document_number"], "2020-695")
        self.assertEqual(notice["security_code"], "831100")
        self.assertEqual(notice["short_name"], "玉宇环保")
        self.assertEqual(notice["company_name"], "武汉博奇玉宇环保股份有限公司")
        self.assertEqual(notice["decision_date"], "2020-09-07")
        self.assertEqual(notice["planned_termination_date"], "2020-09-17")
        self.assertEqual(notice["announcement_date"], "2020-09-07")

    def test_candidate_short_name_matches_notice_short_name(self):
        parsed = parse_neeq_termination_notice_text(NOTICE)
        result = crosscheck_neeq_notice_task(parsed, {
            "linked_candidates": [{
                "company_code": "839401",
                "company_name": "新复大",
                "delisting_date": "2020-09-17",
            }],
        })
        comparison = result["comparisons"][0]
        self.assertTrue(comparison["company_name_match"])
        self.assertFalse(comparison["company_full_name_exact_match"])
        self.assertTrue(comparison["company_short_name_match"])
        self.assertNotIn("company_name_mismatch", comparison["issues"])

    def test_parses_multiple_notices_in_one_document(self):
        text = NOTICE + "\n" + NOTICE.replace("839401", "831100").replace("新复大", "玉宇环保").replace("浙江新复大", "武汉博奇玉宇环保")
        result = parse_neeq_termination_notice_text(text)
        self.assertEqual(result["notice_count"], 2)
        self.assertEqual(
            [item["security_code"] for item in result["notices"]], ["839401", "831100"]
        )

    def test_crosscheck_flags_company_name_mismatch_and_date_difference(self):
        parsed = parse_neeq_termination_notice_text(NOTICE)
        task = {
            "attachment_id": "a1",
            "url": "https://www.neeq.com.cn/disclosure/a.pdf",
            "linked_candidates": [{
                "record_id": "1", "company_code": "839401",
                "company_name": "错误公司名称", "delisting_date": "2020-09-18",
            }],
        }
        result = crosscheck_neeq_notice_task(parsed, task, {
            "download_status": "downloaded", "text_extraction_status": "extracted",
        })
        comparison = result["comparisons"][0]
        self.assertTrue(comparison["company_code_match"])
        self.assertFalse(comparison["company_name_match"])
        self.assertFalse(comparison["result_list_date_matches_planned_date"])
        self.assertIn("company_name_mismatch", comparison["issues"])
        self.assertIn("result_date_vs_planned_date_mismatch", comparison["issues"])
        self.assertFalse(comparison["confirmed_legal_fact"])

    def test_unmatched_notice_code_is_reported(self):
        parsed = parse_neeq_termination_notice_text(NOTICE)
        result = crosscheck_neeq_notice_task(parsed, {
            "linked_candidates": [{"company_code": "831100", "company_name": "玉宇环保"}],
        })
        self.assertEqual(result["unmatched_notices"][0]["security_code"], "839401")

    def test_report_uses_text_loader_and_keeps_safety_flags(self):
        manifest = {"tasks": [{
            "attachment_id": "a1", "url": "https://www.neeq.com.cn/a.pdf",
            "linked_candidates": [{
                "record_id": "1", "company_code": "839401",
                "company_name": "浙江新复大海洋生物科技股份有限公司",
                "delisting_date": "2020-09-17",
            }],
        }]}
        state = {"results": {"a1": {
            "download_status": "downloaded", "text_extraction_status": "extracted",
            "text_file": "a1.txt",
        }}}
        report = build_neeq_notice_review_report(manifest, state, lambda name: NOTICE)
        self.assertEqual(report["attachment_count"], 1)
        self.assertEqual(report["parsed_notice_count"], 1)
        self.assertEqual(report["comparison_count"], 1)
        self.assertEqual(report["items"][0]["comparisons"][0]["company_name_match"], True)
        self.assertEqual(report["safety"]["database_writes"], 0)
        self.assertFalse(report["items"][0]["confirmed_legal_fact"])

    def test_rejects_empty_text_and_keeps_missing_fields_as_warnings(self):
        with self.assertRaises(ValueError):
            parse_neeq_termination_notice_text(" ")
        result = parse_neeq_termination_notice_text("股转系统公告\n2020 1号")
        self.assertIn("missing_security_code", result["notices"][0]["parse_warnings"])
        self.assertIsNone(result["notices"][0]["planned_termination_date"])


    def test_classifies_failure_to_disclose_as_conditional(self):
        notice = parse_neeq_termination_notice_text(NOTICE)["notices"][0]
        self.assertEqual(
            notice["termination_reason_category"], "failure_to_disclose_periodic_report"
        )
        self.assertTrue(notice["conditional_termination"])
        self.assertFalse(notice["notice_states_company_deregistered"])
        self.assertTrue(notice["follow_up_evidence_required"])

    def test_classifies_voluntary_dissolution_and_registration_cancellation(self):
        text = """股转系统公告
2021 402号
常熟联邦化工股份有限公司（证券简称：联邦化工；证券代码：832517；主办券商：某券商）因主动解散清算，已注销公司登记。根据业务规则第4.5.1条第（五）项规定，我司将自2021年5月12日起终止其股票挂牌。
全国中小企业股份转让系统有限责任公司
2021年4月23日"""
        notice = parse_neeq_termination_notice_text(text)["notices"][0]
        self.assertEqual(
            notice["termination_reason_category"], "voluntary_dissolution_liquidation"
        )
        self.assertEqual(notice["termination_legal_basis"], "第4.5.1条第（五）项")
        self.assertTrue(notice["notice_states_company_deregistered"])
        self.assertFalse(notice["conditional_termination"])

    def test_classifies_bankruptcy_liquidation_and_registration_cancellation(self):
        text = """股转系统公告
2021 894号
浙江腾骏祥科技股份有限公司（证券简称：ST腾骏；证券代码：871865；主办券商：某券商）因破产清算，已注销公司登记。根据业务规则第4.5.1条第（五）项规定，我司将自2021年9月8日起终止其股票挂牌。
全国中小企业股份转让系统有限责任公司
2021年8月31日"""
        notice = parse_neeq_termination_notice_text(text)["notices"][0]
        self.assertEqual(notice["termination_reason_category"], "bankruptcy_liquidation")
        self.assertTrue(notice["notice_states_company_deregistered"])
        self.assertFalse(notice["conditional_termination"])

    def test_result_date_mismatch_is_explicit_follow_up_not_legal_confirmation(self):
        parsed = parse_neeq_termination_notice_text(NOTICE)
        result = crosscheck_neeq_notice_task(parsed, {
            "linked_candidates": [{
                "record_id": "1016",
                "company_code": "839401",
                "company_name": "新复大",
                "delisting_date": "2021-03-22",
            }],
        })
        comparison = result["comparisons"][0]
        self.assertEqual(comparison["date_consistency_status"], "mismatch_requires_follow_up")
        self.assertTrue(comparison["follow_up_evidence_required"])
        self.assertFalse(comparison["confirmed_legal_fact"])
        self.assertFalse(comparison["formal_lifecycle_write_allowed"])

    def test_xianglian_831479_official_date_mismatch_is_preserved(self):
        text = """股转系统公告
2020 990号
湖南湘联节能科技股份有限公司（证券简称：湘联股份；证券代码：831479；主办券商：申万宏源承销保荐）未能按照规定时间披露2019年半年度报告。根据业务规则，我司于2021年1月5日对湘联股份作出终止其股票挂牌的决定。
如湘联股份未在规定期限内提交复核申请或复核申请未被受理，我司将自2021年1月15日起终止其股票挂牌。
全国中小企业股份转让系统有限责任公司
2021年1月5日"""
        parsed = parse_neeq_termination_notice_text(text)
        result = crosscheck_neeq_notice_task(parsed, {
            "linked_candidates": [{
                "record_id": "1016",
                "company_code": "831479",
                "company_name": "湘联股份",
                "delisting_date": "2021-03-22",
            }],
        })
        comparison = result["comparisons"][0]
        self.assertEqual(comparison["notice_planned_termination_date"], "2021-01-15")
        self.assertEqual(comparison["result_list_date"], "2021-03-22")
        self.assertEqual(comparison["date_consistency_status"], "mismatch_requires_follow_up")
        self.assertTrue(comparison["conditional_termination"])
        self.assertTrue(comparison["follow_up_evidence_required"])
        self.assertFalse(comparison["confirmed_legal_fact"])

    def test_result_date_match_has_matches_status(self):
        parsed = parse_neeq_termination_notice_text(NOTICE)
        result = crosscheck_neeq_notice_task(parsed, {
            "linked_candidates": [{
                "record_id": "1",
                "company_code": "839401",
                "company_name": "新复大",
                "delisting_date": "2020-09-17",
            }],
        })
        self.assertEqual(result["comparisons"][0]["date_consistency_status"], "matches")


if __name__ == "__main__":
    unittest.main()
