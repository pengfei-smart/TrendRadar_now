import unittest

from trendradar.market_data.opportunity_models import (
    IndustryOpportunityScore,
    SecurityOpportunityScore,
)
from trendradar.market_data.opportunity_ranking import (
    OpportunityRanking,
    deduplicate_industry_opportunities,
    deduplicate_security_opportunities,
    rank_industry_opportunities,
    rank_opportunities,
    rank_security_opportunities,
)


def industry(code, score, confidence=0.8):
    return IndustryOpportunityScore(
        target_code=code,
        score=score,
        raw_score=score,
        confidence=confidence,
        opportunity_level="strong" if score >= 80 else "watch",
        opportunity_type="direct",
        event_score=score,
        evidence_score=score,
        relevance_score=score,
        transmission_score=score,
        market_confirmation_score=score,
        freshness_score=score,
        risk_adjustment=0,
        explanation=("test",),
    )


def security(code, score, confidence=0.8):
    return SecurityOpportunityScore(
        target_code=code,
        industry_code="SW2:801010",
        score=score,
        raw_score=score,
        confidence=confidence,
        opportunity_level="strong" if score >= 80 else "watch",
        opportunity_type="industry_driven",
        industry_opportunity_score=score,
        event_score=score,
        price_strength_score=score,
        liquidity_score=score,
        relative_strength_score=score,
        market_confirmation_score=score,
        risk_adjustment=0,
        explanation=("test",),
    )


class OpportunityRankingTests(unittest.TestCase):

    def test_industry_ranking_orders_by_score(self):
        result = rank_industry_opportunities(
            [
                industry("SW2:801002", 70),
                industry("SW2:801001", 90),
                industry("SW2:801003", 80),
            ]
        )

        self.assertEqual(
            [item.target_code for item in result.items],
            [
                "SW2:801001",
                "SW2:801003",
                "SW2:801002",
            ],
        )

    def test_security_ranking_orders_by_score(self):
        result = rank_security_opportunities(
            [
                security("000002", 70),
                security("000001", 95),
                security("000003", 82),
            ]
        )

        self.assertEqual(
            [item.target_code for item in result.items],
            [
                "000001",
                "000003",
                "000002",
            ],
        )

    def test_zero_score_is_not_an_opportunity(self):
        result = rank_industry_opportunities(
            [
                industry("SW2:801001", 0),
                industry("SW2:801002", 50),
            ]
        )

        self.assertEqual(
            [item.target_code for item in result.items],
            ["SW2:801002"],
        )

    def test_limit_is_applied_after_sorting(self):
        result = rank_security_opportunities(
            [
                security("000001", 70),
                security("000002", 95),
                security("000003", 85),
            ],
            limit=2,
        )

        self.assertEqual(
            [item.target_code for item in result.items],
            ["000002", "000003"],
        )

    def test_confidence_breaks_score_ties(self):
        result = rank_industry_opportunities(
            [
                industry("SW2:801001", 80, confidence=0.70),
                industry("SW2:801002", 80, confidence=0.90),
            ]
        )

        self.assertEqual(
            [item.target_code for item in result.items],
            ["SW2:801002", "SW2:801001"],
        )

    def test_target_code_breaks_complete_ties(self):
        result = rank_security_opportunities(
            [
                security("000002", 80, confidence=0.80),
                security("000001", 80, confidence=0.80),
            ]
        )

        self.assertEqual(
            [item.target_code for item in result.items],
            ["000001", "000002"],
        )

    def test_counts_are_preserved(self):
        result = rank_industry_opportunities(
            [
                industry("SW2:801001", 90),
                industry("SW2:801002", 0),
                industry("SW2:801003", 70),
            ]
        )

        self.assertEqual(result.total_candidates, 3)
        self.assertEqual(result.eligible_candidates, 2)
        self.assertEqual(len(result.items), 2)

    def test_limit_validation(self):
        with self.assertRaises(ValueError):
            rank_industry_opportunities([], limit=0)

        with self.assertRaises(TypeError):
            rank_industry_opportunities([], limit=1.5)

    def test_combined_ranking_returns_both_boards(self):
        industries, securities = rank_opportunities(
            [industry("SW2:801001", 90)],
            [security("000001", 95)],
        )

        self.assertIsInstance(industries, OpportunityRanking)
        self.assertIsInstance(securities, OpportunityRanking)

        self.assertEqual(industries.items[0].target_code, "SW2:801001")
        self.assertEqual(securities.items[0].target_code, "000001")


if __name__ == "__main__":
    unittest.main()


class OpportunityRankingDeduplicationTests(unittest.TestCase):

    def test_duplicate_industry_keeps_highest_score(self):
        result = rank_industry_opportunities(
            [
                industry("SW2:801001", 72),
                industry("SW2:801001", 91),
                industry("SW2:801002", 80),
            ]
        )

        self.assertEqual(
            [item.target_code for item in result.items],
            ["SW2:801001", "SW2:801002"],
        )
        self.assertEqual(result.items[0].score, 91)

    def test_duplicate_security_keeps_highest_score(self):
        result = rank_security_opportunities(
            [
                security("000001", 78),
                security("000001", 94),
                security("000002", 85),
            ]
        )

        self.assertEqual(
            [item.target_code for item in result.items],
            ["000001", "000002"],
        )
        self.assertEqual(result.items[0].score, 94)

    def test_duplicate_target_uses_confidence_as_tiebreaker(self):
        result = rank_industry_opportunities(
            [
                industry("SW2:801001", 85, confidence=0.70),
                industry("SW2:801001", 85, confidence=0.95),
            ]
        )

        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].confidence, 0.95)

    def test_deduplication_happens_before_limit(self):
        result = rank_industry_opportunities(
            [
                industry("SW2:801001", 100),
                industry("SW2:801001", 99),
                industry("SW2:801002", 90),
                industry("SW2:801003", 80),
            ],
            limit=2,
        )

        self.assertEqual(
            [item.target_code for item in result.items],
            ["SW2:801001", "SW2:801002"],
        )

    def test_eligible_count_is_after_deduplication(self):
        result = rank_security_opportunities(
            [
                security("000001", 90),
                security("000001", 85),
                security("000002", 80),
                security("000003", 0),
            ]
        )

        self.assertEqual(result.total_candidates, 4)
        self.assertEqual(result.eligible_candidates, 2)
        self.assertEqual(len(result.items), 2)

    def test_explicit_deduplication_helpers(self):
        industries = deduplicate_industry_opportunities(
            [
                industry("SW2:801001", 70),
                industry("SW2:801001", 90),
            ]
        )
        securities = deduplicate_security_opportunities(
            [
                security("000001", 70),
                security("000001", 90),
            ]
        )

        self.assertEqual(len(industries), 1)
        self.assertEqual(industries[0].score, 90)

        self.assertEqual(len(securities), 1)
        self.assertEqual(securities[0].score, 90)


if __name__ == "__main__":
    unittest.main()
