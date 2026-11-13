# 本文件测试全国股转系统动态分类解析、退市公告筛选和检索计划的安全约束。
"""Tests for NEEQ lifecycle announcement search planning."""

import unittest

from trendradar.market_data.neeq_lifecycle_collector import (
    build_neeq_announcement_search_plan,
    extract_neeq_categories,
    find_neeq_lifecycle_categories,
    is_lifecycle_announcement_title,
    normalize_neeq_announcement_response,
)


class NEEQLifecycleCollectorTests(unittest.TestCase):
    def test_extracts_dynamic_parent_and_child_categories(self):
        payload = [{
            "list": [{
                "title": "公司公告",
                "datebaseNumber": "P100",
                "businessNumber": "B100",
                "sonList": [{
                    "title": "终止挂牌公告",
                    "datebaseNumber": "C200",
                    "businessNumber": "B200",
                }],
            }]
        }]
        categories = extract_neeq_categories(payload)
        self.assertEqual(len(categories), 2)
        self.assertEqual(categories[0].search_key, "0:P100")
        self.assertEqual(categories[1].search_key, "1:C200")
        self.assertEqual(categories[1].parent_title, "公司公告")
        self.assertEqual(
            [item.title for item in find_neeq_lifecycle_categories(categories)],
            ["终止挂牌公告"],
        )

    def test_category_parser_accepts_observed_object_envelope_and_fails_closed(self):
        self.assertEqual(
            len(extract_neeq_categories({"list": [{"title": "公告", "datebaseNumber": "1"}]})),
            1,
        )
        self.assertEqual(extract_neeq_categories({"data": []}), [])
        self.assertEqual(extract_neeq_categories(None), [])

    def test_builds_allowlisted_query_plan_without_guessing_method_or_dictionaries(self):
        plan = build_neeq_announcement_search_plan(
            start_date="2020-01-01",
            end_date="2026-11-13",
            keyword="终止挂牌",
            market_layer="3",
            page=0,
            need_fields=("companyCd", "companyName", "disclosureTitle", "destFilePath", "publishDate"),
        )
        self.assertTrue(plan["endpoint_allowed"])
        self.assertEqual(plan["http_method"], "POST")
        self.assertEqual(plan["request_encoding"], "application/x-www-form-urlencoded + JSONP callback")
        self.assertEqual(plan["params"]["page"], 0)
        self.assertFalse(plan["runtime_verified"])
        self.assertEqual(plan["params"]["isNewThree"], 1)
        self.assertEqual(plan["params"]["xxfcbj"], "3")
        self.assertEqual(plan["params"]["needFields"][0], "companyCd")
        self.assertEqual(plan["params"]["noticeType"], "")

    def test_rejects_unconfirmed_market_layer_and_invalid_query_values(self):
        for layer in ("2", "4", ""):
            with self.subTest(layer=layer), self.assertRaises(ValueError):
                build_neeq_announcement_search_plan(
                    start_date="2020-01-01", end_date="2026-01-01",
                    market_layer=layer,
                )
        with self.assertRaises(ValueError):
            build_neeq_announcement_search_plan(
                start_date="2026-02-01", end_date="2026-01-01",
            )
        with self.assertRaises(ValueError):
            build_neeq_announcement_search_plan(
                start_date="2020-01-01", end_date="2026-01-01", page=-1,
            )
        with self.assertRaises(ValueError):
            build_neeq_announcement_search_plan(
                start_date="2020-01-01", end_date="2026-01-01", company_code="123",
            )

    def test_accepts_zero_based_first_page(self):
        plan = build_neeq_announcement_search_plan(
            start_date="2020-01-01", end_date="2026-01-01", page=0,
        )
        self.assertEqual(plan["params"]["page"], 0)

    def test_normalizes_only_observed_list_info_response_shape(self):
        result = normalize_neeq_announcement_response([{
            "listInfo": {"content": [{"companyCd": "123456"}], "totalElements": 1}
        }])
        self.assertEqual(result["total_elements"], 1)
        self.assertEqual(result["records"][0]["companyCd"], "123456")
        self.assertFalse(result["runtime_verified"])
        with self.assertRaises(ValueError):
            normalize_neeq_announcement_response({"listInfo": {"content": [], "totalElements": 0}})

    def test_title_filter_only_flags_candidates(self):
        self.assertTrue(is_lifecycle_announcement_title("关于公司股票终止挂牌的公告"))
        self.assertFalse(is_lifecycle_announcement_title("关于召开年度股东大会的通知"))
        self.assertFalse(is_lifecycle_announcement_title(None))


if __name__ == "__main__":
    unittest.main()
