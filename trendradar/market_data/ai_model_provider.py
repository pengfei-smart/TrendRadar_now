"""Provider-neutral chat model adapter with strict JSON response handling.

LiteLLM is imported lazily. Credentials and API base may be supplied by a
trusted runtime configuration and are retained only in memory; they are never
included in logs or safe error messages. No automatic approval is performed.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class ChatModelRequest:
    model: str
    system_prompt: str
    user_prompt: str
    timeout_seconds: float = 45.0
    temperature: float = 0.0
    max_tokens: int = 2500
    response_format: str = "json_object"

    def __post_init__(self):
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(self.system_prompt, str) or not self.system_prompt.strip():
            raise ValueError("system_prompt must be a non-empty string")
        if not isinstance(self.user_prompt, str) or not self.user_prompt.strip():
            raise ValueError("user_prompt must be a non-empty string")
        if isinstance(self.timeout_seconds, bool) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if isinstance(self.temperature, bool) or not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        if isinstance(self.max_tokens, bool) or self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if self.response_format != "json_object":
            raise ValueError("only json_object response format is supported")


@dataclass(frozen=True)
class ChatModelResponse:
    content: str
    provider: str
    model: str
    finish_reason: str = ""
    request_id: str = ""
    attempts: int = 1
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ChatModelError(RuntimeError):
    """Safe model-call failure; message must not contain credentials or prompts."""


class ChatModelProvider(Protocol):
    def complete(self, request: ChatModelRequest) -> ChatModelResponse:
        """Return one validated transport response or raise ChatModelError."""


def _retryable_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    return status == 429 or (
        isinstance(status, int) and 500 <= status <= 599
    )


def _usage_count(usage: Any, name: str) -> int | None:
    """Read a non-negative integer token count; missing/invalid values stay unknown."""
    if usage is None:
        return None
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _extract_content(response: Any) -> tuple[str, str, str, int | None, int | None, int | None]:
    """Extract text, request metadata and optional token usage from LiteLLM-like responses."""
    try:
        choice = response.choices[0]
        message = choice.message
        content = message.content
        finish_reason = getattr(choice, "finish_reason", "") or ""
        request_id = getattr(response, "id", "") or ""
        usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
        prompt_tokens = _usage_count(usage, "prompt_tokens")
        completion_tokens = _usage_count(usage, "completion_tokens")
        total_tokens = _usage_count(usage, "total_tokens")
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise ChatModelError("model provider returned an invalid response shape") from exc

    if not isinstance(content, str) or not content.strip():
        raise ChatModelError("model provider returned empty or non-text content")
    return (
        content.strip(), str(finish_reason), str(request_id),
        prompt_tokens, completion_tokens, total_tokens,
    )


class LiteLLMChatProvider:
    """Chat provider backed by LiteLLM with bounded retry and no secret logging."""

    def __init__(
        self,
        *,
        provider_name: str = "litellm",
        max_attempts: int = 2,
        retry_delay_seconds: float = 0.5,
        completion_callable: Callable[..., Any] | None = None,
        sleep_callable: Callable[[float], None] = time.sleep,
        api_key: str = "",
        api_base: str = "",
    ):
        if not isinstance(provider_name, str) or not provider_name.strip():
            raise ValueError("provider_name must be a non-empty string")
        if isinstance(max_attempts, bool) or not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if isinstance(retry_delay_seconds, bool) or retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must be non-negative")
        self.provider_name = provider_name.strip()
        self.max_attempts = max_attempts
        self.retry_delay_seconds = float(retry_delay_seconds)
        if not isinstance(api_key, str):
            raise TypeError("api_key must be a string")
        if not isinstance(api_base, str):
            raise TypeError("api_base must be a string")
        self._completion_callable = completion_callable
        self._sleep = sleep_callable
        # Secrets remain only in memory and are never included in errors.
        self._api_key = api_key.strip()
        self._api_base = api_base.strip()

    def _completion(self) -> Callable[..., Any]:
        if self._completion_callable is not None:
            return self._completion_callable
        try:
            from litellm import completion
        except ImportError as exc:
            raise ChatModelError("LiteLLM is not installed") from exc
        return completion

    def complete(self, request: ChatModelRequest) -> ChatModelResponse:
        if not isinstance(request, ChatModelRequest):
            raise TypeError("request must be a ChatModelRequest")
        completion = self._completion()
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                call_kwargs = {
                    "model": request.model,
                    "messages": [
                        {"role": "system", "content": request.system_prompt},
                        {"role": "user", "content": request.user_prompt},
                    ],
                    "timeout": request.timeout_seconds,
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                    "response_format": {"type": request.response_format},
                }
                if self._api_key:
                    call_kwargs["api_key"] = self._api_key
                if self._api_base:
                    call_kwargs["api_base"] = self._api_base
                raw = completion(**call_kwargs)
                (
                    content, finish_reason, request_id,
                    prompt_tokens, completion_tokens, total_tokens,
                ) = _extract_content(raw)
                return ChatModelResponse(
                    content=content,
                    provider=self.provider_name,
                    model=request.model,
                    finish_reason=finish_reason,
                    request_id=request_id,
                    attempts=attempt,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )
            except ChatModelError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_attempts or not _retryable_error(exc):
                    break
                self._sleep(self.retry_delay_seconds * attempt)

        # Do not include the raw exception: SDK errors can contain request data
        # or provider details that should not be copied into ordinary logs.
        error_type = type(last_error).__name__ if last_error is not None else "UnknownError"
        raise ChatModelError(
            f"chat model request failed after {attempt} attempt(s): {error_type}"
        ) from last_error


def parse_json_object(content: str) -> dict[str, Any]:
    """Parse strict JSON object output; reject markdown fences and non-object JSON."""
    if not isinstance(content, str) or not content.strip():
        raise ValueError("model content must be a non-empty string")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("model response is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("model response JSON must be an object")
    return parsed
