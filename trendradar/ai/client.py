# coding=utf-8
"""
AI 客户端模块

基于 LiteLLM 的统一 AI 模型接口
支持 100+ AI 提供商（OpenAI、DeepSeek、Gemini、Claude、国内模型等）
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from litellm import completion
from litellm.exceptions import (
    APIConnectionError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from .request_guard import AIRequestNotAllowedError
from .usage_ledger import AIUsageLedger, AIUsageRecord

from .usage_ledger_factory import get_ai_usage_ledger

logger = logging.getLogger(__name__)

# 临时诊断：在 LiteLLM 自身 atexit 清理之前记录异步客户端状态。
# 诊断结束后可以整体删除，不影响业务逻辑。
import atexit
import sys
import litellm


def _diagnose_litellm_async_clients():
    prefix = "[LiteLLM退出诊断]"

    def log(message):
        print(f"{prefix} {message}", file=sys.stderr, flush=True)

    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            litellm_version = version("litellm")
        except PackageNotFoundError:
            litellm_version = "unknown"

        log(f"version={litellm_version}")

        cache = vars(litellm).get("in_memory_llm_clients_cache")
        if cache is None:
            log("client_cache=not_found")
            return

        cache_dict = vars(cache).get("cache_dict", {})
        handlers = list(cache_dict.values())

        log(f"cached_handler_count={len(handlers)}")

        for index, handler in enumerate(handlers):
            handler_name = type(handler).__name__
            handler_dict = vars(handler)

            client = handler_dict.get("client")
            if client is None:
                client = handler_dict.get("_client")

            log(f"handler[{index}]={handler_name}")

            if client is None:
                log(f"handler[{index}].client=not_found")
                continue

            log(
                f"handler[{index}].client_type="
                f"{type(client).__name__}"
            )
            log(
                f"handler[{index}].client_closed="
                f"{getattr(client, 'is_closed', 'unknown')}"
            )

            transport = vars(client).get("_transport")
            if transport is None:
                log(f"handler[{index}].transport=not_found")
                continue

            log(
                f"handler[{index}].transport_type="
                f"{type(transport).__name__}"
            )

            session = vars(transport).get("client")

            log(
                f"handler[{index}].transport_client_type="
                f"{type(session).__name__}"
            )
            log(
                f"handler[{index}].transport_client_is_factory="
                f"{callable(session)}"
            )

            if session is None or callable(session):
                log(f"handler[{index}].aiohttp_session=not_created")
                continue

            session_loop = getattr(session, "_loop", None)

            log(
                f"handler[{index}].aiohttp_session_closed="
                f"{getattr(session, 'closed', 'unknown')}"
            )

            if session_loop is None:
                log(f"handler[{index}].session_loop=none")
            else:
                log(
                    f"handler[{index}].session_loop="
                    f"id:{id(session_loop)},"
                    f"closed:{session_loop.is_closed()},"
                    f"running:{session_loop.is_running()}"
                )

    except Exception as exc:
        # 诊断异常绝不能影响程序正常退出。
        log(f"diagnostic_error={type(exc).__name__}: {exc}")


atexit.register(_diagnose_litellm_async_clients)


def _read_value(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _reported_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _response_usage(response: Any) -> dict:
    usage = _read_value(response, "usage")
    details = _read_value(usage, "prompt_tokens_details")
    hit = _read_value(usage, "prompt_cache_hit_tokens")
    if hit is None:
        hit = _read_value(details, "cached_tokens")
    return {
        "prompt_tokens": _reported_count(_read_value(usage, "prompt_tokens")),
        "completion_tokens": _reported_count(_read_value(usage, "completion_tokens")),
        "total_tokens": _reported_count(_read_value(usage, "total_tokens")),
        "prompt_cache_hit_tokens": _reported_count(hit),
        "prompt_cache_miss_tokens": _reported_count(
            _read_value(usage, "prompt_cache_miss_tokens")
        ),
    }


class AIClient:
    """统一的 AI 客户端（基于 LiteLLM）"""

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        feature: str = "application",
        usage_ledger: AIUsageLedger | None = None,
        request_guard=None,
    ):
        """
        初始化 AI 客户端
        Args:
            config: AI 配置字典
                - MODEL: 模型标识（格式: provider/model_name）
                - API_KEY: API 密钥
                - API_BASE: API 基础 URL（可选）
                - TEMPERATURE: 采样温度
                - MAX_TOKENS: 最大生成 token 数
                - TIMEOUT: 请求超时时间（秒）
                - NUM_RETRIES: 重试次数（可选）
                - FALLBACK_MODELS: 备用模型列表（可选）
        """
        self.model = config.get("MODEL", "deepseek/deepseek-chat")
        self.api_key = config.get("API_KEY") or os.environ.get("AI_API_KEY", "")
        self.api_base = config.get("API_BASE", "")
        self.temperature = config.get("TEMPERATURE", 1.0)
        self.max_tokens = config.get("MAX_TOKENS", 5000)
        self.timeout = config.get("TIMEOUT", 120)
        self.num_retries = config.get("NUM_RETRIES", 2)
        self.fallback_models = config.get("FALLBACK_MODELS", [])
        self.feature = feature
        self._usage_ledger = usage_ledger
        self._request_guard = request_guard


    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        usage_operation: str = "chat",
        **kwargs
    ) -> str:
        """调用 AI 模型进行对话，并在每次真实请求前检查 Guard。"""

        retries = kwargs.get("num_retries", self.num_retries)
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
            raise ValueError("num_retries 必须是非负整数")

        params = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "timeout": kwargs.get("timeout", self.timeout),
            # 关闭 LiteLLM 内部重试，由本客户端统一管理。
            "num_retries": 0,
            "max_retries": 0,
        }

        if self.api_key:
            params["api_key"] = self.api_key

        if self.api_base:
            params["api_base"] = self.api_base

        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        if max_tokens and max_tokens > 0:
            params["max_tokens"] = max_tokens

        for key, value in kwargs.items():
            if key not in params and key not in (
                "num_retries",
                "max_retries",
                "fallbacks",
                "api_key",
                "api_base",
            ):
                params[key] = value

        # 只对明确的临时性错误进行重试。
        retryable_errors = (
            RateLimitError,
            Timeout,
            TimeoutError,
            APIConnectionError,
            ServiceUnavailableError,
            InternalServerError,
        )

        def is_retryable(exc):
            return isinstance(exc, retryable_errors)

        started_at = datetime.now(timezone.utc)
        attempts = 0
        last_error = None

        while attempts <= retries:
            # 每次真实请求之前都必须重新获得时间窗口授权。
            try:
                if self._request_guard is None:
                    raise AIRequestNotAllowedError(
                        "AIClient 缺少 RequestGuard，拒绝发起 AI 请求"
                    )
                self._request_guard.check()
            except Exception:
                # 如果之前已有真实请求失败，只记录那些真实请求。
                if attempts > 0:
                    self._append_usage(
                        operation=usage_operation,
                        model=self.model,
                        status="error",
                        started_at=started_at,
                        error_code=type(last_error).__name__,
                        attempts=attempts,
                        attempts_known=True,
                    )
                raise

            try:
                response = completion(**params)
                attempts += 1
                break
            except Exception as exc:
                attempts += 1
                last_error = exc

                if not is_retryable(exc) or attempts > retries:
                    self._append_usage(
                        operation=usage_operation,
                        model=self.model,
                        status="error",
                        started_at=started_at,
                        error_code=type(exc).__name__,
                        attempts=attempts,
                        attempts_known=True,
                    )
                    raise
                # 指数退避，避免连续请求加剧限流。
                time.sleep(min(0.5 * (2 ** (attempts - 1)), 8))

        completed_at = datetime.now(timezone.utc)
        actual_model = _read_value(response, "model") or self.model

        self._append_usage(
            operation=usage_operation,
            model=str(actual_model),
            status="success",
            started_at=started_at,
            completed_at=completed_at,
            request_id=str(_read_value(response, "id") or ""),
            attempts=attempts,
            attempts_known=True,
            **_response_usage(response),
        )

        content = response.choices[0].message.content
        if isinstance(content, list):
            content = "\n".join(
                item.get("text", str(item)) if isinstance(item, dict) else str(item)
                for item in content
            )

        return content or ""


    def _append_usage(
        self, *, operation: str, model: str, status: str,
        started_at: datetime, completed_at: datetime | None = None,
        request_id: str = "", error_code: str = "",
        attempts: int = 0, attempts_known: bool = True,
        **usage: Any,
    ) -> None:
        """Best-effort persistence; telemetry failures never break AI features."""
        try:
            if self._usage_ledger is None:
                self._usage_ledger = get_ai_usage_ledger()
            self._usage_ledger.append(AIUsageRecord(
                feature=self.feature, operation=operation, provider="litellm",
                model=model, request_status=status, request_id=request_id,
                started_at=started_at,
                completed_at=completed_at or datetime.now(timezone.utc),
                error_code=error_code,
                attempts=attempts,
                attempts_known=attempts_known,
                **usage,
            ))
        except Exception as exc:
            logger.warning(
                "AI usage ledger append failed: feature=%s operation=%s error_type=%s",
                self.feature,
                operation,
                type(exc).__name__,
            )

    def validate_config(self) -> tuple[bool, str]:
        """
        验证配置是否有效

        Returns:
            tuple: (是否有效, 错误信息)
        """
        if not self.model:
            return False, "未配置 AI 模型（model）"

        if not self.api_key:
            return False, "未配置 AI API Key，请在 config.yaml 或环境变量 AI_API_KEY 中设置"

        # 验证模型格式（应该包含 provider/model）
        if "/" not in self.model:
            return False, f"模型格式错误: {self.model}，应为 'provider/model' 格式（如 'deepseek/deepseek-chat'）"

        return True, ""
