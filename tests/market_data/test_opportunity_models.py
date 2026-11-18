import unittest
from dataclasses import FrozenInstanceError

from trendradar.market_data.opportunity_models import (
    IndustryOpportunityInput,
    IndustryOpportunityScore,
    SecurityOpportunityInput,
    SecurityOpportunityScore,
)


class OpportunityModelsTests(unittest.TestCase):
    def test_industry_input_preserves_normalized_market_and_evidence_fields(self):
        item = IndustryOpportunityInput(
            industry_code="SW2:801081",
            event_strength=15.0,
            evidence_type="direct",
            relevance_score=15.0,
            transmission_distance=0,
            price_change_pct=2.4,
            volume_ratio=1.8,
            relative_strength_pct=1.2,
            breadth_ratio=0.68,
            leader_confirmation_ratio=0.75,
            age_hours=3.0,
            risk_flags=("low_liquidity",),
        )
        self.assertEqual(item.industry_code, "SW2:801081")
        self.assertEqual(item.evidence_type, "direct")
        self.assertEqual(item.transmission_distance, 0)
        self.assertEqual(item.risk_flags, ("low_liquidity",))
        self.assertTrue(item.eligible)

    def test_industry_input_allows_missing_market_observations(self):
        item = IndustryOpportunityInput(
            industry_code="SW2:801081",
            event_strength=10.0,
            evidence_type="official_name",
            relevance_score=13.0,
            transmission_distance=0,
            price_change_pct=None,
            volume_ratio=None,
            relative_strength_pct=None,
            breadth_ratio=None,
            leader_confirmation_ratio=None,
            age_hours=12.0,
        )
        self.assertIsNone(item.price_change_pct)
        self.assertIsNone(item.volume_ratio)
        self.assertEqual(item.risk_flags, ())

    def test_security_input_keeps_industry_and_direct_event_separate(self):
        item = SecurityOpportunityInput(
            security_code="600000",
            industry_code="SW2:801081",
            industry_score=82.0,
            direct_event=True,
            event_strength=18.0,
            price_change_pct=5.2,
            volume_ratio=2.1,
            relative_strength_pct=1.7,
            liquidity_ratio=1.4,
            market_confirmation_ratio=0.8,
        )
        self.assertEqual(item.security_code, "600000")
        self.assertEqual(item.industry_code, "SW2:801081")
        self.assertTrue(item.direct_event)
        self.assertEqual(item.industry_score, 82.0)

    def test_security_input_can_mark_ineligible_candidate(self):
        item = SecurityOpportunityInput(
            security_code="600000",
            industry_code="SW2:801081",
            industry_score=70.0,
            direct_event=False,
            event_strength=0.0,
            price_change_pct=None,
            volume_ratio=None,
            relative_strength_pct=None,
            liquidity_ratio=None,
            market_confirmation_ratio=None,
            risk_flags=("suspended",),
            eligible=False,
        )
        self.assertFalse(item.eligible)
        self.assertIn("suspended", item.risk_flags)

    def test_industry_score_exposes_all_score_components_and_explanation(self):
        result = IndustryOpportunityScore(
            target_code="SW2:801081",
            score=82.0,
            raw_score=73.8,
            confidence=0.86,
            opportunity_level="A",
            opportunity_type="event_driven",
            event_score=15.0,
            evidence_score=15.0,
            relevance_score=15.0,
            transmission_score=10.0,
            market_confirmation_score=18.0,
            freshness_score=4.8,
            risk_adjustment=-4.0,
            explanation=("行业事件直接关联", "成交活跃度改善"),
        )
        self.assertEqual(result.score, 82.0)
        self.assertEqual(result.raw_score, 73.8)
        self.assertEqual(result.risk_adjustment, -4.0)
        self.assertEqual(len(result.explanation), 2)

    def test_security_score_exposes_all_score_components(self):
        result = SecurityOpportunityScore(
            target_code="600000",
            industry_code="SW2:801081",
            score=88.0,
            raw_score=83.6,
            confidence=0.91,
            opportunity_level="S",
            opportunity_type="direct_security",
            industry_opportunity_score=82.0,
            event_score=15.0,
            price_strength_score=13.0,
            liquidity_score=14.0,
            relative_strength_score=9.0,
            market_confirmation_score=9.0,
            risk_adjustment=-2.0,
            explanation=("个股存在直接事件关联",),
        )
        self.assertEqual(result.industry_code, "SW2:801081")
        self.assertEqual(result.industry_opportunity_score, 82.0)
        self.assertEqual(result.opportunity_type, "direct_security")

    def test_score_models_are_immutable(self):
        result = IndustryOpportunityScore(
            target_code="SW2:801081",
            score=60.0,
            raw_score=54.0,
            confidence=0.5,
            opportunity_level="C",
            opportunity_type="event_driven",
            event_score=10.0,
            evidence_score=5.0,
            relevance_score=10.0,
            transmission_score=8.0,
            market_confirmation_score=15.0,
            freshness_score=4.0,
            risk_adjustment=0.0,
        )
        with self.assertRaises(FrozenInstanceError):
            result.score = 70.0


if __name__ == "__main__":
    unittest.main()
