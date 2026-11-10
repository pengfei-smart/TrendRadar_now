# 本文件补充测试行情抓取器对空候选、零价格及异常代码响应的保护行为。
"""Edge-case tests for quote fetcher."""

import unittest
from unittest.mock import Mock

from trendradar.market_data.models import Security
from trendradar.market_data.quote_fetcher import (
    EastmoneyBatchQuoteProvider,
    QuoteSourceError,
    QuoteFetcher,
)


class QuoteFetcherEdgeTests(unittest.TestCase):
    def test_empty_candidates_do_not_make_http_request(self):
        session = Mock()
        provider = EastmoneyBatchQuoteProvider(session=session)
        self.assertEqual(provider.fetch_quotes([]), [])
        session.get.assert_not_called()

    def test_zero_price_is_omitted_and_no_quote_raises(self):
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": {"diff": [
            {"f12": "600000", "f2": "0", "f3": "0", "f6": "0", "f10": "-"}
        ]}}
        session.get.return_value = response
        provider = EastmoneyBatchQuoteProvider(session=session)
        with self.assertRaises(QuoteSourceError):
            provider.fetch_quotes([Security("600000", "浦发银行", "SH")])

    def test_quote_fetcher_rejects_non_candidate_quote(self):
        class Unexpected:
            source_name = "unexpected"

            def fetch_quotes(self, securities):
                from trendradar.market_data.models import QuoteSnapshot
                return [QuoteSnapshot("000001", "2026-01-01T14:33:00+08:00",
                                      price=10, source="unexpected")]

        with self.assertRaises(QuoteSourceError):
            QuoteFetcher([Unexpected()]).fetch([Security("600000", "浦发银行", "SH")])


if __name__ == "__main__":
    unittest.main()
