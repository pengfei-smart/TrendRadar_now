import json
import unittest
from dataclasses import dataclass

from trendradar.market_data.ai_model_provider import ChatModelError, ChatModelResponse
from trendradar.market_data.event_review_models import ReviewEvidence
from trendradar.market_data.event_reviewer_agent import (
    REVIEWER_PROMPT_VERSION,
    run_reviewer,
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
            content=self.content,
            provider="fake-provider",
            model=request.model,
            request_id="fake-request",
        )


def valid_payload(**overrides):
    payload = {
        "recommendation": "support",
        "dimension_scores": {
            "severity": 0.5,
            "scope": 0.25,
            "duration": None,
            "surprise": None,
        },
        "dimension_rationales": {
            "severity": "已提供证据支持中等影响。",
            "scope": "证据只涉及局部范围。",
            "duration": "缺少持续时间证据。",
            "surprise": "没有可追溯的事前预期基准。",
        },
        "evidence_ids": ["ev-1"],
        "confidence": 0.7,
        "summary": "事件影响有限，仍需观察。",
        "concerns": ["需要补充持续时间信息。"],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def evidence():
    return (
        ReviewEvidence(
            evidence_id="ev-1",
            event_id="event-1",
            source="Official source",
            source_url="https://example.com",
            excerpt="A verified event occurred.",
            evidence_kind="primary",
            supports_dimensions=("severity", "scope"),
        ),
    )


class EventReviewerAgentTests(unittest.TestCase):
    def test_valid_output_becomes_typed_complete_assessment(self):
        provider = FakeProvider(valid_payload())
        result = run_reviewer(
            provider=provider, model="test/model", role="fact_checker",
            event_id="event-1", event_payload={"title": "Test event"},
            evidence=evidence(),
        )
        assessment = result.assessment
        self.assertEqual(result.error_code, "")
        self.assertEqual(assessment.completion_status, "complete")
        self.assertEqual(assessment.reviewer_role, "fact_checker")
        self.assertEqual(assessment.model_provider, "fake-provider")
        self.assertEqual(assessment.model_name, "test/model")
        self.assertEqual(assessment.prompt_version, REVIEWER_PROMPT_VERSION)
        self.assertEqual(assessment.evidence_ids, ("ev-1",))

    def test_each_role_receives_distinct_role_specific_system_prompt(self):
        prompts = []
        for role in (
            "fact_checker", "macro_policy_analyst",
            "industry_chain_analyst", "market_expectations_analyst",
        ):
            provider = FakeProvider(valid_payload())
            run_reviewer(
                provider=provider, model="test/model", role=role,
                event_id="event-1", event_payload={}, evidence=evidence(),
            )
            prompts.append(provider.requests[0].system_prompt)
        self.assertEqual(len(set(prompts)), 4)
        self.assertIn("事实核查员", prompts[0])
        self.assertIn("宏观与政策分析员", prompts[1])
        self.assertIn("产业链分析员", prompts[2])
        self.assertIn("市场预期分析员", prompts[3])

    def test_request_contains_event_and_evidence_without_losing_ids(self):
        provider = FakeProvider(valid_payload())
        run_reviewer(
            provider=provider, model="test/model", role="fact_checker",
            event_id="event-1", event_payload={"title": "Important"},
            evidence=evidence(),
        )
        user_payload = json.loads(provider.requests[0].user_prompt)
        self.assertEqual(user_payload["event"]["title"], "Important")
        self.assertEqual(user_payload["evidence"][0]["evidence_id"], "ev-1")

    def test_chief_role_is_not_accepted_as_independent_reviewer(self):
        with self.assertRaises(ValueError):
            run_reviewer(
                provider=FakeProvider(valid_payload()), model="test/model",
                role="chief_reviewer", event_id="event-1",
                event_payload={}, evidence=evidence(),
            )

    def test_model_transport_failure_returns_error_assessment(self):
        result = run_reviewer(
            provider=FakeProvider(error=ChatModelError("provider unavailable")),
            model="test/model", role="fact_checker",
            event_id="event-1", event_payload={}, evidence=evidence(),
        )
        self.assertEqual(result.error_code, "model_error")
        self.assertEqual(result.assessment.completion_status, "error")
        self.assertEqual(result.assessment.recommendation, "abstain")
        self.assertTrue(all(
            score is None for _, score in result.assessment.dimension_scores
        ))

    def test_invalid_json_returns_error_assessment(self):
        result = run_reviewer(
            provider=FakeProvider("not json"), model="test/model",
            role="fact_checker", event_id="event-1",
            event_payload={}, evidence=evidence(),
        )
        self.assertEqual(result.error_code, "invalid_model_output")
        self.assertEqual(result.assessment.completion_status, "error")

    def test_missing_or_extra_fields_are_rejected(self):
        payload = json.loads(valid_payload())
        del payload["concerns"]
        for candidate in (payload, {**json.loads(valid_payload()), "extra": True}):
            with self.subTest(candidate=candidate):
                result = run_reviewer(
                    provider=FakeProvider(json.dumps(candidate)),
                    model="test/model", role="fact_checker",
                    event_id="event-1", event_payload={}, evidence=evidence(),
                )
                self.assertEqual(result.error_code, "invalid_model_output")

    def test_invalid_score_and_missing_rationale_are_rejected(self):
        payload = json.loads(valid_payload())
        payload["dimension_scores"]["severity"] = 1.5
        result = run_reviewer(
            provider=FakeProvider(json.dumps(payload)), model="test/model",
            role="fact_checker", event_id="event-1",
            event_payload={}, evidence=evidence(),
        )
        self.assertEqual(result.assessment.completion_status, "error")

        payload = json.loads(valid_payload())
        payload["dimension_rationales"]["scope"] = " "
        result = run_reviewer(
            provider=FakeProvider(json.dumps(payload)), model="test/model",
            role="fact_checker", event_id="event-1",
            event_payload={}, evidence=evidence(),
        )
        self.assertEqual(result.assessment.completion_status, "error")

    def test_unknown_evidence_id_is_rejected(self):
        payload = json.loads(valid_payload())
        payload["evidence_ids"] = ["not-in-input"]
        result = run_reviewer(
            provider=FakeProvider(json.dumps(payload)), model="test/model",
            role="fact_checker", event_id="event-1",
            event_payload={}, evidence=evidence(),
        )
        self.assertEqual(result.error_code, "invalid_model_output")

    def test_surprise_score_requires_cited_pre_event_expectation_evidence(self):
        payload = json.loads(valid_payload())
        payload["dimension_scores"]["surprise"] = 0.75
        payload["dimension_rationales"]["surprise"] = "结果高于预期。"
        result = run_reviewer(
            provider=FakeProvider(json.dumps(payload)), model="test/model",
            role="market_expectations_analyst", event_id="event-1",
            event_payload={}, evidence=evidence(),
        )
        self.assertEqual(result.assessment.completion_status, "error")
        self.assertIn("pre-event expectation", result.error_message)

    def test_surprise_score_allowed_with_cited_expectation_baseline(self):
        baseline = ReviewEvidence(
            evidence_id="baseline-1", event_id="event-1",
            source="Pre-event consensus", source_url="https://example.com/consensus",
            excerpt="Consensus estimate published before the event.",
            evidence_kind="secondary", published_at="2026-11-17T08:00:00+08:00",
            supports_dimensions=("surprise",),
        )
        payload = json.loads(valid_payload())
        payload["evidence_ids"] = ["ev-1", "baseline-1"]
        payload["dimension_scores"]["surprise"] = 0.75
        payload["dimension_rationales"]["surprise"] = "相对事前一致预期存在偏差。"
        result = run_reviewer(
            provider=FakeProvider(json.dumps(payload)), model="test/model",
            role="market_expectations_analyst", event_id="event-1",
            event_payload={"event_time": "2026-11-17T09:00:00+08:00"}, evidence=evidence() + (baseline,),
        )
        self.assertEqual(result.assessment.completion_status, "complete")
        self.assertEqual(dict(result.assessment.dimension_scores)["surprise"], 0.75)

    def test_rejects_evidence_from_another_event(self):
        wrong = ReviewEvidence(
            evidence_id="ev-other", event_id="other-event", source="Source",
            source_url="", excerpt="Excerpt", evidence_kind="context",
        )
        with self.assertRaises(ValueError):
            run_reviewer(
                provider=FakeProvider(valid_payload()), model="test/model",
                role="fact_checker", event_id="event-1",
                event_payload={}, evidence=(wrong,),
            )

    def test_rejects_invalid_event_payload_type(self):
        with self.assertRaises(TypeError):
            run_reviewer(
                provider=FakeProvider(valid_payload()), model="test/model",
                role="fact_checker", event_id="event-1",
                event_payload=[], evidence=evidence(),
            )


    def test_surprise_score_rejects_baseline_published_after_event(self):
        baseline = ReviewEvidence(
            evidence_id="baseline-1", event_id="event-1",
            source="Late report", source_url="https://example.com/late",
            excerpt="Published after the event.", evidence_kind="secondary",
            published_at="2026-11-17T10:00:00+08:00",
            supports_dimensions=("surprise",),
        )
        payload = json.loads(valid_payload())
        payload["evidence_ids"] = ["ev-1", "baseline-1"]
        payload["dimension_scores"]["surprise"] = 0.75
        result = run_reviewer(
            provider=FakeProvider(json.dumps(payload)), model="test/model",
            role="market_expectations_analyst", event_id="event-1",
            event_payload={"event_time": "2026-11-17T09:00:00+08:00"},
            evidence=evidence() + (baseline,),
        )
        self.assertEqual(result.assessment.completion_status, "error")

    def test_surprise_score_rejects_missing_event_time(self):
        baseline = ReviewEvidence(
            evidence_id="baseline-1", event_id="event-1",
            source="Consensus", source_url="https://example.com/consensus",
            excerpt="Consensus.", evidence_kind="secondary",
            published_at="2026-11-17T08:00:00+08:00",
            supports_dimensions=("surprise",),
        )
        payload = json.loads(valid_payload())
        payload["evidence_ids"] = ["ev-1", "baseline-1"]
        payload["dimension_scores"]["surprise"] = 0.75
        result = run_reviewer(
            provider=FakeProvider(json.dumps(payload)), model="test/model",
            role="market_expectations_analyst", event_id="event-1",
            event_payload={}, evidence=evidence() + (baseline,),
        )
        self.assertEqual(result.assessment.completion_status, "error")


if __name__ == "__main__":
    unittest.main()
