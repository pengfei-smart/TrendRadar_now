import unittest

from trendradar.market_data.event_source_registry import assess_event_source


class EventSourceRegistryTests(unittest.TestCase):
    def test_federal_reserve_is_registered_as_official_primary(self):
        result = assess_event_source("federal_reserve")

        self.assertEqual(result.source_class, "official_primary")
        self.assertEqual(result.source_role, "central_bank_publication")
        self.assertTrue(result.is_primary_source)
        self.assertEqual(result.assessment_status, "registered")

    def test_macro_statistical_sources_are_official_primary(self):
        for source in ("bls_latest", "bls_cpi", "bls_employment", "eia_energy"):
            with self.subTest(source=source):
                result = assess_event_source(source)
                self.assertEqual(result.source_class, "official_primary")
                self.assertTrue(result.is_primary_source)

    def test_nasdaq_feed_is_secondary_not_primary(self):
        result = assess_event_source("nasdaq_news")

        self.assertEqual(result.source_class, "news_aggregator")
        self.assertFalse(result.is_primary_source)

    def test_source_lookup_is_case_and_whitespace_tolerant(self):
        result = assess_event_source("  BLS_CPI  ")

        self.assertEqual(result.source, "bls_cpi")
        self.assertEqual(result.source_class, "official_primary")

    def test_unknown_source_is_not_assumed_trusted(self):
        result = assess_event_source("some_unreviewed_feed")

        self.assertEqual(result.source_class, "unknown")
        self.assertEqual(result.assessment_status, "unassessed")
        self.assertFalse(result.is_primary_source)

    def test_missing_source_is_unassessed(self):
        result = assess_event_source("")

        self.assertEqual(result.source, "")
        self.assertEqual(result.assessment_status, "unassessed")
        self.assertFalse(result.is_primary_source)


if __name__ == "__main__":
    unittest.main()
