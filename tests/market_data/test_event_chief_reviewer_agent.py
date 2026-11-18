import json
import unittest

from trendradar.market_data.ai_model_provider import ChatModelError, ChatModelResponse
from trendradar.market_data.event_review_models import (
    IMPACT_DIMENSIONS, REVIEWER_ROLES, ReviewEvidence, ReviewerAssessment,
)
from trendradar.market_data.event_chief_reviewer_agent import (
    CHIEF_REVIEWER_PROMPT_VERSION,
    run_chief_reviewer,
)


class FakeProvider:
    provider_name = "fake"

    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return ChatModelResponse(
            content=self.content, provider="fake-provider",
            model=request.model, request_id="chair-request",
        )


def four_reviews():
    return tuple(
        ReviewerAssessment(
            event_id="event-1",
            reviewer_role=role,
            recommendation="support",
            completion_status="complete",
            dimension_scores=tuple((dimension, 0.5) for dimension in IMPACT_DIMENSIONS),
            dimension_rationales=tuple((dimension, "有证据支持的初步判断。")
                                       for dimension in IMPACT_DIMENSIONS),
            evidence_ids=(),
            confidence=0.7,
            summary=f"{role}审查完成。",
        )
        for role in REVIEWER_ROLES
    )


def chief_payload(**overrides):
    payload = {
        "status": "conditional",
        "final_dimension_scores": {
            "severity": 0.5, "scope": 0.25,
            "duration": None, "surprise": None,
        },
        "rationale": "四位审查员的主要事实判断一致，但持续时间仍缺少证据。",
        "reviewer_roles_considered": list(REVIEWER_ROLES),
        "dissenting_views": [],
        "unresolved_issues": ["事件影响持续时间尚未确认。"],
        "required_followups": ["补充项目周期或供需持续性证据。"],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class EventChiefReviewerAgentTests(unittest.TestCase):
    def test_complete_board_is_sent_to_chief_and_typed_result_returned(self):
        provider = FakeProvider(chief_payload())
        result = run_chief_reviewer(
            provider=provider, model="test/chair",
            event_id="event-1", event_payload={"title": "Test event"},
            evidence=(), reviewer_assessments=four_reviews(),
        )
        self.assertEqual(result.error_code, "")
        self.assertEqual(result.adjudication.status, "conditional")
        self.assertEqual(result.adjudication.chair_model_provider, "fake-provider")
        self.assertEqual(result.adjudication.chair_model_name, "test/chair")
        self.assertEqual(result.adjudication.prompt_version, CHIEF_REVIEWER_PROMPT_VERSION)
        self.assertEqual(set(result.adjudication.reviewer_roles_considered), set(REVIEWER_ROLES))

    def test_prompt_requires_counterevidence_and_rejects_majority_vote(self):
        provider = FakeProvider(chief_payload())
        run_chief_reviewer(
            provider=provider, model="test/chair",
            event_id="event-1", event_payload={}, evidence=(),
            reviewer_assessments=four_reviews(),
        )
        prompt = provider.requests[0].system_prompt
        self.assertIn("主动寻找反证", prompt)
        self.assertIn("不能简单平均四位审查员的分数", prompt)
        self.assertIn("不能因为多数审查员意见一致就自动接受", prompt)

    def test_user_prompt_contains_all_four_reviews_and_evidence(self):
        evidence = (
            ReviewEvidence(
                evidence_id="ev-1", event_id="event-1", source="Source",
                source_url="https://example.com", excerpt="Event evidence",
                evidence_kind="primary",
            ),
        )
        reviews = tuple(
            ReviewerAssessment(
                event_id="event-1", reviewer_role=role,
                recommendation="support", completion_status="complete",
                dimension_scores=tuple((d, 0.4) for d in IMPACT_DIMENSIONS),
                dimension_rationales=tuple((d, "理由") for d in IMPACT_DIMENSIONS),
                evidence_ids=("ev-1",), confidence=0.6, summary="review",
            ) for role in REVIEWER_ROLES
        )
        provider = FakeProvider(chief_payload())
        run_chief_reviewer(
            provider=provider, model="test/chair",
            event_id="event-1", event_payload={"title": "Event"},
            evidence=evidence, reviewer_assessments=reviews,
        )
        request_data = json.loads(provider.requests[0].user_prompt)
        self.assertEqual(len(request_data["independent_reviews"]), 4)
        self.assertEqual(request_data["evidence"][0]["evidence_id"], "ev-1")

    def test_incomplete_board_does_not_call_model(self):
        provider = FakeProvider(chief_payload())
        result = run_chief_reviewer(
            provider=provider, model="test/chair",
            event_id="event-1", event_payload={}, evidence=(),
            reviewer_assessments=four_reviews()[:3],
        )
        self.assertEqual(result.error_code, "incomplete_review_board")
        self.assertIsNone(result.adjudication)
        self.assertEqual(provider.requests, [])

    def test_failed_reviewer_blocks_chair(self):
        reviews = list(four_reviews())
        failed = reviews[0]
        reviews[0] = ReviewerAssessment(
            event_id=failed.event_id, reviewer_role=failed.reviewer_role,
            recommendation="abstain", completion_status="error",
            dimension_scores=tuple((d, None) for d in IMPACT_DIMENSIONS),
            dimension_rationales=tuple((d, "审查失败") for d in IMPACT_DIMENSIONS),
            evidence_ids=(), confidence=0, summary="failed",
        )
        provider = FakeProvider(chief_payload())
        result = run_chief_reviewer(
            provider=provider, model="test/chair",
            event_id="event-1", event_payload={}, evidence=(),
            reviewer_assessments=tuple(reviews),
        )
        self.assertEqual(result.error_code, "incomplete_review_board")
        self.assertEqual(provider.requests, [])

    def test_invalid_json_returns_failure_without_adjudication(self):
        result = run_chief_reviewer(
            provider=FakeProvider("not json"), model="test/chair",
            event_id="event-1", event_payload={}, evidence=(),
            reviewer_assessments=four_reviews(),
        )
        self.assertEqual(result.error_code, "invalid_model_output")
        self.assertIsNone(result.adjudication)

    def test_missing_or_extra_fields_are_rejected(self):
        original = json.loads(chief_payload())
        missing = dict(original)
        del missing["required_followups"]
        extra = {**original, "extra": "not allowed"}
        for payload in (missing, extra):
            result = run_chief_reviewer(
                provider=FakeProvider(json.dumps(payload)), model="test/chair",
                event_id="event-1", event_payload={}, evidence=(),
                reviewer_assessments=four_reviews(),
            )
            self.assertEqual(result.error_code, "invalid_model_output")

    def test_all_four_roles_must_be_considered_exactly_once(self):
        roles = list(REVIEWER_ROLES[:-1])
        payload = chief_payload(reviewer_roles_considered=roles)
        result = run_chief_reviewer(
            provider=FakeProvider(payload), model="test/chair",
            event_id="event-1", event_payload={}, evidence=(),
            reviewer_assessments=four_reviews(),
        )
        self.assertEqual(result.error_code, "invalid_model_output")

    def test_score_out_of_range_is_rejected(self):
        scores = {
            "severity": 1.1, "scope": 0.2,
            "duration": None, "surprise": None,
        }
        result = run_chief_reviewer(
            provider=FakeProvider(chief_payload(final_dimension_scores=scores)),
            model="test/chair", event_id="event-1",
            event_payload={}, evidence=(), reviewer_assessments=four_reviews(),
        )
        self.assertEqual(result.error_code, "invalid_model_output")

    def test_accepted_with_unresolved_issues_is_rejected(self):
        payload = chief_payload(
            status="accepted",
            unresolved_issues=["关键事实仍然不清楚。"],
        )
        result = run_chief_reviewer(
            provider=FakeProvider(payload), model="test/chair",
            event_id="event-1", event_payload={}, evidence=(),
            reviewer_assessments=four_reviews(),
        )
        self.assertEqual(result.error_code, "invalid_model_output")
        self.assertIsNone(result.adjudication)

    def test_transport_failure_returns_error(self):
        result = run_chief_reviewer(
            provider=FakeProvider(error=ChatModelError("provider unavailable")),
            model="test/chair", event_id="event-1",
            event_payload={}, evidence=(), reviewer_assessments=four_reviews(),
        )
        self.assertEqual(result.error_code, "model_error")
        self.assertIsNone(result.adjudication)

    def test_rejects_evidence_or_reviews_from_another_event(self):
        wrong = ReviewEvidence(
            evidence_id="ev-x", event_id="other", source="Source",
            source_url="", excerpt="Evidence", evidence_kind="context",
        )
        with self.assertRaises(ValueError):
            run_chief_reviewer(
                provider=FakeProvider(chief_payload()), model="test/chair",
                event_id="event-1", event_payload={}, evidence=(wrong,),
                reviewer_assessments=four_reviews(),
            )

    def test_invalid_event_payload_type_is_rejected(self):
        with self.assertRaises(TypeError):
            run_chief_reviewer(
                provider=FakeProvider(chief_payload()), model="test/chair",
                event_id="event-1", event_payload=[],
                evidence=(), reviewer_assessments=four_reviews(),
            )


if __name__ == "__main__":
    unittest.main()
