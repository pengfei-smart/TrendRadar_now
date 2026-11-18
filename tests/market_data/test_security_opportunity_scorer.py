import unittest

from trendradar.market_data.opportunity_models import SecurityOpportunityInput
from trendradar.market_data.security_opportunity_scorer import (
    SecurityOpportunityScorer,
    score_security_opportunity,
)


def make_input(**overrides):
    values = {
        "security_code": "600000",
        "industry_code": "SW2:801081",
        "industry_score": 100.0,
        "direct_event": True,
        "event_strength": 1.0,
        "price_change_pct": 10.0,
        "volume_ratio": 2.0,
        "relative_strength_pct": 10.0,
        "liquidity_ratio": 3.0,
        "market_confirmation_ratio": 1.0,
        "risk_flags": (),
    }
    values.update(overrides)
    return SecurityOpportunityInput(**values)


class SecurityOpportunityScorerTests(unittest.TestCase):
    def test_perfect_balanced_input_reaches_100(self):
        result = score_security_opportunity(make_input())
        self.assertEqual(result.raw_score, 100.0)
        self.assertEqual(result.score, 100.0)
        self.assertEqual(result.opportunity_level, "S")
        self.assertEqual(result.opportunity_type, "mixed")

    def test_normalized_event_strength_scales_to_zero_half_and_full_points(self):
        full = score_security_opportunity(make_input(event_strength=1.0))
        half = score_security_opportunity(make_input(event_strength=0.5))
        zero = score_security_opportunity(make_input(event_strength=0.0))
        self.assertEqual(full.event_score, 20.0)
        self.assertEqual(half.event_score, 10.0)
        self.assertEqual(zero.event_score, 0.0)

    def test_event_strength_outside_unit_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            score_security_opportunity(make_input(event_strength=1.01))
        with self.assertRaises(ValueError):
            score_security_opportunity(make_input(event_strength=-0.01))

    def test_industry_score_contributes_twenty_five_points(self):
        result = score_security_opportunity(make_input(industry_score=0.0))
        self.assertEqual(result.industry_opportunity_score, 0.0)
        self.assertEqual(result.raw_score, 75.0)

    def test_no_direct_event_does_not_block_industry_driven_candidate(self):
        result = score_security_opportunity(
            make_input(direct_event=False, event_strength=1.0)
        )
        self.assertEqual(result.event_score, 0.0)
        self.assertEqual(result.opportunity_type, "industry_driven")

    def test_direct_event_is_classified_separately_when_industry_is_weak(self):
        result = score_security_opportunity(make_input(industry_score=40.0))
        self.assertEqual(result.opportunity_type, "direct_event")

    def test_technical_confirmation_type_is_available(self):
        result = score_security_opportunity(
            make_input(industry_score=40.0, direct_event=False)
        )
        self.assertEqual(result.opportunity_type, "technical_confirmation")

    def test_missing_market_observations_score_zero(self):
        result = score_security_opportunity(
            make_input(
                price_change_pct=None,
                volume_ratio=None,
                relative_strength_pct=None,
                liquidity_ratio=None,
                market_confirmation_ratio=None,
            )
        )
        self.assertEqual(result.price_strength_score, 0.0)
        self.assertEqual(result.liquidity_score, 0.0)
        self.assertEqual(result.relative_strength_score, 0.0)
        self.assertEqual(result.market_confirmation_score, 0.0)

    def test_risk_adjustment_is_capped_at_minus_fifteen(self):
        result = score_security_opportunity(
            make_input(
                risk_flags=(
                    "data_stale",
                    "data_missing",
                    "low_liquidity",
                    "high_volatility",
                    "weak_market",
                    "conflicting_signal",
                    "unknown_risk",
                )
            )
        )
        self.assertEqual(result.risk_adjustment, -15.0)

    def test_ineligible_candidate_is_not_scored(self):
        result = score_security_opportunity(make_input(eligible=False))
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.opportunity_type, "ineligible")

    def test_invalid_codes_and_industry_score_are_rejected(self):
        with self.assertRaises(ValueError):
            score_security_opportunity(make_input(security_code=" "))
        with self.assertRaises(ValueError):
            score_security_opportunity(make_input(industry_code=" "))
        with self.assertRaises(ValueError):
            score_security_opportunity(make_input(industry_score=101.0))

    def test_confidence_reflects_missing_observations_and_risk(self):
        full = score_security_opportunity(make_input())
        partial = score_security_opportunity(
            make_input(
                price_change_pct=None,
                volume_ratio=None,
                relative_strength_pct=None,
                liquidity_ratio=None,
                market_confirmation_ratio=None,
                risk_flags=("data_missing",),
            )
        )
        self.assertGreater(full.confidence, partial.confidence)

    def test_explanation_contains_each_score_component(self):
        result = score_security_opportunity(make_input())
        self.assertEqual(len(result.explanation), 7)
        self.assertIn("行业机会贡献", result.explanation[0])
        self.assertIn("风险调整", result.explanation[-1])

    def test_object_wrapper_uses_same_pure_scoring_function(self):
        item = make_input(event_strength=0.6, price_change_pct=6.0)
        self.assertEqual(
            SecurityOpportunityScorer().score(item),
            score_security_opportunity(item),
        )


if __name__ == "__main__":
    unittest.main()
