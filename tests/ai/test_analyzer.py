import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from trendradar.ai.analyzer import AIAnalyzer
from trendradar.ai.request_guard import AIRequestNotAllowedError


class AIAnalyzerTests(unittest.TestCase):

    def setUp(self):
        # 解析测试不需要初始化 AIClient，也不需要真实 API。
        self.analyzer = object.__new__(AIAnalyzer)
        self.analyzer.debug = False

    def valid_strategy(self):
        labels = [
            "天时",
            "地利",
            "人和",
            "风险等级",
            "建议总体仓位",
            "反向利空路径",
            "验证点",
            "今日核心观察点",
            "盘中触发器",
            "离场触发器",
            "进攻方向",
            "风险红线",
        ]
        return (
            "1. 中国A股短线交易者：\n"
            + "\n".join(f"{label}：未知/无法确定" for label in labels)
        )

    def valid_payload(self):
        return {
            "core_trends": "核心趋势分析",
            "sentiment_controversy": "舆情分析",
            "signals": "异动信号",
            "rss_insights": "RSS 洞察",
            "outlook_strategy": self.valid_strategy(),
            "standalone_summaries": {"知乎": "热点摘要"},
        }

    def test_parse_valid_json(self):
        response = json.dumps(self.valid_payload(), ensure_ascii=False)

        result = self.analyzer._parse_response(response)

        self.assertTrue(result.success)
        self.assertEqual("核心趋势分析", result.core_trends)
        self.assertEqual({"知乎": "热点摘要"}, result.standalone_summaries)

    def test_parse_json_with_bom(self):
        response = "\ufeff" + json.dumps(
            self.valid_payload(), ensure_ascii=False
        )

        result = self.analyzer._parse_response(response)

        self.assertTrue(result.success)

    def test_parse_json_inside_markdown_code_block(self):
        response = (
            "以下是分析结果：\n```json\n"
            + json.dumps(self.valid_payload(), ensure_ascii=False)
            + "\n```\n"
        )

        result = self.analyzer._parse_response(response)

        self.assertTrue(result.success)

    def test_parse_json_with_surrounding_text(self):
        response = (
            "分析结果如下：\n"
            + json.dumps(self.valid_payload(), ensure_ascii=False)
            + "\n以上为分析结果。"
        )

        result = self.analyzer._parse_response(response)

        self.assertTrue(result.success)

    def test_empty_response_is_rejected(self):
        result = self.analyzer._parse_response("")

        self.assertFalse(result.success)
        self.assertIn("空响应", result.error)

    def test_missing_required_field_is_rejected(self):
        payload = self.valid_payload()
        del payload["signals"]

        result = self.analyzer._parse_response(
            json.dumps(payload, ensure_ascii=False)
        )

        self.assertFalse(result.success)
        self.assertIn("signals", result.error)

    def test_empty_required_field_is_rejected(self):
        payload = self.valid_payload()
        payload["core_trends"] = "   "

        result = self.analyzer._parse_response(
            json.dumps(payload, ensure_ascii=False)
        )

        self.assertFalse(result.success)
        self.assertIn("core_trends", result.error)

    def test_wrong_required_field_type_is_rejected(self):
        payload = self.valid_payload()
        payload["signals"] = ["不是字符串"]

        result = self.analyzer._parse_response(
            json.dumps(payload, ensure_ascii=False)
        )

        self.assertFalse(result.success)
        self.assertIn("signals", result.error)

    def test_missing_strategy_label_is_rejected(self):
        payload = self.valid_payload()
        payload["outlook_strategy"] = payload[
            "outlook_strategy"
        ].replace("天时：未知/无法确定\n", "")

        result = self.analyzer._parse_response(
            json.dumps(payload, ensure_ascii=False)
        )

        self.assertFalse(result.success)
        self.assertIn("天时", result.error)

    def test_duplicate_strategy_label_is_rejected(self):
        payload = self.valid_payload()
        payload["outlook_strategy"] += "\n天时：重复标签"

        result = self.analyzer._parse_response(
            json.dumps(payload, ensure_ascii=False)
        )

        self.assertFalse(result.success)
        self.assertIn("重复", result.error)

    def test_strategy_label_merged_into_other_line_is_rejected(self):
        payload = self.valid_payload()
        payload["outlook_strategy"] = payload[
            "outlook_strategy"
        ].replace(
            "天时：未知/无法确定",
            "天时：未知/无法确定 地利：未知/无法确定",
        ).replace(
            "\n地利：未知/无法确定",
            "",
        )

        result = self.analyzer._parse_response(
            json.dumps(payload, ensure_ascii=False)
        )

        self.assertFalse(result.success)
        self.assertIn("地利", result.error)

    def test_strategy_empty_label_content_should_be_rejected(self):
        payload = self.valid_payload()
        payload["outlook_strategy"] = payload[
            "outlook_strategy"
        ].replace("天时：未知/无法确定", "天时：")

        result = self.analyzer._parse_response(
            json.dumps(payload, ensure_ascii=False)
        )

        # 当前生产代码可能无法通过此测试。
        # 这是本轮审查发现的待修复问题。
        self.assertFalse(result.success)
        self.assertIn("天时", result.error)

    def test_standalone_summaries_must_be_dict(self):
        payload = self.valid_payload()
        payload["standalone_summaries"] = []

        result = self.analyzer._parse_response(
            json.dumps(payload, ensure_ascii=False)
        )

        self.assertFalse(result.success)
        self.assertIn("standalone_summaries", result.error)

    def test_standalone_summary_values_must_be_strings(self):
        payload = self.valid_payload()
        payload["standalone_summaries"] = {"知乎": 123}

        result = self.analyzer._parse_response(
            json.dumps(payload, ensure_ascii=False)
        )

        self.assertFalse(result.success)
        self.assertIn("非字符串", result.error)

    def test_standalone_empty_summary_is_currently_accepted(self):
        payload = self.valid_payload()
        payload["standalone_summaries"] = {"知乎": "   "}

        result = self.analyzer._parse_response(
            json.dumps(payload, ensure_ascii=False)
        )

        # 记录当前行为，后续再决定是否收紧校验。
        self.assertTrue(result.success)
        self.assertEqual({"知乎": ""}, result.standalone_summaries)

    def test_prepare_news_content_respects_positive_limit(self):
        self.analyzer.max_news = 2
        self.analyzer.include_rss = True
        self.analyzer.include_rank_timeline = False

        stats = [{
            "word": "热点",
            "titles": [
                {"title": "新闻一"},
                {"title": "新闻二"},
                {"title": "新闻三"},
            ],
        }]
        rss_stats = [{
            "word": "RSS",
            "titles": [
                {"title": "RSS 新闻一"},
                {"title": "RSS 新闻二"},
            ],
        }]

        news, rss, hot_total, rss_total, analyzed = (
            self.analyzer._prepare_news_content(stats, rss_stats)
        )

        self.assertEqual(3, hot_total)
        self.assertEqual(2, rss_total)
        self.assertEqual(2, analyzed)
        self.assertIn("新闻一", news)
        self.assertIn("新闻二", news)
        self.assertNotIn("新闻三", news)
        self.assertEqual("", rss)

    def test_prepare_news_content_can_include_rss_after_hotlist(self):
        self.analyzer.max_news = 3
        self.analyzer.include_rss = True
        self.analyzer.include_rank_timeline = False

        stats = [{
            "word": "热点",
            "titles": [{"title": "热榜新闻"}],
        }]
        rss_stats = [{
            "word": "RSS",
            "titles": [
                {"title": "RSS 新闻一"},
                {"title": "RSS 新闻二"},
            ],
        }]

        news, rss, _, _, analyzed = (
            self.analyzer._prepare_news_content(stats, rss_stats)
        )

        self.assertEqual(3, analyzed)
        self.assertIn("热榜新闻", news)
        self.assertIn("RSS 新闻一", rss)
        self.assertIn("RSS 新闻二", rss)

    def test_prepare_news_content_respects_include_rss_false(self):
        self.analyzer.max_news = 10
        self.analyzer.include_rss = False
        self.analyzer.include_rank_timeline = False

        rss_stats = [{
            "word": "RSS",
            "titles": [{"title": "RSS 新闻"}],
        }]

        _, rss, _, rss_total, analyzed = (
            self.analyzer._prepare_news_content([], rss_stats)
        )

        self.assertEqual(1, rss_total)
        self.assertEqual("", rss)
        self.assertEqual(0, analyzed)

    def test_call_ai_retries_empty_response_then_succeeds(self):
        self.analyzer.system_prompt = "system"
        self.analyzer.client = Mock()
        self.analyzer.client.chat.side_effect = ["", "", "valid response"]

        with patch("time.sleep") as mock_sleep:
            response = self.analyzer._call_ai("user prompt")

        self.assertEqual("valid response", response)
        self.assertEqual(3, self.analyzer.client.chat.call_count)
        self.assertEqual(2, mock_sleep.call_count)

    def test_call_ai_stops_after_three_empty_responses(self):
        self.analyzer.system_prompt = ""
        self.analyzer.client = Mock()
        self.analyzer.client.chat.return_value = ""

        with patch("time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "空响应"):
                self.analyzer._call_ai("user prompt")

        self.assertEqual(3, self.analyzer.client.chat.call_count)

    def test_json_repair_guard_denial_is_not_retried(self):
        self.analyzer.client = Mock()
        self.analyzer.client.chat.side_effect = AIRequestNotAllowedError(
            "AI request is not allowed"
        )

        with self.assertRaisesRegex(
            AIRequestNotAllowedError,
            "AI request is not allowed",
        ):
            self.analyzer._retry_fix_json(
                "invalid response",
                "invalid JSON",
            )

        self.analyzer.client.chat.assert_called_once()

if __name__ == "__main__":
    unittest.main()
