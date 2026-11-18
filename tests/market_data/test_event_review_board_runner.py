import json
import unittest

from trendradar.market_data.ai_model_provider import ChatModelError, ChatModelResponse
from trendradar.market_data.event_review_models import (
    IMPACT_DIMENSIONS, REVIEWER_ROLES, ReviewEvidence,
)
from trendradar.market_data.event_review_board_runner import run_event_review_board


def reviewer_output():
    return json.dumps({
        "recommendation": "support",
        "dimension_scores": {
            "severity": 0.5, "scope": 0.5,
            "duration": None, "surprise": None,
        },
        "dimension_rationales": {dimension: "依据当前证据作出的有限判断。"
                                 for dimension in IMPACT_DIMENSIONS},
        "evidence_ids": ["ev-1"],
        "confidence": 0.7,
        "summary": "审查完成。",
        "concerns": [],
    }, ensure_ascii=False)


def chair_output(status="conditional"):
    return json.dumps({
        "status": status,
        "final_dimension_scores": {
            "severity": 0.5, "scope": 0.5,
            "duration": None, "surprise": None,
        },
        "rationale": "事实有一定支持，但仍需补充持续时间证据。",
        "reviewer_roles_considered": list(REVIEWER_ROLES),
        "dissenting_views": [],
        "unresolved_issues": ["持续时间尚未确认。"],
        "required_followups": ["补充持续时间证据。"],
    }, ensure_ascii=False)


class RoutingProvider:
    provider_name = "routing-fake"

    def __init__(self, fail_role=None, fail_chair=False):
        self.fail_role = fail_role
        self.fail_chair = fail_chair
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        prompt = request.system_prompt
        role_markers = {
            "fact_checker": "你是事实核查员",
            "macro_policy_analyst": "你是宏观与政策分析员",
            "industry_chain_analyst": "你是产业链分析员",
            "market_expectations_analyst": "你是市场预期分析员",
        }
        for role, marker in role_markers.items():
            if marker in prompt:
                if role == self.fail_role:
                    raise ChatModelError("simulated reviewer failure")
                content = reviewer_output()
                break
        else:
            if self.fail_chair:
                raise ChatModelError("simulated chair failure")
            content = chair_output()
        return ChatModelResponse(
            content=content, provider="fake", model=request.model,
            request_id=f"request-{len(self.requests)}",
        )


class EventReviewBoardRunnerTests(unittest.TestCase):
    def setUp(self):
        self.evidence = (
            ReviewEvidence(
                evidence_id="ev-1", event_id="event-1",
                source="Official source", source_url="https://example.com",
                excerpt="A verifiable event excerpt.", evidence_kind="primary",
                supports_dimensions=("severity", "scope"),
            ),
        )

    def run_board(self, provider):
        return run_event_review_board(
            provider=provider,
            reviewer_model="test/reviewer",
            chief_model="test/chair",
            event_id="event-1",
            event_payload={"title": "Test event"},
            evidence=self.evidence,
        )

    def test_runs_four_reviewers_then_chair_and_returns_adjudicated_board(self):
        provider = RoutingProvider()
        result = self.run_board(provider)
        self.assertEqual(result.status, "adjudicated")
        self.assertEqual(result.board.status, "adjudicated")
        self.assertEqual(len(result.reviewer_results), 4)
        self.assertEqual(len(provider.requests), 5)
        self.assertEqual(
            tuple(item.assessment.reviewer_role for item in result.reviewer_results),
            REVIEWER_ROLES,
        )
        self.assertEqual(result.chief_result.adjudication.status, "conditional")

    def test_reviewer_failure_blocks_chair_and_board_remains_incomplete(self):
        provider = RoutingProvider(fail_role="industry_chain_analyst")
        result = self.run_board(provider)
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.board.status, "incomplete")
        self.assertIsNone(result.chief_result)
        self.assertEqual(len(result.reviewer_results), 4)
        self.assertEqual(len(provider.requests), 4)
        self.assertIn(
            "industry_chain_analyst",
            result.board.validation_warnings[0],
        )

    def test_chair_failure_preserves_complete_board_without_adjudication(self):
        provider = RoutingProvider(fail_chair=True)
        result = self.run_board(provider)
        self.assertEqual(result.status, "chief_error")
        self.assertEqual(result.board.status, "ready_for_adjudication")
        self.assertIsNone(result.board.adjudication)
        self.assertEqual(result.chief_result.error_code, "model_error")
        self.assertEqual(len(provider.requests), 5)

    def test_uses_separate_reviewer_and_chair_models(self):
        provider = RoutingProvider()
        run_event_review_board(
            provider=provider,
            reviewer_model="provider/reviewer-model",
            chief_model="provider/chair-model",
            event_id="event-1",
            event_payload={},
            evidence=self.evidence,
        )
        self.assertTrue(all(
            request.model == "provider/reviewer-model"
            for request in provider.requests[:4]
        ))
        self.assertEqual(provider.requests[4].model, "provider/chair-model")

    def test_invalid_models_are_rejected_before_provider_calls(self):
        provider = RoutingProvider()
        with self.assertRaises(ValueError):
            run_event_review_board(
                provider=provider, reviewer_model=" ", chief_model="chair",
                event_id="event-1", event_payload={},
                evidence=self.evidence,
            )
        self.assertEqual(provider.requests, [])


if __name__ == "__main__":
    unittest.main()
