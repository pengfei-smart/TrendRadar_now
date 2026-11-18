import unittest

from trendradar.market_data.opportunity_input_builder import (
    build_industry_opportunity_input,
    build_security_opportunity_input,
)


class OpportunityInputBuilderTests(unittest.TestCase):
    def test_industry_builder_normalizes_codes_and_evidence(self):
        result = build_industry_opportunity_input(
            industry_code=" sw2:801001 ",
            event_strength=0.8,
            evidence_type=" DIRECT ",
            relevance_score=0.9,
            transmission_distance=1,
            price_change_pct=3.2,
            volume_ratio=2.0,
            relative_strength_pct=1.5,
            breadth_ratio=0.7,
            leader_confirmation_ratio=0.8,
            age_hours=2.0,
            risk_flags=[" high_volatility ", "high_volatility"],
        )

        self.assertEqual(result.industry_code, "SW2:801001")
        self.assertEqual(result.evidence_type, "direct")
        self.assertEqual(result.risk_flags, ("high_volatility",))

    def test_industry_builder_preserves_missing_market_values(self):
        result = build_industry_opportunity_input(
            industry_code="SW2:801001",
            event_strength=0.8,
            evidence_type="direct",
            relevance_score=0.9,
            transmission_distance=0,
            price_change_pct=None,
            volume_ratio=None,
            relative_strength_pct=None,
            breadth_ratio=None,
            leader_confirmation_ratio=None,
            age_hours=1.0,
        )

        self.assertIsNone(result.price_change_pct)
        self.assertIsNone(result.volume_ratio)
        self.assertIsNone(result.relative_strength_pct)
        self.assertIsNone(result.breadth_ratio)
        self.assertIsNone(result.leader_confirmation_ratio)

    def test_security_builder_normalizes_codes(self):
        result = build_security_opportunity_input(
            security_code=" 000001 ",
            industry_code=" sw2:801001 ",
            industry_score=82.5,
            direct_event=True,
            event_strength=0.9,
            price_change_pct=4.0,
            volume_ratio=2.5,
            relative_strength_pct=3.0,
            liquidity_ratio=1.5,
            market_confirmation_ratio=0.8,
        )

        self.assertEqual(result.security_code, "000001")
        self.assertEqual(result.industry_code, "SW2:801001")
        self.assertEqual(result.industry_score, 82.5)

    def test_security_builder_allows_missing_market_observations(self):
        result = build_security_opportunity_input(
            security_code="000001",
            industry_code="SW2:801001",
            industry_score=75,
            direct_event=False,
            event_strength=0.0,
            price_change_pct=None,
            volume_ratio=None,
            relative_strength_pct=None,
            liquidity_ratio=None,
            market_confirmation_ratio=None,
        )

        self.assertIsNone(result.price_change_pct)
        self.assertIsNone(result.volume_ratio)
        self.assertIsNone(result.relative_strength_pct)
        self.assertIsNone(result.liquidity_ratio)
        self.assertIsNone(result.market_confirmation_ratio)

    def test_industry_unit_interval_is_validated(self):
        with self.assertRaises(ValueError):
            build_industry_opportunity_input(
                industry_code="SW2:801001",
                event_strength=1.1,
                evidence_type="direct",
                relevance_score=0.8,
                transmission_distance=0,
                price_change_pct=None,
                volume_ratio=None,
                relative_strength_pct=None,
                breadth_ratio=None,
                leader_confirmation_ratio=None,
                age_hours=1,
            )

    def test_industry_negative_age_is_rejected(self):
        with self.assertRaises(ValueError):
            build_industry_opportunity_input(
                industry_code="SW2:801001",
                event_strength=0.8,
                evidence_type="direct",
                relevance_score=0.8,
                transmission_distance=0,
                price_change_pct=None,
                volume_ratio=None,
                relative_strength_pct=None,
                breadth_ratio=None,
                leader_confirmation_ratio=None,
                age_hours=-1,
            )

    def test_negative_transmission_distance_is_rejected(self):
        with self.assertRaises(ValueError):
            build_industry_opportunity_input(
                industry_code="SW2:801001",
                event_strength=0.8,
                evidence_type="direct",
                relevance_score=0.8,
                transmission_distance=-1,
                price_change_pct=None,
                volume_ratio=None,
                relative_strength_pct=None,
                breadth_ratio=None,
                leader_confirmation_ratio=None,
                age_hours=1,
            )

    def test_security_industry_score_range_is_validated(self):
        with self.assertRaises(ValueError):
            build_security_opportunity_input(
                security_code="000001",
                industry_code="SW2:801001",
                industry_score=101,
                direct_event=False,
                event_strength=0,
                price_change_pct=None,
                volume_ratio=None,
                relative_strength_pct=None,
                liquidity_ratio=None,
                market_confirmation_ratio=None,
            )

    def test_ratio_fields_are_validated(self):
        with self.assertRaises(ValueError):
            build_industry_opportunity_input(
                industry_code="SW2:801001",
                event_strength=0.8,
                evidence_type="direct",
                relevance_score=0.8,
                transmission_distance=0,
                price_change_pct=None,
                volume_ratio=None,
                relative_strength_pct=None,
                breadth_ratio=1.2,
                leader_confirmation_ratio=None,
                age_hours=1,
            )

    def test_ineligible_flag_is_preserved(self):
        result = build_security_opportunity_input(
            security_code="000001",
            industry_code="SW2:801001",
            industry_score=70,
            direct_event=False,
            event_strength=0,
            price_change_pct=None,
            volume_ratio=None,
            relative_strength_pct=None,
            liquidity_ratio=None,
            market_confirmation_ratio=None,
            risk_flags=["risk_warning"],
            eligible=False,
        )

        self.assertFalse(result.eligible)
        self.assertEqual(result.risk_flags, ("risk_warning",))


if __name__ == "__main__":
    unittest.main()
