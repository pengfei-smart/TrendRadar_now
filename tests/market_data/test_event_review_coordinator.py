import unittest

from trendradar.market_data.event_review_coordinator import (
    assemble_event_review_board,
)
from trendradar.market_data.event_review_models import (
    REVIEWER_ROLES,
    ChiefAdjudication,
    ReviewEvidence,
    ReviewerAssessment,
)


def make_evidence(evidence_id="ev-1"):
    return ReviewEvidence(
        evidence_id=evidence_id,
        event_id="event-1",
        source="official_source",
        source_url="https://example.invalid/source",
        excerpt="可核验的原始材料摘录",
        evidence_kind="primary",
        supports_dimensions=("severity", "scope"),
    )


def make_assessments(roles=REVIEWER_ROLES):
    return tuple(
        ReviewerAssessment(
            event_id="event-1",
            reviewer_role=role,
            recommendation="support",
            completion_status="complete",
            dimension_scores=(("severity", 0.5),),
            dimension_rationales=(("severity", "基于原始证据进行判断"),),
            evidence_ids=("ev-1",),
            confidence=0.7,
            summary=f"{role}独立评估意见",
            model_provider="test-provider",
            model_name="test-model",
            model_version="v1",
            prompt_version="review-v1",
        )
        for role in roles
    )


def make_adjudication(roles=REVIEWER_ROLES, status="conditional"):
    return ChiefAdjudication(
        event_id="event-1",
        status=status,
        final_dimension_scores=(("severity", 0.5), ("surprise", None)),
        rationale="综合四位审查员意见，超预期证据仍不足",
        reviewer_roles_considered=tuple(roles),
        unresolved_issues=("缺少事前市场预期基准",),
        required_followups=("补充事前一致预期记录",),
        chair_model_provider="test-provider",
        chair_model_name="chair-model",
        chair_model_version="v1",
        prompt_version="chair-v1",
    )


class EventReviewCoordinatorTests(unittest.TestCase):
    def test_partial_board_is_incomplete_and_never_implicitly_approved(self):
        result = assemble_event_review_board(
            event_id="event-1",
            evidence=(make_evidence(),),
            reviewer_assessments=make_assessments(REVIEWER_ROLES[:2]),
        )

        self.assertEqual(result.status, "incomplete")
        self.assertEqual(
            result.missing_reviewer_roles,
            REVIEWER_ROLES[2:],
        )
        self.assertIsNone(result.adjudication)
        self.assertTrue(result.validation_warnings)

    def test_all_four_reviews_make_board_ready_for_chair(self):
        result = assemble_event_review_board(
            event_id="event-1",
            evidence=(make_evidence(),),
            reviewer_assessments=make_assessments(),
        )

        self.assertEqual(result.status, "ready_for_adjudication")
        self.assertEqual(result.missing_reviewer_roles, ())
        self.assertIsNone(result.adjudication)

    def test_complete_board_with_chair_decision_is_adjudicated(self):
        decision = make_adjudication()
        result = assemble_event_review_board(
            event_id="event-1",
            evidence=(make_evidence(),),
            reviewer_assessments=make_assessments(),
            adjudication=decision,
        )

        self.assertEqual(result.status, "adjudicated")
        self.assertIs(result.adjudication, decision)

    def test_failed_or_insufficient_review_keeps_board_incomplete(self):
        assessments = list(make_assessments())
        first = assessments[0]
        assessments[0] = ReviewerAssessment(
            event_id=first.event_id,
            reviewer_role=first.reviewer_role,
            recommendation="abstain",
            completion_status="insufficient_evidence",
            dimension_scores=(),
            dimension_rationales=(),
            evidence_ids=(),
            confidence=0.0,
            summary="证据不足，无法完成评估",
        )

        result = assemble_event_review_board(
            event_id="event-1",
            evidence=(make_evidence(),),
            reviewer_assessments=tuple(assessments),
        )
        self.assertEqual(result.status, "incomplete")
        self.assertTrue(any("审查未完成" in item for item in result.validation_warnings))

    def test_chair_cannot_adjudicate_before_all_reviews_complete(self):
        with self.assertRaises(ValueError):
            assemble_event_review_board(
                event_id="event-1",
                evidence=(make_evidence(),),
                reviewer_assessments=make_assessments(REVIEWER_ROLES[:3]),
                adjudication=make_adjudication(),
            )

    def test_duplicate_reviewer_role_is_rejected(self):
        assessments = make_assessments(REVIEWER_ROLES[:1])
        with self.assertRaises(ValueError):
            assemble_event_review_board(
                event_id="event-1",
                evidence=(make_evidence(),),
                reviewer_assessments=assessments + assessments,
            )

    def test_unknown_evidence_reference_is_rejected(self):
        assessments = list(make_assessments())
        first = assessments[0]
        assessments[0] = ReviewerAssessment(
            event_id=first.event_id,
            reviewer_role=first.reviewer_role,
            recommendation=first.recommendation,
            completion_status=first.completion_status,
            dimension_scores=first.dimension_scores,
            dimension_rationales=first.dimension_rationales,
            evidence_ids=("missing-evidence",),
            confidence=first.confidence,
            summary=first.summary,
        )
        with self.assertRaises(ValueError):
            assemble_event_review_board(
                event_id="event-1",
                evidence=(make_evidence(),),
                reviewer_assessments=tuple(assessments),
            )

    def test_duplicate_evidence_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            assemble_event_review_board(
                event_id="event-1",
                evidence=(make_evidence(), make_evidence()),
                reviewer_assessments=(),
            )

    def test_mismatched_event_ids_are_rejected(self):
        other = ReviewEvidence(
            evidence_id="ev-other",
            event_id="event-other",
            source="source",
            source_url="",
            excerpt="excerpt",
            evidence_kind="secondary",
        )
        with self.assertRaises(ValueError):
            assemble_event_review_board(
                event_id="event-1",
                evidence=(other,),
                reviewer_assessments=(),
            )

    def test_chair_must_consider_all_four_reviewers(self):
        with self.assertRaises(ValueError):
            assemble_event_review_board(
                event_id="event-1",
                evidence=(make_evidence(),),
                reviewer_assessments=make_assessments(),
                adjudication=make_adjudication(REVIEWER_ROLES[:3]),
            )

    def test_wrong_chair_event_id_is_rejected(self):
        decision = ChiefAdjudication(
            event_id="another-event",
            status="rejected",
            final_dimension_scores=(),
            rationale="另一事件的裁决",
            reviewer_roles_considered=REVIEWER_ROLES,
        )
        with self.assertRaises(ValueError):
            assemble_event_review_board(
                event_id="event-1",
                evidence=(make_evidence(),),
                reviewer_assessments=make_assessments(),
                adjudication=decision,
            )

    def test_rejects_wrong_container_types(self):
        with self.assertRaises(TypeError):
            assemble_event_review_board(
                event_id="event-1",
                evidence=[make_evidence()],
                reviewer_assessments=make_assessments(),
            )


if __name__ == "__main__":
    unittest.main()
