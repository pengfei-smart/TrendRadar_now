# 本文件测试候选股票批量行情响应解析、请求分批、数值转换和主备行情源降级。
"""Tests for batch quote providers and failover."""

import unittest
from unittest.mock import Mock

from trendradar.market_data.models import QuoteSnapshot, Security
from trendradar.market_data.quote_fetcher import (
    EastmoneyBatchQuoteProvider,
    QuoteFetcher,
    QuoteSourceError,
    eastmoney_secid,
)


class QuoteFetcherTests(unittest.TestCase):
    def test_secid_mapping(self):
        self.assertEqual(eastmoney_secid(Security("600000", "浦发银行", "SH")), "1.600000")
        self.assertEqual(eastmoney_secid(Security("000001", "平安银行", "SZ")), "0.000001")
        self.assertEqual(eastmoney_secid(Security("830799", "北交所证券", "BJ")), "0.830799")

    def test_fetches_batches_and_filters_candidates(self):
        session = Mock()
        first = Mock()
        first.raise_for_status.return_value = None
        first.json.return_value = {"data": {"diff": [
            {"f12": "600000", "f2": 10.25, "f3": 1.5, "f6": 123456.0, "f10": 1.2},
            {"f12": "000001", "f2": 12.5, "f3": -0.5, "f6": 654321.0, "f10": 0.9},
        ]}}
        session.get.return_value = first
        provider = EastmoneyBatchQuoteProvider(session=session, batch_size=2)
        quotes = provider.fetch_quotes([
            Security("600000", "浦发银行", "SH"),
            Security("000001", "平安银行", "SZ"),
        ])
        self.assertEqual(len(quotes), 2)
        by_code = {quote.code: quote for quote in quotes}
        self.assertEqual(by_code["600000"].price, 10.25)
        self.assertEqual(by_code["600000"].change_pct, 1.5)
        self.assertEqual(by_code["600000"].turnover, 123456.0)
        self.assertEqual(by_code["600000"].volume_ratio, 1.2)
        self.assertTrue(by_code["600000"].delayed)
        self.assertEqual(session.get.call_count, 1)

    def test_chunks_candidates_by_configured_batch_size(self):
        session = Mock()
        responses = []
        for code in ("600000", "000001", "300001"):
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"data": {"diff": [
                {"f12": code, "f2": 10, "f3": 1, "f6": 100, "f10": 1}
            ]}}
            responses.append(response)
        session.get.side_effect = responses
        provider = EastmoneyBatchQuoteProvider(session=session, batch_size=1)
        quotes = provider.fetch_quotes([
            Security("600000", "浦发银行", "SH"),
            Security("000001", "平安银行", "SZ"),
            Security("300001", "创业板", "SZ"),
        ])
        self.assertEqual(len(quotes), 3)
        self.assertEqual(session.get.call_count, 3)

    def test_parses_object_diff(self):
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": {"diff": {
            "0": {"f12": "600000", "f2": "10.25", "f3": "1.5", "f6": "123", "f10": "1.1"}
        }}}
        session.get.return_value = response
        provider = EastmoneyBatchQuoteProvider(session=session, batch_size=10)
        quotes = provider.fetch_quotes([Security("600000", "浦发银行", "SH")])
        self.assertEqual(quotes[0].price, 10.25)

    def test_quote_fetcher_falls_back_to_secondary(self):
        class Broken:
            source_name = "primary"

            def fetch_quotes(self, securities):
                raise QuoteSourceError("blocked")

        class Healthy:
            source_name = "secondary"

            def fetch_quotes(self, securities):
                return [QuoteSnapshot("600000", "2026-01-01T14:33:00+08:00",
                                      price=10, change_pct=1, source="secondary")]

        quotes, source = QuoteFetcher([Broken(), Healthy()]).fetch(
            [Security("600000", "浦发银行", "SH")]
        )
        self.assertEqual(source, "secondary")
        self.assertEqual(len(quotes), 1)

    def test_quote_fetcher_fails_if_all_sources_unavailable(self):
        class Broken:
            source_name = "provider"

            def fetch_quotes(self, securities):
                raise QuoteSourceError("offline")

        with self.assertRaises(QuoteSourceError):
            QuoteFetcher([Broken()]).fetch([Security("600000", "浦发银行", "SH")])


if __name__ == "__main__":
    unittest.main()
