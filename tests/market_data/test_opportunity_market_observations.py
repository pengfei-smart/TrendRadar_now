import unittest
from types import SimpleNamespace

from trendradar.market_data.opportunity_market_observations import (
    calculate_industry_market_breadth,
)


def make_snapshot():
    securities = tuple(
        SimpleNamespace(code=code)
        for code in ("600001", "600002", "600003", "600004", "600005")
    )
    quotes = (
        SimpleNamespace(code="600001", change_pct=2.0),
        SimpleNamespace(code="600002", change_pct=-1.0),
        SimpleNamespace(code="600003", change_pct=0.0),
        SimpleNamespace(code="600004", change_pct=3.0),
        SimpleNamespace(code="600005", change_pct=None),
        # Not eligible: must not enter any sector's counts.
        SimpleNamespace(code="688001", change_pct=9.0),
    )
    freshness = tuple(
        SimpleNamespace(security_code=code, status=status)
        for code, status in (
            ("600001", "fresh"),
            ("600002", "fresh"),
            ("600003", "fresh"),
            ("600004", "stale"),
            ("600005", "fresh"),
            ("688001", "fresh"),
        )
    )
    return SimpleNamespace(
        eligible_securities=securities,
        quotes=quotes,
        quote_freshness=freshness,
        security_sector_codes=(
            ("600001", ("SW1:801080", "SW2:801081")),
            ("600002", ("SW1:801080", "SW2:801081")),
            ("600003", ("SW2:801081",)),
            ("600004", ("SW2:801081",)),
            ("600005", ("SW2:801081",)),
            ("688001", ("SW2:801081",)),
        ),
    )


class OpportunityMarketObservationTests(unittest.TestCase):
    def test_breadth_uses_fresh_changes_and_coverage_uses_eligible_members(self):
        (item,) = calculate_industry_market_breadth(make_snapshot())

        self.assertEqual(item.industry_code, "SW2:801081")
        self.assertEqual(item.eligible_security_count, 5)
        self.assertEqual(item.fresh_quote_count, 4)
        self.assertEqual(item.observed_quote_count, 3)
        self.assertEqual(item.advancing_count, 1)
        self.assertEqual(item.declining_count, 1)
        self.assertEqual(item.unchanged_count, 1)
        self.assertAlmostEqual(item.breadth_ratio, 1 / 3)
        self.assertEqual(item.quote_coverage_ratio, 3 / 5)

    def test_stale_quotes_do_not_contribute_to_breadth_or_coverage(self):
        snapshot = make_snapshot()
        snapshot.quote_freshness = tuple(
            SimpleNamespace(security_code=item.security_code, status="stale")
            if item.security_code == "600001" else item
            for item in snapshot.quote_freshness
        )
        (item,) = calculate_industry_market_breadth(snapshot)
        self.assertEqual(item.advancing_count, 0)
        self.assertEqual(item.observed_quote_count, 2)
        self.assertEqual(item.quote_coverage_ratio, 2 / 5)

    def test_missing_change_pct_counts_as_fresh_quote_but_not_coverage(self):
        (item,) = calculate_industry_market_breadth(make_snapshot())
        self.assertEqual(item.fresh_quote_count, 4)
        self.assertEqual(item.observed_quote_count, 3)
        self.assertEqual(item.quote_coverage_ratio, 0.6)

    def test_no_usable_observations_returns_none_breadth_not_zero(self):
        snapshot = make_snapshot()
        snapshot.quote_freshness = tuple(
            SimpleNamespace(security_code=item.security_code, status="stale")
            for item in snapshot.quote_freshness
        )
        (item,) = calculate_industry_market_breadth(snapshot)
        self.assertIsNone(item.breadth_ratio)
        self.assertEqual(item.quote_coverage_ratio, 0.0)

    def test_only_sw2_memberships_are_aggregated(self):
        (item,) = calculate_industry_market_breadth(make_snapshot())
        self.assertEqual(item.industry_code, "SW2:801081")
        self.assertFalse(item.industry_code.startswith("SW1:"))

    def test_unmapped_and_ineligible_securities_are_not_counted(self):
        snapshot = make_snapshot()
        snapshot.security_sector_codes += (
            ("688001", ("SW2:801081",)),
            ("999999", ("SW2:801081",)),
        )
        (item,) = calculate_industry_market_breadth(snapshot)
        self.assertEqual(item.eligible_security_count, 5)

    def test_duplicate_quote_rows_are_rejected(self):
        snapshot = make_snapshot()
        snapshot.quotes += (snapshot.quotes[0],)
        with self.assertRaisesRegex(ValueError, "重复行情记录"):
            calculate_industry_market_breadth(snapshot)

    def test_duplicate_freshness_rows_are_rejected(self):
        snapshot = make_snapshot()
        snapshot.quote_freshness += (snapshot.quote_freshness[0],)
        with self.assertRaisesRegex(ValueError, "重复行情新鲜度记录"):
            calculate_industry_market_breadth(snapshot)

    def test_non_finite_change_values_are_not_observations(self):
        snapshot = make_snapshot()
        snapshot.quotes = tuple(
            SimpleNamespace(code=item.code, change_pct=float("nan"))
            if item.code == "600001" else item
            for item in snapshot.quotes
        )
        (item,) = calculate_industry_market_breadth(snapshot)
        self.assertEqual(item.observed_quote_count, 2)
        self.assertEqual(item.quote_coverage_ratio, 0.4)


if __name__ == "__main__":
    unittest.main()
