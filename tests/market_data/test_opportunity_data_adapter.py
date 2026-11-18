import unittest
from types import SimpleNamespace

from trendradar.market_data.opportunity_data_adapter import (
    IndustryOpportunityObservation,
    SecurityOpportunityObservation,
    adapt_industry_observation,
    adapt_industry_observations,
    adapt_security_observation,
    adapt_security_observations,
)


def industry_observation(
    code: str = " sw2:801001 ",
    *,
    eligible: bool = True,
) -> IndustryOpportunityObservation:
    return IndustryOpportunityObservation(
        industry_code=code,
        event_strength=0.9,
        evidence_type=" DIRECT ",
        relevance_score=0.95,
        transmission_distance=0,
        price_change_pct=3.2,
        volume_ratio=2.1,
        relative_strength_pct=1.8,
        breadth_ratio=0.75,
        leader_confirmation_ratio=0.8,
        age_hours=1.5,
        risk_flags=(" high_volatility ", "high_volatility"),
        eligible=eligible,
    )


def security_observation(
    code: str = " 000001 ",
    *,
    direct_event: bool = True,
    eligible: bool = True,
) -> SecurityOpportunityObservation:
    return SecurityOpportunityObservation(
        security_code=code,
        industry_code=" sw2:801001 ",
        industry_score=86.0,
        direct_event=direct_event,
        event_strength=0.8,
        price_change_pct=4.5,
        volume_ratio=2.8,
        relative_strength_pct=3.0,
        liquidity_ratio=1.7,
        market_confirmation_ratio=0.85,
        risk_flags=(" risk_warning ",),
        eligible=eligible,
    )


class OpportunityDataAdapterTests(unittest.TestCase):
    def test_industry_observation_is_converted(self):
        result = adapt_industry_observation(industry_observation())

        self.assertEqual(result.industry_code, "SW2:801001")
        self.assertEqual(result.evidence_type, "direct")
        self.assertEqual(result.event_strength, 0.9)
        self.assertEqual(result.risk_flags, ("high_volatility",))
        self.assertTrue(result.eligible)

    def test_security_observation_is_converted(self):
        result = adapt_security_observation(security_observation())

        self.assertEqual(result.security_code, "000001")
        self.assertEqual(result.industry_code, "SW2:801001")
        self.assertEqual(result.industry_score, 86.0)
        self.assertTrue(result.direct_event)
        self.assertEqual(result.risk_flags, ("risk_warning",))

    def test_industry_missing_market_values_are_preserved(self):
        observation = IndustryOpportunityObservation(
            industry_code="SW2:801001",
            event_strength=0.7,
            evidence_type="industry",
            relevance_score=0.8,
            transmission_distance=1,
            price_change_pct=None,
            volume_ratio=None,
            relative_strength_pct=None,
            breadth_ratio=None,
            leader_confirmation_ratio=None,
            age_hours=2.0,
        )

        result = adapt_industry_observation(observation)

        self.assertIsNone(result.price_change_pct)
        self.assertIsNone(result.volume_ratio)
        self.assertIsNone(result.relative_strength_pct)
        self.assertIsNone(result.breadth_ratio)
        self.assertIsNone(result.leader_confirmation_ratio)

    def test_security_missing_market_values_are_preserved(self):
        observation = SecurityOpportunityObservation(
            security_code="000001",
            industry_code="SW2:801001",
            industry_score=75.0,
            direct_event=False,
            event_strength=0.0,
            price_change_pct=None,
            volume_ratio=None,
            relative_strength_pct=None,
            liquidity_ratio=None,
            market_confirmation_ratio=None,
        )

        result = adapt_security_observation(observation)

        self.assertIsNone(result.price_change_pct)
        self.assertIsNone(result.volume_ratio)
        self.assertIsNone(result.relative_strength_pct)
        self.assertIsNone(result.liquidity_ratio)
        self.assertIsNone(result.market_confirmation_ratio)

    def test_ineligible_industry_is_preserved(self):
        result = adapt_industry_observation(
            industry_observation(eligible=False)
        )

        self.assertFalse(result.eligible)

    def test_ineligible_security_is_preserved(self):
        result = adapt_security_observation(
            security_observation(eligible=False)
        )

        self.assertFalse(result.eligible)

    def test_direct_event_must_be_boolean(self):
        observation = security_observation()

        object.__setattr__(observation, "direct_event", "true")

        with self.assertRaises(TypeError):
            adapt_security_observation(observation)

    def test_eligible_must_be_boolean(self):
        observation = industry_observation()

        object.__setattr__(observation, "eligible", "false")

        with self.assertRaises(TypeError):
            adapt_industry_observation(observation)

    def test_wrong_industry_observation_type_is_rejected(self):
        with self.assertRaises(TypeError):
            adapt_industry_observation(object())  # type: ignore[arg-type]

    def test_wrong_security_observation_type_is_rejected(self):
        with self.assertRaises(TypeError):
            adapt_security_observation(object())  # type: ignore[arg-type]

    def test_industry_collection_adapter_preserves_order(self):
        observations = (
            industry_observation("SW2:801002"),
            industry_observation("SW2:801001"),
        )

        result = adapt_industry_observations(observations)

        self.assertEqual(
            tuple(item.industry_code for item in result),
            ("SW2:801002", "SW2:801001"),
        )

    def test_snapshot_market_breadth_overrides_unverified_observation_values(self):
        snapshot = SimpleNamespace(
            eligible_securities=tuple(
                SimpleNamespace(code=code)
                for code in ("600001", "600002", "600003", "600004", "600005")
            ),
            security_sector_codes=(
                ("600001", ("SW2:801081",)),
                ("600002", ("SW2:801081",)),
                ("600003", ("SW2:801081",)),
                ("600004", ("SW2:801081",)),
                ("600005", ("SW2:801081",)),
            ),
            quotes=(
                SimpleNamespace(code="600001", change_pct=2.0),
                SimpleNamespace(code="600002", change_pct=-1.0),
                SimpleNamespace(code="600003", change_pct=0.0),
                SimpleNamespace(code="600004", change_pct=3.0),
            ),
            quote_freshness=(
                SimpleNamespace(security_code="600001", status="fresh"),
                SimpleNamespace(security_code="600002", status="fresh"),
                SimpleNamespace(security_code="600003", status="fresh"),
                SimpleNamespace(security_code="600004", status="stale"),
            ),
        )
        result = adapt_industry_observations(
            (industry_observation("SW2:801081"),),
            snapshot=snapshot,
        )[0]

        self.assertAlmostEqual(result.breadth_ratio, 1 / 3)
        self.assertEqual(result.quote_coverage_ratio, 0.6)

    def test_snapshot_low_coverage_is_passed_to_scorer_input(self):
        snapshot = SimpleNamespace(
            eligible_securities=tuple(
                SimpleNamespace(code=code)
                for code in ("600001", "600002", "600003", "600004", "600005")
            ),
            security_sector_codes=tuple(
                (code, ("SW2:801081",))
                for code in ("600001", "600002", "600003", "600004", "600005")
            ),
            quotes=(
                SimpleNamespace(code="600001", change_pct=2.0),
                SimpleNamespace(code="600002", change_pct=-1.0),
            ),
            quote_freshness=(
                SimpleNamespace(security_code="600001", status="fresh"),
                SimpleNamespace(security_code="600002", status="fresh"),
            ),
        )
        result = adapt_industry_observations(
            (industry_observation("SW2:801081"),),
            snapshot=snapshot,
        )[0]

        self.assertEqual(result.breadth_ratio, 0.5)
        self.assertEqual(result.quote_coverage_ratio, 0.4)

    def test_security_collection_adapter_preserves_order(self):
        observations = (
            security_observation("000002"),
            security_observation("000001"),
        )

        result = adapt_security_observations(observations)

        self.assertEqual(
            tuple(item.security_code for item in result),
            ("000002", "000001"),
        )

    def test_empty_collections_return_empty_tuples(self):
        self.assertEqual(adapt_industry_observations([]), ())
        self.assertEqual(adapt_security_observations([]), ())


if __name__ == "__main__":
    unittest.main()
