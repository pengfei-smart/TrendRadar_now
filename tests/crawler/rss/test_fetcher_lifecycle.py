
import unittest
from unittest.mock import Mock, patch

from trendradar.crawler.rss.fetcher import RSSFetcher


class RSSFetcherLifecycleTests(unittest.TestCase):
    @patch("trendradar.crawler.rss.fetcher.requests.Session")
    def test_session_starts_open(self, session_class):
        session = Mock()
        session_class.return_value = session

        fetcher = RSSFetcher(feeds=[])

        self.assertFalse(fetcher._closed)
        session.close.assert_not_called()

    @patch("trendradar.crawler.rss.fetcher.requests.Session")
    def test_close_closes_session_once(self, session_class):
        session = Mock()
        session_class.return_value = session

        fetcher = RSSFetcher(feeds=[])

        fetcher.close()

        self.assertTrue(fetcher._closed)
        session.close.assert_called_once()

    @patch("trendradar.crawler.rss.fetcher.requests.Session")
    def test_repeated_close_is_idempotent(self, session_class):
        session = Mock()
        session_class.return_value = session

        fetcher = RSSFetcher(feeds=[])

        fetcher.close()
        fetcher.close()

        session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
