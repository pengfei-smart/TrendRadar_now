# 本文件提供免费优先且可插拔的批量A股行情获取接口，属于逐文件开发清单第八项。
"""Provider-neutral quote retrieval interface.

A concrete provider must be selected and tested before production wiring. This
module deliberately does not silently assume that an unofficial endpoint has
stable availability or zero delay.
"""

from typing import Protocol, Sequence

from trendradar.market_data.models import QuoteSnapshot


class QuoteProvider(Protocol):
    """Batch quote provider contract."""

    provider_name: str

    def fetch_quotes(self, codes: Sequence[str]) -> list[QuoteSnapshot]:
        """Fetch quotes for a batch of codes; implementations must declare delay."""
        ...


class QuoteFetcher:
    """Small adapter that normalizes provider output and degrades gracefully."""

    def __init__(self, provider: QuoteProvider):
        self.provider = provider

    def fetch(self, codes: Sequence[str]) -> list[QuoteSnapshot]:
        unique_codes = list(dict.fromkeys(code.strip() for code in codes if code.strip()))
        if not unique_codes:
            return []
        try:
            quotes = self.provider.fetch_quotes(unique_codes)
            return [
                quote for quote in quotes
                if quote.code in set(unique_codes) and quote.timestamp
            ]
        except Exception as exc:
            print(f"[行情雷达] 行情源 {self.provider.provider_name} 获取失败，降级为空行情：{exc}")
            return []
