# 本文件测试NEEQ批量终止挂牌公告附表的证券代码、主办券商及名称差异识别。
"""Tests for batch NEEQ termination notice roster parsing."""

import unittest

from trendradar.market_data.neeq_termination_batch_parser import (
    match_neeq_result_record, parse_neeq_termination_roster,
)


NOTICE = """
股转公告〔2026〕305号
我司于 2026 年 9 月 29 日作出终止上述公司股票挂牌的决定。
附表
11 870099 华搜股份 万和证券
14 832480 ST 商会网 西南证券
"""


class BatchParserTests(unittest.TestCase):
    def test_extracts_roster_and_decision_date(self):
        roster = parse_neeq_termination_roster(NOTICE)
        self.assertIn("870099", roster["security_codes"])
        self.assertIn("832480", roster["security_codes"])
        self.assertNotIn("820024", roster["security_codes"])
        self.assertEqual(roster["document_number"], "股转公告〔2026〕305号")
        self.assertEqual(roster["decision_date"], "2026-09-29")

    def test_matches_by_code_and_broker_not_mutable_name(self):
        roster = parse_neeq_termination_roster(NOTICE)
        result = match_neeq_result_record({
            "companyCode": "832480", "companyName": "摘牌商网",
            "hostBroker": "西南证券",
        }, roster)
        self.assertTrue(result["roster_code_match"])
        self.assertTrue(result["broker_match"])
        self.assertFalse(result["name_match"])
        self.assertEqual(result["match_status"], "code_and_broker_match")

    def test_unknown_code_fails_closed(self):
        roster = parse_neeq_termination_roster(NOTICE)
        result = match_neeq_result_record({"companyCode": "000001"}, roster)
        self.assertFalse(result["roster_code_match"])
        self.assertEqual(result["match_status"], "code_not_found_in_batch_notice")


if __name__ == "__main__":
    unittest.main()
