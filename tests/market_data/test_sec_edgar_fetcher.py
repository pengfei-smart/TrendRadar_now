# 本文件测试SEC EDGAR申报采集的CIK格式、User-Agent约束、事件标准化和恶意文档路径过滤。
"""Unit tests for SEC EDGAR submissions event ingestion."""

import unittest
from unittest.mock import Mock

from trendradar.market_data.sec_edgar_fetcher import (
    SECEdgarFetcher,
    SECEdgarError,
    SEC_SUBMISSIONS_URL,
)


class SECEdgarFetcherTests(unittest.TestCase):
    def test_cik_is_normalized_and_request_is_scoped_to_company(self):
        self.assertEqual(SECEdgarFetcher._normalize_cik("320193"), "0000320193")
        self.assertEqual(SECEdgarFetcher._normalize_cik("0000320193"), "0000320193")
        fetcher = SECEdgarFetcher(
            companies={"AAPL": "320193"},
            user_agent="TrendRadar test contact test@example.org",
            session=Mock(),
        )
        self.assertEqual(fetcher.companies, {"AAPL": "0000320193"})

    def test_missing_contact_email_fails_before_network(self):
        session = Mock()
        fetcher = SECEdgarFetcher(
            companies={"AAPL": "320193"},
            user_agent="TrendRadar application",
            session=session,
        )
        with self.assertRaises(SECEdgarError):
            fetcher.fetch()
        session.get.assert_not_called()

    def test_valid_8k_is_normalized_to_market_event(self):
        payload = {
            "filings": {"recent": {
                "form": ["8-K", "4"],
                "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002"],
                "filingDate": ["2026-11-09", "2026-11-09"],
                "primaryDocument": ["aapl-20261109.htm", "form4.xml"],
                "primaryDocDescription": ["Current report", "Statement of changes"],
            }}
        }
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = payload
        session.get.return_value = response
        fetcher = SECEdgarFetcher(
            companies={"AAPL": "320193"},
            user_agent="TrendRadar test contact test@example.org",
            session=session,
            min_interval=0.1,
        )

        events = fetcher.fetch()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_id, "sec_edgar:AAPL:0000320193-26-000001")
        self.assertEqual(event.source, "sec_edgar")
        self.assertEqual(event.event_type, "sec_filing")
        self.assertEqual(event.region, "US")
        self.assertIn("/000032019326000001/aapl-20261109.htm", event.url)
        self.assertEqual(session.get.call_args.args[0], SEC_SUBMISSIONS_URL.format(cik="0000320193"))

    def test_malicious_primary_document_path_is_skipped(self):
        payload = {"filings": {"recent": {
            "form": ["8-K"],
            "accessionNumber": ["0000320193-26-000001"],
            "filingDate": ["2026-11-09"],
            "primaryDocument": ["../secrets.txt"],
        }}}
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = payload
        session.get.return_value = response
        fetcher = SECEdgarFetcher(
            companies={"AAPL": "320193"},
            user_agent="TrendRadar test contact test@example.org",
            session=session,
        )
        self.assertEqual(fetcher.fetch(), [])


if __name__ == "__main__":
    unittest.main()
