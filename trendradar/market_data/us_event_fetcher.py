# 本文件采集美股公司与行业事件RSS并标准化为市场事件，属于逐文件开发清单第八项。
"""US market/company event ingestion from configurable RSS feeds."""

from datetime import datetime, timezone
from typing import Iterable

import feedparser
import requests

from trendradar.market_data.models import MarketEvent


DEFAULT_FEEDS = (
    ("nasdaq_news", "https://www.nasdaq.com/feed/rssoutbound?category=Stocks"),
    ("federal_reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
)


class USEventFetcher:
    """Fetch events from public RSS feeds with bounded requests."""

    def __init__(self, feeds: Iterable[tuple[str, str]] = DEFAULT_FEEDS, timeout: int = 12):
        self.feeds = tuple(feeds)
        self.timeout = max(3, timeout)

    def fetch(self, limit_per_feed: int = 40) -> list[MarketEvent]:
        events: list[MarketEvent] = []
        for source, url in self.feeds:
            try:
                response = requests.get(
                    url,
                    timeout=self.timeout,
                    headers={"User-Agent": "TrendRadar market event reader/1.0"},
                )
                response.raise_for_status()
                parsed = feedparser.parse(response.content)
                for entry in parsed.entries[:max(1, limit_per_feed)]:
                    title = str(entry.get("title", "")).strip()
                    if not title:
                        continue
                    published = entry.get("published") or entry.get("updated") or ""
                    if not published:
                        published = datetime.now(timezone.utc).isoformat()
                    link = str(entry.get("link", ""))
                    event_id = f"{source}:{link or title}"
                    events.append(MarketEvent(
                        event_id=event_id,
                        source=source,
                        title=title,
                        published_at=str(published),
                        region="US",
                        url=link,
                        summary=str(entry.get("summary", ""))[:4000],
                    ))
            except Exception as exc:
                print(f"[市场事件] 美股事件源 {source} 获取失败：{exc}")
        return events
