import unittest

from trendradar.market_data.event_review_models import (
    ADJUDICATION_STATUSES,
    REVIEWER_ROLES,
    ChiefAdjudication,
    ReviewEvidence,
    ReviewerAssessment,
)


class EventReviewModelsTests(unittest.TestCase):
    def test_five_review_roles_are_explicit(self):
        self.assertEqual(
            REVIEWER_ROLES,
            (
                "fact_checker",
                "macro_policy_analyst",
                "industry_chain_analyst",
                "market_expectations_analyst",
            ),
        )
        self.assertEqual(
            ADJUDICATION_STATUSES,
            {"accepted", "conditional", "needs_evidence", "rejected"},
        )

    def test_evidence_preserves_source_excerpt_and_dimension_scope(self):
        evidence = ReviewEvidence(
            evidence_id="ev-1",
            event_id="event-1",
            source="official_release",
            source_url="https://example.invalid/release",
            excerpt="正式发布的政策文本",
            evidence_kind="primary",
            published_at="2026-11-18T09:00:00+08:00",
            supports_dimensions=("severity", "scope", "severity"),
        )

        self.assertEqual(evidence.evidence_kind, "primary")
        self.assertEqual(evidence.supports_dimensions, ("severity", "scope"))

    def test_evidence_rejects_unknown_kind_or_dimension(self):
        base = dict(
            evidence_id="ev-1", event_id="event-1", source="source",
            source_url="", excerpt="excerpt",
        )
        with self.assertRaises(ValueError):
            ReviewEvidence(**base, evidence_kind="unverified")
        with self.assertRaises(ValueError):
            ReviewEvidence(**base, evidence_kind="primary",
                           supports_dimensions=("market_direction",))

    def test_reviewer_assessment_records_independent_scores_and_model_trace(self):
        assessment = ReviewerAssessment(
            event_id="event-1",
            reviewer_role="industry_chain_analyst",
            recommendation="support",
            completion_status="complete",
            dimension_scores=(("severity", 0.75), ("scope", 0.5)),
            dimension_rationales=(
                ("severity", "正式措施对关键环节产生直接影响"),
                ("scope", "目前证据仅覆盖一个行业"),
            ),
            evidence_ids=("ev-1", "ev-2", "ev-1"),
            confidence=0.8,
            summary="影响较大，但范围仍有限",
            concerns=("尚无企业执行数据",),
            model_provider="provider-a",
            model_name="model-x",
            model_version="2026-11",
            prompt_version="review-v1",
        )

        self.assertEqual(assessment.evidence_ids, ("ev-1", "ev-2"))
        self.assertEqual(assessment.dimension_scores[0], ("severity", 0.75))
        self.assertEqual(assessment.model_name, "model-x")
        self.assertEqual(assessment.prompt_version, "review-v1")

    def test_reviewer_rejects_chair_role_and_invalid_recommendation(self):
        kwargs = dict(
            event_id="event-1", reviewer_role="fact_checker",
            recommendation="support", completion_status="complete",
            dimension_scores=(), dimension_rationales=(), evidence_ids=(),
            confidence=0.5, summary="review",
        )
        with self.assertRaises(ValueError):
            ReviewerAssessment(**{**kwargs, "reviewer_role": "chief_reviewer"})
        with self.assertRaises(ValueError):
            ReviewerAssessment(**{**kwargs, "recommendation": "approve"})

    def test_reviewer_rejects_invalid_score_and_confidence(self):
        kwargs = dict(
            event_id="event-1", reviewer_role="fact_checker",
            recommendation="support", completion_status="complete",
            dimension_scores=(("severity", 1.1),), dimension_rationales=(),
            evidence_ids=(), confidence=0.5, summary="review",
        )
        with self.assertRaises(ValueError):
            ReviewerAssessment(**kwargs)
        with self.assertRaises(TypeError):
            ReviewerAssessment(**{**kwargs, "dimension_scores": (), "confidence": True})

    def test_reviewer_rejects_duplicate_dimension_scores(self):
        kwargs = dict(
            event_id="event-1", reviewer_role="fact_checker",
            recommendation="support", completion_status="complete",
            dimension_scores=(("severity", 0.5), ("severity", 0.6)),
            dimension_rationales=(), evidence_ids=(), confidence=0.5,
            summary="review",
        )
        with self.assertRaises(ValueError):
            ReviewerAssessment(**kwargs)

    def test_reviewer_requires_rationale_for_each_supplied_rationale_entry(self):
        kwargs = dict(
            event_id="event-1", reviewer_role="fact_checker",
            recommendation="support", completion_status="complete",
            dimension_scores=(), dimension_rationales=(("severity", " "),),
            evidence_ids=(), confidence=0.5, summary="review",
        )
        with self.assertRaises(ValueError):
            ReviewerAssessment(**kwargs)

    def test_chief_adjudication_records_dissent_and_followups(self):
        decision = ChiefAdjudication(
            event_id="event-1",
            status="conditional",
            final_dimension_scores=(
                ("severity", 0.75), ("scope", 0.5), ("surprise", None),
            ),
            rationale="事实已确认，但预期基准缺失",
            reviewer_roles_considered=(
                "fact_checker", "industry_chain_analyst",
                "market_expectations_analyst",
            ),
            dissenting_views=("宏观审查员认为影响范围可能更广",),
            unresolved_issues=("缺少事前市场一致预期",),
            required_followups=("补充政策前的市场预期记录",),
            chair_model_name="model-chair",
            prompt_version="chair-v1",
        )

        self.assertEqual(decision.status, "conditional")
        self.assertEqual(decision.final_dimension_scores[-1], ("surprise", None))
        self.assertEqual(len(decision.unresolved_issues), 1)
        self.assertEqual(decision.chair_model_name, "model-chair")

    def test_accepted_decision_cannot_hide_unresolved_issues(self):
        with self.assertRaises(ValueError):
            ChiefAdjudication(
                event_id="event-1",
                status="accepted",
                final_dimension_scores=(),
                rationale="accepted",
                reviewer_roles_considered=("fact_checker",),
                unresolved_issues=("事实尚未核实",),
            )

    def test_chair_rejects_unknown_reviewer_role(self):
        with self.assertRaises(ValueError):
            ChiefAdjudication(
                event_id="event-1",
                status="rejected",
                final_dimension_scores=(),
                rationale="rejected",
                reviewer_roles_considered=("unknown_reviewer",),
            )


if __name__ == "__main__":
    unittest.main()
