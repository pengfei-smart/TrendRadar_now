# 本文件采集美国宏观政策/经济信息RSS并统一事件格式，属于逐文件开发清单第八项。
"""US macroeconomic and policy event ingestion."""

from typing import Iterable

from trendradar.market_data.models import MarketEvent
from trendradar.market_data.us_event_fetcher import USEventFetcher


DEFAULT_MACRO_FEEDS = (
    ("fed_monetary", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    ("bea_releases", "https://apps.bea.gov/rss/rss.xml"),
    ("eia_energy", "https://www.eia.gov/rss/todayinenergy.xml"),
)


class MacroEventFetcher:
    """Reuses the same robust RSS transport for official macro feeds."""

    def __init__(self, feeds: Iterable[tuple[str, str]] = DEFAULT_MACRO_FEEDS, timeout: int = 12):
        self._fetcher = USEventFetcher(feeds=feeds, timeout=timeout)

    def fetch(self, limit_per_feed: int = 30) -> list[MarketEvent]:
        return [
            MarketEvent(
                event_id=event.event_id,
                source=event.source,
                title=event.title,
                published_at=event.published_at,
                event_type="macro_policy",
                direction="neutral",
                region="US",
                url=event.url,
                summary=event.summary,
            )
            for event in self._fetcher.fetch(limit_per_feed=limit_per_feed)
        ]
