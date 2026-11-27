import unittest
from unittest.mock import MagicMock, patch

from trendradar.context import AppContext
from trendradar.ai.request_guard import AIRequestNotAllowedError


class AIFilterGuardTests(unittest.TestCase):
    def setUp(self):
        self.storage = MagicMock()
        self.storage.get_latest_prompt_hash.return_value = "hash"
        self.storage.get_latest_ai_filter_tag_version.return_value = 1
        self.storage.get_active_ai_filter_tags.return_value = [
            {"id": 1, "tag": "科技", "priority": 1}
        ]
        self.storage.get_all_news_ids.return_value = []
        self.storage.get_all_rss_ids.return_value = []
        self.storage.get_analyzed_news_ids.return_value = []

        self.ctx = AppContext({
            "FILTER": {"METHOD": "ai"},
            "AI": {"MODEL": "test-model"},
            "AI_FILTER": {
                "BATCH_SIZE": 10,
                "BATCH_INTERVAL": 0,
                "RECLASSIFY_THRESHOLD": 0.6,
            },
            "RSS": {"ENABLED": False},
        })
        self.ctx._storage_manager = self.storage
        self.ctx.get_ai_request_guard = MagicMock(return_value=MagicMock())

    def _run(self, configure):
        with patch("trendradar.context.AIFilter") as filter_class:
            ai_filter = filter_class.return_value
            ai_filter.load_interests_content.return_value = "测试兴趣"
            ai_filter.compute_interests_hash.return_value = "hash"

            error = AIRequestNotAllowedError("AI 请求不在允许时间窗口")
            configure(ai_filter, error)

            result = self.ctx.run_ai_filter()

        self.storage.begin_batch.assert_called_once()
        self.storage.end_batch.assert_called_once()
        self.assertFalse(result.success)
        self.assertIn("AI 请求不在允许时间窗口", result.error)
        return ai_filter, result

    def test_first_run_extract_guard_denied(self):
        self.storage.get_latest_prompt_hash.return_value = None

        def configure(ai_filter, error):
            ai_filter.extract_tags.side_effect = error

        ai_filter, _ = self._run(configure)

        ai_filter.extract_tags.assert_called_once()
        ai_filter.update_tags.assert_not_called()
        ai_filter.classify_batch.assert_not_called()

    def test_first_run_cleans_old_tags_and_analyzed_news_before_saving(self):
        self.storage.get_latest_prompt_hash.return_value = None

        extracted_tags = [
            {"tag": " 科技 ", "description": "科技行业"},
            {"tag": "新能源", "description": "新能源产业"},
        ]
        expected_tags = [
            {"tag": "科技", "description": "科技行业", "priority": 1},
            {"tag": "新能源", "description": "新能源产业", "priority": 2},
        ]
        events = []

        self.storage.deprecate_all_ai_filter_tags.side_effect = (
            lambda **kwargs: events.append("deprecate") or 3
        )
        self.storage.clear_analyzed_news.side_effect = (
            lambda **kwargs: events.append("clear")
        )
        self.storage.save_ai_filter_tags.side_effect = (
            lambda *args, **kwargs: events.append("save") or len(args[0])
        )

        with patch("trendradar.context.AIFilter") as filter_class:
            ai_filter = filter_class.return_value
            ai_filter.load_interests_content.return_value = "测试兴趣"
            ai_filter.compute_interests_hash.return_value = "hash"
            ai_filter.extract_tags.side_effect = (
                lambda content: events.append("extract") or extracted_tags
            )

            result = self.ctx.run_ai_filter()

        self.assertTrue(result.success, result.error)
        self.assertEqual(events, ["extract", "deprecate", "clear", "save"])

        self.storage.deprecate_all_ai_filter_tags.assert_called_once_with(
            interests_file="ai_interests.txt"
        )
        self.storage.clear_analyzed_news.assert_called_once_with(
            interests_file="ai_interests.txt"
        )
        self.storage.save_ai_filter_tags.assert_called_once_with(
            expected_tags,
            2,
            "hash",
            interests_file="ai_interests.txt",
        )
        self.storage.begin_batch.assert_called_once()
        self.storage.end_batch.assert_called_once()
        ai_filter.classify_batch.assert_not_called()

    def test_update_tags_guard_denied(self):
        self.storage.get_latest_prompt_hash.return_value = "old-hash"

        def configure(ai_filter, error):
            ai_filter.update_tags.side_effect = error

        ai_filter, _ = self._run(configure)

        ai_filter.update_tags.assert_called_once()
        ai_filter.extract_tags.assert_not_called()
        ai_filter.classify_batch.assert_not_called()

    def test_fallback_extract_guard_denied(self):
        self.storage.get_latest_prompt_hash.return_value = "old-hash"

        def configure(ai_filter, error):
            ai_filter.update_tags.return_value = None
            ai_filter.extract_tags.side_effect = error

        ai_filter, _ = self._run(configure)

        ai_filter.update_tags.assert_called_once()
        ai_filter.extract_tags.assert_called_once()
        ai_filter.classify_batch.assert_not_called()

    def test_full_reclassify_extract_guard_denied(self):
        self.storage.get_latest_prompt_hash.return_value = "old-hash"

        def configure(ai_filter, error):
            ai_filter.update_tags.return_value = {
                "change_ratio": 0.9,
                "keep": [],
                "add": [],
                "remove": [],
            }
            ai_filter.extract_tags.side_effect = error

        ai_filter, _ = self._run(configure)

        ai_filter.update_tags.assert_called_once()
        ai_filter.extract_tags.assert_called_once()
        ai_filter.classify_batch.assert_not_called()

    def test_hotlist_classify_guard_denied(self):
        self.storage.get_all_news_ids.return_value = [
            {"id": 101, "title": "测试新闻", "source_name": "测试来源"}
        ]

        def configure(ai_filter, error):
            ai_filter.classify_batch.side_effect = error

        ai_filter, _ = self._run(configure)

        ai_filter.classify_batch.assert_called_once()
        self.storage.save_ai_filter_results.assert_not_called()
        self.storage.save_analyzed_news.assert_not_called()

    def test_rss_classify_guard_denied(self):
        self.ctx.config["RSS"] = {
            "ENABLED": True,
            "FRESHNESS_FILTER": {"ENABLED": False},
            "FEEDS": [],
        }
        self.storage.get_all_rss_ids.return_value = [
            {
                "id": 201,
                "title": "测试 RSS 新闻",
                "source_name": "测试 RSS",
                "published_at": "",
                "source_id": "feed-1",
            }
        ]

        def configure(ai_filter, error):
            ai_filter.classify_batch.side_effect = error

        ai_filter, _ = self._run(configure)

        ai_filter.classify_batch.assert_called_once()
        self.storage.save_ai_filter_results.assert_not_called()
        self.storage.save_analyzed_news.assert_not_called()

    def test_guard_denial_cleanup_failure_is_reported(self):
        self.storage.get_latest_prompt_hash.return_value = None
        self.storage.end_batch.side_effect = OSError("模拟远程上传失败")

        with patch("trendradar.context.AIFilter") as filter_class:
            ai_filter = filter_class.return_value
            ai_filter.load_interests_content.return_value = "测试兴趣"
            ai_filter.compute_interests_hash.return_value = "hash"
            ai_filter.extract_tags.side_effect = AIRequestNotAllowedError(
                "AI 请求不在允许时间窗口"
            )

            result = self.ctx.run_ai_filter()

        self.storage.end_batch.assert_called_once()
        self.assertFalse(result.success)
        self.assertIn("AI 请求不在允许时间窗口", result.error)
        self.assertIn("模拟远程上传失败", result.error)


    def test_batch_cleanup_when_prompt_hash_read_raises(self):
        self.storage.get_latest_prompt_hash.side_effect = RuntimeError(
            "模拟读取提示词 hash 失败"
        )

        with patch("trendradar.context.AIFilter") as filter_class:
            ai_filter = filter_class.return_value
            ai_filter.load_interests_content.return_value = "测试兴趣"
            ai_filter.compute_interests_hash.return_value = "hash"

            with self.assertRaisesRegex(
                RuntimeError, "模拟读取提示词 hash 失败"
            ):
                self.ctx.run_ai_filter()

        self.storage.begin_batch.assert_called_once()
        self.storage.end_batch.assert_called_once()

    def test_batch_cleanup_when_news_query_raises(self):
        self.storage.get_all_news_ids.side_effect = RuntimeError(
            "模拟新闻查询失败"
        )

        with patch("trendradar.context.AIFilter") as filter_class:
            ai_filter = filter_class.return_value
            ai_filter.load_interests_content.return_value = "测试兴趣"
            ai_filter.compute_interests_hash.return_value = "hash"

            with self.assertRaisesRegex(
                RuntimeError, "模拟新闻查询失败"
            ):
                self.ctx.run_ai_filter()

        self.storage.begin_batch.assert_called_once()
        self.storage.end_batch.assert_called_once()

    def test_batch_end_failure_does_not_retry(self):
        self.storage.get_active_ai_filter_tags.return_value = []
        self.storage.end_batch.side_effect = OSError(
            "模拟批处理结束失败"
        )

        with patch("trendradar.context.AIFilter") as filter_class:
            ai_filter = filter_class.return_value
            ai_filter.load_interests_content.return_value = "测试兴趣"
            ai_filter.compute_interests_hash.return_value = "hash"

            with self.assertRaisesRegex(
                OSError, "模拟批处理结束失败"
            ):
                self.ctx.run_ai_filter()

        self.storage.begin_batch.assert_called_once()
        self.storage.end_batch.assert_called_once()



    def test_business_and_batch_cleanup_failures_are_both_preserved(self):
        self.storage.get_latest_prompt_hash.side_effect = RuntimeError(
            "模拟新闻查询失败"
        )
        self.storage.end_batch.side_effect = OSError(
            "模拟远程上传失败"
        )

        with patch("trendradar.context.AIFilter") as filter_class:
            ai_filter = filter_class.return_value
            ai_filter.load_interests_content.return_value = "测试兴趣"
            ai_filter.compute_interests_hash.return_value = "hash"

            with self.assertRaisesRegex(
                RuntimeError, "模拟新闻查询失败"
            ) as captured:
                self.ctx.run_ai_filter()

        self.storage.begin_batch.assert_called_once()
        self.storage.end_batch.assert_called_once()

        self.assertIsInstance(captured.exception.__cause__, OSError)
        self.assertIn(
            "模拟远程上传失败",
            str(captured.exception.__cause__),
        )


if __name__ == "__main__":
    unittest.main()
