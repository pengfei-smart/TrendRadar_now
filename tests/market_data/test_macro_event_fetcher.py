# 本文件验证美国宏观官方RSS源配置及事件元数据标准化，避免源分类或地区字段错误。
"""Tests for official US macro event source configuration."""

import unittest
from unittest.mock import patch

from trendradar.market_data.macro_event_fetcher import DEFAULT_MACRO_FEEDS, MacroEventFetcher
from trendradar.market_data.models import MarketEvent


class MacroEventFetcherTests(unittest.TestCase):
    def test_default_sources_cover_fed_bls_and_energy(self):
        sources = {source for source, _ in DEFAULT_MACRO_FEEDS}
        self.assertIn("fed_monetary", sources)
        self.assertIn("bls_cpi", sources)
        self.assertIn("bls_employment", sources)
        self.assertIn("eia_energy", sources)

    def test_macro_events_are_normalized(self):
        source_event = MarketEvent(
            event_id="bls_cpi:https://example.test/cpi",
            source="bls_cpi",
            title="Consumer Price Index",
            published_at="2026-11-09T08:30:00-05:00",
            region="US",
            url="https://example.test/cpi",
            summary="CPI release",
        )
        with patch("trendradar.market_data.macro_event_fetcher.USEventFetcher") as fetcher:
            fetcher.return_value.fetch.return_value = [source_event]
            events = MacroEventFetcher().fetch(limit_per_feed=7)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_id, source_event.event_id)
        self.assertEqual(events[0].event_type, "macro_policy")
        self.assertEqual(events[0].direction, "neutral")
        self.assertEqual(events[0].region, "US")
        self.assertEqual(events[0].source, "bls_cpi")
        fetcher.return_value.fetch.assert_called_once_with(limit_per_feed=7)


if __name__ == "__main__":
    unittest.main()
