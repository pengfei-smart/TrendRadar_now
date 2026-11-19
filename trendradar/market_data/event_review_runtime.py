"""Build a review runtime from TrendRadar's resolved AI configuration.

The caller may pass the AI section (config["AI"]) returned by
trendradar.core.loader.load_config. The convenience loader in this module
resolves AI_API_KEY from the environment using TrendRadar's existing config
logic. Secrets remain in memory and are never copied into review audit records.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .ai_model_provider import LiteLLMChatProvider
from .event_review_guard import (
    EventReviewBudget,
    EventReviewBudgetProvider,
    GlobalModelCallGuard,
)


@dataclass(frozen=True)
class EventReviewRuntime:
    provider: LiteLLMChatProvider
    reviewer_model: str
    chief_model: str
    global_guard: GlobalModelCallGuard
    event_budget: EventReviewBudget

    def provider_for_event(self) -> EventReviewBudgetProvider:
        """Create a fresh per-event budget sharing this runtime's global guard."""
        return EventReviewBudgetProvider(self.global_guard, self.event_budget)


def build_event_review_runtime(
    ai_config: Mapping[str, Any],
    *,
    reviewer_model: str | None = None,
    chief_model: str | None = None,
    max_attempts: int = 1,
    max_model_calls_per_event: int = 5,
    max_output_tokens_per_call: int = 3000,
    max_timeout_seconds: float = 60.0,
    max_concurrent_calls: int = 1,
    circuit_failure_threshold: int = 5,
    circuit_cooldown_seconds: float = 60.0,
) -> EventReviewRuntime:
    """Create a guarded runtime without making API calls.

    Runtime defaults to one transport attempt per role to keep the event call
    ceiling predictable; retries can be explicitly enabled by the caller.
    """
    if not isinstance(ai_config, Mapping):
        raise TypeError("ai_config must be a mapping")

    configured_model = ai_config.get("MODEL", ai_config.get("model", ""))
    selected_reviewer = reviewer_model or configured_model
    selected_chief = chief_model or configured_model
    for field, value in (
        ("reviewer_model", selected_reviewer),
        ("chief_model", selected_chief),
    ):
        if not isinstance(value, str) or not value.strip() or "/" not in value:
            raise ValueError(f"{field} must use provider/model format")

    api_key = ai_config.get("API_KEY", ai_config.get("api_key", ""))
    # The repository's sample config uses this literal as an env-var hint.
    if api_key == "AI_API_KEY":
        api_key = ""
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError(
            "AI API key is missing; set the configured API-key environment "
            "variable or configure ai.api_key securely"
        )

    api_base = ai_config.get("API_BASE", ai_config.get("api_base", "")) or ""
    if not isinstance(api_base, str):
        raise TypeError("AI API base must be a string")

    provider = LiteLLMChatProvider(
        api_key=api_key,
        api_base=api_base,
        max_attempts=max_attempts,
        usage_ledger_enabled=True,
    )
    budget = EventReviewBudget(
        max_model_calls=max_model_calls_per_event,
        max_output_tokens_per_call=max_output_tokens_per_call,
        max_timeout_seconds=max_timeout_seconds,
    )
    global_guard = GlobalModelCallGuard(
        provider,
        max_concurrent_calls=max_concurrent_calls,
        failure_threshold=circuit_failure_threshold,
        cooldown_seconds=circuit_cooldown_seconds,
    )
    return EventReviewRuntime(
        provider=provider,
        reviewer_model=selected_reviewer.strip(),
        chief_model=selected_chief.strip(),
        global_guard=global_guard,
        event_budget=budget,
    )


def load_event_review_runtime(
    config_path: str | None = None,
    **runtime_options: Any,
) -> EventReviewRuntime:
    """Load TrendRadar's normal application config and build a guarded runtime.

    This reuses load_config(), including CONFIG_PATH and AI_* environment
    overrides, so callers do not need to read or pass the API key themselves.
    """
    from trendradar.core.loader import load_config

    application_config = load_config(config_path)
    ai_config = application_config.get("AI")
    if not isinstance(ai_config, Mapping):
        raise ValueError("TrendRadar AI configuration is missing or invalid")
    return build_event_review_runtime(ai_config, **runtime_options)


__all__ = [
    "EventReviewRuntime",
    "build_event_review_runtime",
    "load_event_review_runtime",
]
