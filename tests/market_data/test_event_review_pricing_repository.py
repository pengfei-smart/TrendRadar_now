"""Tests for immutable, source-traceable event-review model pricing."""
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest

from trendradar.market_data.event_review_pricing_repository import (
    EventReviewPricingRepository,
)
from trendradar.market_data.storage import MarketStorage
from trendradar.market_data.models import MarketEvent


class EventReviewPricingRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = MarketStorage(str(Path(self.tmp.name) / "market.db"))
        self.repository = EventReviewPricingRepository(self.storage)

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, **overrides):
        values = {
            "provider": "provider-a",
            "model": "model-x",
            "currency": "USD",
            "input_price_per_million": "2.500000",
            "output_price_per_million": "10",
            "valid_from": "2026-11-01T00:00:00Z",
            "valid_until": "2026-12-01T00:00:00Z",
            "source_url": "https://provider.example/pricing",
            "source_checked_at": "2026-11-19T10:00:00+08:00",
            "pricing_id": "pricing-1",
        }
        values.update(overrides)
        return self.repository.add_price(**values)

    def test_add_and_read_price_preserves_decimal_and_provenance(self):
        record = self.add()
        self.assertEqual(record.pricing_id, "pricing-1")
        self.assertEqual(record.input_price_per_million, Decimal("2.5"))
        self.assertEqual(record.output_price_per_million, Decimal("10"))
        self.assertEqual(record.currency, "USD")
        self.assertEqual(record.source_url, "https://provider.example/pricing")
        self.assertEqual(self.repository.list_prices(), (record,))
        self.assertEqual(
            self.repository.find_price(
                provider="provider-a", model="model-x", at="2026-11-30T23:59:59Z",
            ),
            record,
        )

    def test_effective_window_is_start_inclusive_and_end_exclusive(self):
        self.add()
        self.assertIsNone(self.repository.find_price(
            provider="provider-a", model="model-x", at="2026-10-31T23:59:59Z",
        ))
        self.assertIsNone(self.repository.find_price(
            provider="provider-a", model="model-x", at="2026-12-01T00:00:00Z",
        ))

    def test_adjacent_price_versions_are_allowed_but_overlaps_are_rejected(self):
        self.add(valid_until="2026-12-01T00:00:00Z")
        next_record = self.add(
            pricing_id="pricing-2",
            input_price_per_million="3",
            valid_from="2026-12-01T00:00:00Z",
            valid_until=None,
        )
        self.assertEqual(len(self.repository.list_prices()), 2)
        self.assertEqual(
            self.repository.find_price(
                provider="provider-a", model="model-x", at="2026-12-01T00:00:00Z",
            ),
            next_record,
        )
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.add(
                pricing_id="pricing-3",
                valid_from="2026-11-30T00:00:00Z",
                valid_until="2027-01-01T00:00:00Z",
            )

    def test_provider_and_model_must_match_exactly(self):
        self.add()
        self.assertIsNone(self.repository.find_price(
            provider="provider-b", model="model-x", at="2026-11-19T00:00:00Z",
        ))
        self.assertIsNone(self.repository.find_price(
            provider="provider-a", model="model-y", at="2026-11-19T00:00:00Z",
        ))

    def test_rejects_invalid_price_currency_and_timestamps(self):
        with self.assertRaisesRegex(ValueError, "non-negative"):
            self.add(input_price_per_million="-1")
        with self.assertRaisesRegex(TypeError, "float"):
            self.add(output_price_per_million=1.25)
        with self.assertRaisesRegex(ValueError, "currency"):
            self.add(currency="usd")
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.add(valid_from="2026-11-01T00:00:00")
        with self.assertRaisesRegex(ValueError, "later"):
            self.add(valid_until="2026-10-01T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "HTTP"):
            self.add(source_url="provider-pricing-page")

    def test_v13_database_migrates_without_losing_existing_events(self):
        self.storage.save_events([
            MarketEvent("existing-event", "test", "Existing event", "2026-11-19"),
        ])
        with self.storage.connect() as connection:
            connection.execute("DROP TABLE event_review_model_pricing")
            connection.execute(
                "UPDATE schema_meta SET value='13' WHERE key='schema_version'"
            )

        migrated = MarketStorage(str(Path(self.tmp.name) / "market.db"))
        with migrated.connect() as connection:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            event_count = connection.execute(
                "SELECT COUNT(*) FROM market_events WHERE event_id='existing-event'"
            ).fetchone()[0]
            pricing_table = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name='event_review_model_pricing'"
            ).fetchone()[0]
        self.assertEqual(version, "14")
        self.assertEqual(event_count, 1)
        self.assertEqual(pricing_table, 1)

    def test_pricing_history_is_append_only(self):
        self.add()
        with self.storage.connect() as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    "UPDATE event_review_model_pricing SET output_price_per_million='0' "
                    "WHERE pricing_id='pricing-1'"
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    "DELETE FROM event_review_model_pricing WHERE pricing_id='pricing-1'"
                )
        self.assertEqual(len(self.repository.list_prices()), 1)


if __name__ == "__main__":
    unittest.main()
