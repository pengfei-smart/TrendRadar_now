# 本文件采集美国宏观政策与经济指标官方RSS源，并将其标准化为可追踪的MarketEvent。
"""US macroeconomic and policy event ingestion from official feeds."""

from typing import Iterable

from trendradar.market_data.models import MarketEvent
from trendradar.market_data.us_event_fetcher import USEventFetcher


# Official feeds documented by the Federal Reserve, BLS and EIA.
# BEA's RSS is intentionally not configured until its canonical feed URL is verified.
DEFAULT_MACRO_FEEDS = (
    ("fed_monetary", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    ("bls_latest", "https://www.bls.gov/feed/bls_latest.rss"),
    ("bls_cpi", "https://www.bls.gov/feed/cpi.rss"),
    ("bls_employment", "https://www.bls.gov/feed/empsit.rss"),
    ("eia_energy", "https://www.eia.gov/rss/todayinenergy.xml"),
)


class MacroEventFetcher:
    """Fetch official macro/policy RSS feeds and normalize their event metadata."""

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
