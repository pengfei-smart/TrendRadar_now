import unittest

from trendradar.market_data.opportunity_models import (
    IndustryOpportunityInput,
    SecurityOpportunityInput,
)
from trendradar.market_data.opportunity_radar import OpportunityRadarEngine


def industry_input(
    code: str,
    *,
    event_strength: float = 1.0,
    evidence_type: str = "direct",
    relevance_score: float = 1.0,
    transmission_distance: int = 0,
    price_change_pct: float | None = 3.0,
    volume_ratio: float | None = 2.0,
    relative_strength_pct: float | None = 2.0,
    breadth_ratio: float | None = 0.8,
    leader_confirmation_ratio: float | None = 0.8,
    age_hours: float = 1.0,
) -> IndustryOpportunityInput:
    return IndustryOpportunityInput(
        industry_code=code,
        event_strength=event_strength,
        evidence_type=evidence_type,
        relevance_score=relevance_score,
        transmission_distance=transmission_distance,
        price_change_pct=price_change_pct,
        volume_ratio=volume_ratio,
        relative_strength_pct=relative_strength_pct,
        breadth_ratio=breadth_ratio,
        leader_confirmation_ratio=leader_confirmation_ratio,
        age_hours=age_hours,
    )


def security_input(
    code: str,
    industry_code: str,
    industry_score: float,
    *,
    direct_event: bool = False,
) -> SecurityOpportunityInput:
    return SecurityOpportunityInput(
        security_code=code,
        industry_code=industry_code,
        industry_score=industry_score,
        direct_event=direct_event,
        event_strength=1.0 if direct_event else 0.0,
        price_change_pct=3.0,
        volume_ratio=2.0,
        relative_strength_pct=2.0,
        liquidity_ratio=1.5,
        market_confirmation_ratio=0.8,
    )


class OpportunityRadarEngineTests(unittest.TestCase):
    def test_empty_inputs_return_empty_boards(self):
        result = OpportunityRadarEngine().run([], [])

        self.assertEqual(result.industry_board, ())
        self.assertEqual(result.security_board, ())
        self.assertEqual(result.industry_candidate_count, 0)
        self.assertEqual(result.security_candidate_count, 0)

    def test_engine_returns_both_boards(self):
        result = OpportunityRadarEngine(
            industry_limit=5,
            security_limit=5,
        ).run(
            [industry_input("SW2:801001")],
            [security_input("000001", "SW2:801001", 90)],
        )

        self.assertEqual(len(result.industry_board), 1)
        self.assertEqual(len(result.security_board), 1)
        self.assertEqual(result.industry_board[0].target_code, "SW2:801001")
        self.assertEqual(result.security_board[0].target_code, "000001")

    def test_engine_applies_limits(self):
        result = OpportunityRadarEngine(
            industry_limit=1,
            security_limit=1,
        ).run(
            [
                industry_input("SW2:801001"),
                industry_input("SW2:801002"),
            ],
            [
                security_input("000001", "SW2:801001", 90),
                security_input("000002", "SW2:801002", 90),
            ],
        )

        self.assertEqual(len(result.industry_board), 1)
        self.assertEqual(len(result.security_board), 1)

    def test_engine_preserves_warnings(self):
        result = OpportunityRadarEngine().run(
            [],
            [],
            warnings=["market data partially unavailable"],
        )

        self.assertEqual(
            result.warnings,
            ("market data partially unavailable",),
        )

    def test_ineligible_candidates_are_excluded(self):
        industry = industry_input("SW2:801001")
        industry = IndustryOpportunityInput(
            **{
                **industry.__dict__,
                "eligible": False,
            }
        )

        security = security_input("000001", "SW2:801001", 90)
        security = SecurityOpportunityInput(
            **{
                **security.__dict__,
                "eligible": False,
            }
        )

        result = OpportunityRadarEngine().run(
            [industry],
            [security],
        )

        self.assertEqual(result.industry_board, ())
        self.assertEqual(result.security_board, ())
        self.assertEqual(result.industry_candidate_count, 0)
        self.assertEqual(result.security_candidate_count, 0)

    def test_duplicate_targets_are_deduplicated_before_limit(self):
        result = OpportunityRadarEngine(
            industry_limit=1,
            security_limit=1,
        ).run(
            [
                industry_input("SW2:801001"),
                industry_input(
                    "SW2:801001",
                    event_strength=0.5,
                ),
                industry_input("SW2:801002"),
            ],
            [
                security_input("000001", "SW2:801001", 90),
                security_input("000001", "SW2:801001", 80),
                security_input("000002", "SW2:801002", 70),
            ],
        )

        self.assertEqual(len(result.industry_board), 1)
        self.assertEqual(len(result.security_board), 1)

        self.assertEqual(
            result.industry_board[0].target_code,
            "SW2:801001",
        )
        self.assertEqual(
            result.security_board[0].target_code,
            "000001",
        )


if __name__ == "__main__":
    unittest.main()
