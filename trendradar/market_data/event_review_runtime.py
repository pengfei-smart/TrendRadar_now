"""Build a review runtime from TrendRadar's resolved AI configuration.

The caller may pass the AI section (config["AI"]) returned by
trendradar.core.loader.load_config. The convenience loader in this module
resolves AI_API_KEY from the environment using TrendRadar's existing config
logic. Secrets remain in memory and are never copied into review audit records.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from trendradar.core.loader import load_config

from .ai_model_provider import LiteLLMChatProvider
from .event_review_guard import (
    EventReviewBudget,
    EventReviewBudgetProvider,
    GlobalModelCallGuard,
)


@dataclass(frozen=True)
class EventReviewRuntime:
    """Shared provider runtime and global safety controls."""

    provider: LiteLLMChatProvider
    reviewer_model: str
    chief_model: str
    global_guard: GlobalModelCallGuard
    event_budget: EventReviewBudget
    request_guard: Any

    def provider_for_event(self) -> EventReviewBudgetProvider:
        """Create a fresh per-event budget sharing global protections."""
        return EventReviewBudgetProvider(
            provider=self.global_guard,
            budget=self.event_budget,
            request_guard=self.request_guard,
        )

def build_event_review_runtime(
    ai_config: Mapping[str, Any],
    *,
    reviewer_model: str | None = None,
    chief_model: str | None = None,
    request_guard: Any = None,
    max_model_calls_per_event: int = 5,
    max_output_tokens_per_call: int = 3000,
    max_timeout_seconds: float = 60.0,
    max_concurrent_calls: int = 1,
    circuit_failure_threshold: int = 3,
    circuit_cooldown_seconds: float = 60.0,
) -> EventReviewRuntime:
    """Build a guarded Event Review runtime from resolved AI config."""
    if not isinstance(ai_config, Mapping):
        raise TypeError("AI config must be a mapping")

    if request_guard is None or not callable(
        getattr(request_guard, "check", None)
    ):
        raise ValueError(
            "A valid AI request guard is required to build Event Review Runtime"
        )

    resolved_reviewer_model = reviewer_model or ai_config.get("MODEL")
    resolved_chief_model = chief_model or resolved_reviewer_model

    if (
        not isinstance(resolved_reviewer_model, str)
        or not resolved_reviewer_model.strip()
    ):
        raise ValueError("reviewer_model is required")

    if (
        not isinstance(resolved_chief_model, str)
        or not resolved_chief_model.strip()
    ):
        raise ValueError("chief_model is required")

    api_key = ai_config.get("API_KEY")
    if (
        not isinstance(api_key, str)
        or not api_key.strip()
        or api_key.strip() == "AI_API_KEY"
    ):
        raise ValueError("AI API key is missing")

    api_base = ai_config.get("API_BASE") or ""
    if not isinstance(api_base, str):
        raise TypeError("API base must be a string")

    provider = LiteLLMChatProvider(
        api_key=api_key,
        api_base=api_base,
        max_attempts=1,
        request_guard=request_guard,
    )

    global_guard = GlobalModelCallGuard(
        provider,
        max_concurrent_calls=max_concurrent_calls,
        failure_threshold=circuit_failure_threshold,
        cooldown_seconds=circuit_cooldown_seconds,
    )

    event_budget = EventReviewBudget(
        max_model_calls=max_model_calls_per_event,
        max_output_tokens_per_call=max_output_tokens_per_call,
        max_timeout_seconds=max_timeout_seconds,
    )

    return EventReviewRuntime(
        provider=provider,
        reviewer_model=resolved_reviewer_model,
        chief_model=resolved_chief_model,
        global_guard=global_guard,
        event_budget=event_budget,
        request_guard=request_guard,
    )

def load_event_review_runtime(
    config_path: str | None = None,
    **runtime_options: Any,
) -> EventReviewRuntime:
    """Load TrendRadar config and build the Event Review runtime."""
    application_config = load_config(config_path)
    if not isinstance(application_config, Mapping):
        raise TypeError("TrendRadar config must be a mapping")

    ai_config = application_config.get("AI")
    if not isinstance(ai_config, Mapping):
        raise ValueError("TrendRadar config does not contain a valid AI section")

    return build_event_review_runtime(ai_config, **runtime_options)


__all__ = [
    "EventReviewRuntime",
    "build_event_review_runtime",
    "load_event_review_runtime",
]