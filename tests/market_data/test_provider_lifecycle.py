import unittest
from unittest.mock import Mock, patch

from trendradar.market_data.quote_fetcher import EastmoneyBatchQuoteProvider
from trendradar.market_data.security_master_provider import (
    EastmoneySecurityMasterProvider,
)
from trendradar.market_data.listing_status_provider import (
    SSEMainBoardAProvider,
    SZSEBoardAProvider,
)


PROVIDERS = [
    (
        EastmoneyBatchQuoteProvider,
        "trendradar.market_data.quote_fetcher",
    ),
    (
        EastmoneySecurityMasterProvider,
        "trendradar.market_data.security_master_provider",
    ),
    (
        SSEMainBoardAProvider,
        "trendradar.market_data.listing_status_provider",
    ),
    (
        SZSEBoardAProvider,
        "trendradar.market_data.listing_status_provider",
    ),
]


class ProviderLifecycleTests(unittest.TestCase):

    def test_owned_sessions_are_closed_once(self):
        for provider_class, module_name in PROVIDERS:
            with self.subTest(provider=provider_class.__name__):
                with patch(f"{module_name}.requests.Session") as factory:
                    session = Mock()
                    factory.return_value = session

                    provider = provider_class()

                    self.assertIs(provider.session, session)
                    self.assertTrue(provider._owns_session)

                    provider.close()
                    provider.close()

                    session.close.assert_called_once_with()

    def test_injected_sessions_are_not_closed(self):
        for provider_class, _ in PROVIDERS:
            with self.subTest(provider=provider_class.__name__):
                session = Mock()
                provider = provider_class(session=session)

                self.assertIs(provider.session, session)
                self.assertFalse(provider._owns_session)

                provider.close()
                provider.close()

                session.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
