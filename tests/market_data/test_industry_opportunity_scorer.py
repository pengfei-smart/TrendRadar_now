import unittest

from trendradar.market_data.industry_opportunity_scorer import (
    IndustryOpportunityScorer,
    score_industry_opportunity,
)
from trendradar.market_data.opportunity_models import IndustryOpportunityInput


def make_input(**overrides):
    values = {
        "industry_code": "SW2:801081",
        "event_strength": 20.0,
        "evidence_type": "direct",
        "relevance_score": 15.0,
        "transmission_distance": 0,
        "price_change_pct": 6.0,
        "volume_ratio": 2.5,
        "relative_strength_pct": 5.0,
        "breadth_ratio": 1.0,
        "leader_confirmation_ratio": 1.0,
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
        item = make_input(event_strength=12.0, age_hours=6.0)
        self.assertEqual(
            IndustryOpportunityScorer().score(item),
            score_industry_opportunity(item),
        )


if __name__ == "__main__":
    unittest.main()
