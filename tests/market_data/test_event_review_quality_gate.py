import unittest

from trendradar.market_data.event_review_coordinator import assemble_event_review_board
from trendradar.market_data.event_review_models import (
    IMPACT_DIMENSIONS, REVIEWER_ROLES, ChiefAdjudication,
    ReviewEvidence, ReviewerAssessment,
)
from trendradar.market_data.event_review_quality_gate import (
    evaluate_event_review_quality,
)


class EventReviewQualityGateTests(unittest.TestCase):
    def setUp(self):
        self.evidence = (
            ReviewEvidence(
                evidence_id="primary-1", event_id="event-1",
                source="official_release", source_url="https://example.com/release",
                excerpt="Officially published event evidence.",
                evidence_kind="primary",
                supports_dimensions=("severity", "scope"),
            ),
        )

    def assessments(self, *, incomplete_role=None, omit_evidence_role=None):
        result = []
        for role in REVIEWER_ROLES:
            dimensions = IMPACT_DIMENSIONS
            if role == incomplete_role:
                dimensions = IMPACT_DIMENSIONS[:-1]
            result.append(ReviewerAssessment(
                event_id="event-1",
                reviewer_role=role,
                recommendation="support",
                completion_status="complete",
                dimension_scores=tuple(
                    (name, None if name == "surprise" else 0.5)
                    for name in dimensions
                ),
                dimension_rationales=tuple(
                    (name, "Evidence-based rationale.")
                    for name in dimensions
                ),
                evidence_ids=() if role == omit_evidence_role else ("primary-1",),
                confidence=0.7,
                summary="Review completed.",
                model_provider="test",
                model_name="reviewer",
                prompt_version="review-v1",
            ))
        return tuple(result)

    def adjudication(
        self, *, status="accepted", dissent=(), unresolved=(), followups=(),
        incomplete_dimensions=False, surprise= None,
    ):
        dimensions = IMPACT_DIMENSIONS[:-1] if incomplete_dimensions else IMPACT_DIMENSIONS
        return ChiefAdjudication(
            event_id="event-1",
            status=status,
            final_dimension_scores=tuple(
                (name, surprise if name == "surprise" else 0.5)
                for name in dimensions
            ),
            rationale="Final review rationale.",
            reviewer_roles_considered=REVIEWER_ROLES,
            dissenting_views=dissent,
            unresolved_issues=unresolved,
            required_followups=followups,
            chair_model_provider="test",
            chair_model_name="chair",
            prompt_version="chair-v1",
        )

    def board(self, *, assessments=None, adjudication=None, evidence=None):
        return assemble_event_review_board(
            event_id="event-1",
            evidence=self.evidence if evidence is None else evidence,
            reviewer_assessments=self.assessments() if assessments is None else assessments,
            adjudication=self.adjudication() if adjudication is None else adjudication,
        )

    def test_complete_accepted_board_with_primary_fact_check_passes(self):
        result = evaluate_event_review_quality(self.board())
        self.assertEqual(result.status, "passed")
        self.assertTrue(result.eligible_for_scoring)
        self.assertEqual(result.reasons, ())

    def test_conditional_decision_is_not_eligible_for_scoring(self):
        result = evaluate_event_review_quality(
            self.board(adjudication=self.adjudication(status="conditional"))
        )
        self.assertEqual(result.status, "blocked")
        self.assertFalse(result.eligible_for_scoring)
        self.assertTrue(any("不是 accepted" in reason for reason in result.reasons))

    def test_accepted_decision_with_followups_or_dissent_is_blocked(self):
        result = evaluate_event_review_quality(
            self.board(adjudication=self.adjudication(
                followups=("补充执行数据",),
                dissent=("产业链分析员保留异议",),
            ))
        )
        self.assertEqual(result.status, "blocked")
        self.assertTrue(any("后续证据" in reason for reason in result.reasons))
        self.assertTrue(any("分歧" in reason for reason in result.reasons))

    def test_incomplete_reviewer_dimensions_and_missing_evidence_are_blocked(self):
        result = evaluate_event_review_quality(self.board(
            assessments=self.assessments(
                incomplete_role="industry_chain_analyst",
                omit_evidence_role="macro_policy_analyst",
            )
        ))
        self.assertEqual(result.status, "blocked")
        self.assertTrue(any("评分维度不完整" in reason for reason in result.reasons))
        self.assertTrue(any("未引用证据" in reason for reason in result.reasons))

    def test_fact_checker_must_cite_primary_evidence(self):
        secondary = ReviewEvidence(
            evidence_id="secondary-1", event_id="event-1",
            source="news_aggregator", source_url="https://example.com/news",
            excerpt="Secondary report.", evidence_kind="secondary",
        )
        assessments = tuple(
            ReviewerAssessment(
                event_id=item.event_id,
                reviewer_role=item.reviewer_role,
                recommendation=item.recommendation,
                completion_status=item.completion_status,
                dimension_scores=item.dimension_scores,
                dimension_rationales=item.dimension_rationales,
                evidence_ids=("secondary-1",),
                confidence=item.confidence,
                summary=item.summary,
            )
            for item in self.assessments()
        )
        result = evaluate_event_review_quality(self.board(
            assessments=assessments,
            evidence=(secondary,),
        ))
        self.assertEqual(result.status, "blocked")
        self.assertTrue(any("一级来源" in reason for reason in result.reasons))

    def test_surprise_score_requires_dimension_specific_evidence(self):
        result = evaluate_event_review_quality(
            self.board(adjudication=self.adjudication(surprise=0.75))
        )
        self.assertEqual(result.status, "blocked")
        self.assertTrue(any("超预期评分" in reason for reason in result.reasons))

    def test_surprise_requires_timezone_aware_pre_event_baseline(self):
        baseline = ReviewEvidence(
            evidence_id="baseline-1", event_id="event-1",
            source="Consensus", source_url="https://example.com/consensus",
            excerpt="Published consensus before the event.",
            evidence_kind="secondary",
            published_at="2026-11-17T08:00:00+08:00",
            supports_dimensions=("surprise",),
        )
        assessments = list(self.assessments())
        original = assessments[-1]
        assessments[-1] = ReviewerAssessment(
            event_id=original.event_id, reviewer_role=original.reviewer_role,
            recommendation=original.recommendation, completion_status=original.completion_status,
            dimension_scores=original.dimension_scores,
            dimension_rationales=original.dimension_rationales,
            evidence_ids=("primary-1", "baseline-1"), confidence=original.confidence,
            summary=original.summary,
        )
        board = self.board(
            assessments=tuple(assessments),
            adjudication=self.adjudication(surprise=0.75),
            evidence=self.evidence + (baseline,),
        )
        missing_time = evaluate_event_review_quality(
            board, event_payload={},
        )
        self.assertEqual(missing_time.status, "blocked")
        self.assertTrue(any("发布时间早于事件发生时间" in reason for reason in missing_time.reasons))

        valid_time = evaluate_event_review_quality(
            board,
            event_payload={"event_time": "2026-11-17T09:00:00+08:00"},
        )
        self.assertEqual(valid_time.status, "passed")

    def test_surprise_baseline_published_after_event_is_blocked(self):
        baseline = ReviewEvidence(
            evidence_id="baseline-1", event_id="event-1",
            source="Late report", source_url="https://example.com/late",
            excerpt="Published after the event.",
            evidence_kind="secondary",
            published_at="2026-11-17T10:00:00+08:00",
            supports_dimensions=("surprise",),
        )
        board = self.board(
            adjudication=self.adjudication(surprise=0.75),
            evidence=self.evidence + (baseline,),
        )
        result = evaluate_event_review_quality(
            board, event_payload={"event_time": "2026-11-17T09:00:00+08:00"},
        )
        self.assertEqual(result.status, "blocked")

    def test_non_board_input_is_rejected(self):
        with self.assertRaises(TypeError):
            evaluate_event_review_quality(None)


if __name__ == "__main__":
    unittest.main()
