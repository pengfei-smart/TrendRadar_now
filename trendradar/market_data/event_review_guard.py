"""Per-event model budgets and process-wide AI call protection.

The guard is intentionally in-memory: share one GlobalModelCallGuard across
all event reviews in a process. Multi-process deployments need a shared
coordination backend before claiming a cross-process global limit.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable

from .ai_model_provider import ChatModelError, ChatModelProvider, ChatModelRequest, ChatModelResponse


@dataclass(frozen=True)
class EventReviewBudget:
    max_model_calls: int = 5
    max_output_tokens_per_call: int = 3000
    max_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if isinstance(self.max_model_calls, bool) or self.max_model_calls < 1:
            raise ValueError("max_model_calls must be positive")
        if isinstance(self.max_output_tokens_per_call, bool) or self.max_output_tokens_per_call < 1:
            raise ValueError("max_output_tokens_per_call must be positive")
        if isinstance(self.max_timeout_seconds, bool) or self.max_timeout_seconds <= 0:
            raise ValueError("max_timeout_seconds must be positive")


class GlobalModelCallGuard:
    """Shared process-wide concurrency limiter and consecutive-failure breaker."""

    def __init__(
        self,
        provider: ChatModelProvider,
        *,
        max_concurrent_calls: int = 1,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(max_concurrent_calls, bool) or max_concurrent_calls < 1:
            raise ValueError("max_concurrent_calls must be positive")
        if isinstance(failure_threshold, bool) or failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if isinstance(cooldown_seconds, bool) or cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be positive")
        self._provider = provider
        self._semaphore = threading.BoundedSemaphore(max_concurrent_calls)
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = float(cooldown_seconds)
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._open_until: float | None = None

    def _check_breaker(self) -> None:
        with self._lock:
            now = self._monotonic()
            if self._open_until is not None:
                if now < self._open_until:
                    raise ChatModelError("global AI circuit breaker is open")
                self._open_until = None
                self._consecutive_failures = 0

    def _record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._open_until = None

    def _record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self._open_until = self._monotonic() + self._cooldown_seconds

    def complete(self, request: ChatModelRequest) -> ChatModelResponse:
        self._check_breaker()
        if not self._semaphore.acquire(blocking=False):
            raise ChatModelError("global AI concurrency limit reached")
        try:
            self._check_breaker()
            try:
                result = self._provider.complete(request)
            except Exception as exc:
                self._record_failure()
                if isinstance(exc, ChatModelError):
                    raise
                raise ChatModelError(
                    f"global AI provider failed: {type(exc).__name__}"
                ) from exc
            self._record_success()
            return result
        finally:
            self._semaphore.release()


class EventReviewBudgetProvider:
    """Enforce one event's logical-call, output-token and timeout budgets."""

    def __init__(
        self,
        provider: ChatModelProvider,
        budget: EventReviewBudget | None = None,
    ) -> None:
        self._provider = provider
        self.budget = budget or EventReviewBudget()
        self._calls = 0
        self._lock = threading.Lock()

    @property
    def calls_used(self) -> int:
        with self._lock:
            return self._calls

    def complete(self, request: ChatModelRequest) -> ChatModelResponse:
        if not isinstance(request, ChatModelRequest):
            raise TypeError("request must be a ChatModelRequest")
        if request.max_tokens > self.budget.max_output_tokens_per_call:
            raise ChatModelError("event review output-token budget exceeded")
        if request.timeout_seconds > self.budget.max_timeout_seconds:
            raise ChatModelError("event review timeout budget exceeded")
        with self._lock:
            if self._calls >= self.budget.max_model_calls:
                raise ChatModelError("event review model-call budget exhausted")
            # Failed calls consume budget too; no retry can evade this limit.
            self._calls += 1
        return self._provider.complete(request)


__all__ = [
    "EventReviewBudget",
    "EventReviewBudgetProvider",
    "GlobalModelCallGuard",
]
