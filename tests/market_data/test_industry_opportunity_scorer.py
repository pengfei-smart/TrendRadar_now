import unittest

from trendradar.market_data.industry_opportunity_scorer import (
    IndustryOpportunityScorer,
    score_industry_opportunity,
)
from trendradar.market_data.opportunity_models import IndustryOpportunityInput


def make_input(**overrides):
    values = {
        "industry_code": "SW2:801081",
        "event_strength": 1.0,
        "evidence_type": "direct",
        "relevance_score": 1.0,
        "transmission_distance": 0,
        "price_change_pct": 6.0,
        "volume_ratio": 2.5,
        "relative_strength_pct": 5.0,
        "breadth_ratio": 1.0,
        "leader_confirmation_ratio": 1.0,
        "quote_coverage_ratio": 1.0,
        "age_hours": 0.0,
        "risk_flags": (),
    }
    values.update(overrides)
    return IndustryOpportunityInput(**values)


class IndustryOpportunityScorerTests(unittest.TestCase):
    def test_perfect_balanced_input_reaches_95_before_risk(self):
        result = score_industry_opportunity(make_input())
        self.assertEqual(result.raw_score, 95.0)
        self.assertEqual(result.score, 95.0)
        self.assertEqual(result.opportunity_level, "S")
        self.assertEqual(result.risk_adjustment, 0.0)

    def test_normalized_event_and_relevance_inputs_scale_to_full_component_points(self):
        full = score_industry_opportunity(make_input(event_strength=1.0, relevance_score=1.0))
        half = score_industry_opportunity(make_input(event_strength=0.5, relevance_score=0.5))
        zero = score_industry_opportunity(make_input(event_strength=0.0, relevance_score=0.0))
        self.assertEqual(full.event_score, 20.0)
        self.assertEqual(full.relevance_score, 15.0)
        self.assertEqual(half.event_score, 10.0)
        self.assertEqual(half.relevance_score, 7.5)
        self.assertEqual(zero.event_score, 0.0)
        self.assertEqual(zero.relevance_score, 0.0)

    def test_event_and_relevance_outside_unit_interval_are_rejected(self):
        with self.assertRaises(ValueError):
            score_industry_opportunity(make_input(event_strength=1.01))
        with self.assertRaises(ValueError):
            score_industry_opportunity(make_input(relevance_score=-0.01))

    def test_risk_adjustment_is_applied_after_base_score(self):
        result = score_industry_opportunity(
            make_input(risk_flags=("data_stale", "low_liquidity"))
        )
        self.assertEqual(result.raw_score, 95.0)
        self.assertEqual(result.risk_adjustment, -5.0)
        self.assertEqual(result.score, 90.0)

    def test_freshness_decays_to_zero_after_48_hours(self):
        fresh = score_industry_opportunity(make_input(age_hours=0.0))
        stale = score_industry_opportunity(make_input(age_hours=48.0))
        self.assertEqual(fresh.freshness_score, 10.0)
        self.assertEqual(stale.freshness_score, 0.0)
        self.assertEqual(fresh.raw_score - stale.raw_score, 10.0)

    def test_direct_and_transmission_opportunities_are_distinguished(self):
        direct = score_industry_opportunity(make_input(evidence_type="direct"))
        transmitted = score_industry_opportunity(
            make_input(evidence_type="upstream", transmission_distance=2)
        )
        self.assertEqual(direct.opportunity_type, "event_driven")
        self.assertEqual(transmitted.opportunity_type, "transmission_driven")
        self.assertGreater(direct.evidence_score, transmitted.evidence_score)
        self.assertGreater(direct.transmission_score, transmitted.transmission_score)

    def test_breadth_points_require_at_least_sixty_percent_quote_coverage(self):
        sufficient = score_industry_opportunity(
            make_input(quote_coverage_ratio=0.60)
        )
        insufficient = score_industry_opportunity(
            make_input(quote_coverage_ratio=0.59)
        )
        self.assertEqual(
            sufficient.market_confirmation_score - insufficient.market_confirmation_score,
            5.0,
        )
        self.assertIn("上涨广度不计入市场确认分", insufficient.explanation[1])

    def test_low_coverage_reduces_breadth_confidence_without_risk_penalty(self):
        result = score_industry_opportunity(
            make_input(breadth_ratio=1.0, quote_coverage_ratio=0.40)
        )
        self.assertAlmostEqual(result.confidence, 0.85)
        self.assertEqual(result.risk_adjustment, 0.0)

    def test_missing_coverage_does_not_award_breadth_points(self):
        result = score_industry_opportunity(
            make_input(quote_coverage_ratio=None)
        )
        self.assertEqual(result.market_confirmation_score, 20.0)
        self.assertIn("行情覆盖率不足60%", result.explanation[1])

    def test_quote_coverage_outside_unit_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            score_industry_opportunity(make_input(quote_coverage_ratio=1.01))
        with self.assertRaises(ValueError):
            score_industry_opportunity(make_input(quote_coverage_ratio=-0.01))

    def test_market_confirmation_is_zero_when_observations_are_missing(self):
        result = score_industry_opportunity(
            make_input(
                price_change_pct=None,
                volume_ratio=None,
                relative_strength_pct=None,
                breadth_ratio=None,
                leader_confirmation_ratio=None,
            )
        )
        self.assertEqual(result.market_confirmation_score, 0.0)

    def test_unknown_evidence_is_conservatively_scored(self):
        result = score_industry_opportunity(make_input(evidence_type="unknown_type"))
        self.assertEqual(result.evidence_score, 5.0)
        self.assertEqual(result.opportunity_type, "transmission_driven")

    def test_ineligible_candidate_is_not_scored_as_an_opportunity(self):
        result = score_industry_opportunity(make_input(eligible=False))
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.raw_score, 0.0)
        self.assertEqual(result.opportunity_level, "D")
        self.assertEqual(result.opportunity_type, "ineligible")

    def test_empty_industry_code_is_rejected(self):
        with self.assertRaises(ValueError):
            score_industry_opportunity(make_input(industry_code=" "))

    def test_negative_event_age_is_rejected(self):
        with self.assertRaises(ValueError):
            score_industry_opportunity(make_input(age_hours=-0.1))

    def test_risk_adjustment_is_capped_at_minus_15(self):
        result = score_industry_opportunity(
            make_input(
                risk_flags=(
                    "data_stale",
                    "data_missing",
                    "low_liquidity",
                    "weak_breadth",
                    "high_volatility",
                    "conflicting_signal",
                    "unknown_risk",
                )
            )
        )
        self.assertEqual(result.risk_adjustment, -15.0)

    def test_object_wrapper_uses_same_pure_scoring_function(self):
        item = make_input(event_strength=0.6, relevance_score=0.8, age_hours=6.0)
        self.assertEqual(
            IndustryOpportunityScorer().score(item),
            score_industry_opportunity(item),
        )


if __name__ == "__main__":
    unittest.main()
