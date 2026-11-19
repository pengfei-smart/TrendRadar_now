# coding=utf-8
"""
AI 客户端模块

基于 LiteLLM 的统一 AI 模型接口
支持 100+ AI 提供商（OpenAI、DeepSeek、Gemini、Claude、国内模型等）
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from litellm import completion

from .usage_ledger import AIUsageLedger, AIUsageRecord


logger = logging.getLogger(__name__)


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

    def __init__(self, config: Dict[str, Any], *, feature: str = "application", usage_ledger: AIUsageLedger | None = None):
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

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        usage_operation: str = "chat",
        **kwargs
    ) -> str:
        """
        调用 AI 模型进行对话

        Args:
            messages: 消息列表，格式: [{"role": "system/user/assistant", "content": "..."}]
            **kwargs: 额外参数，会覆盖默认配置

        Returns:
            str: AI 响应内容

        Raises:
            Exception: API 调用失败时抛出异常
        """
        # 构建请求参数
        params = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "timeout": kwargs.get("timeout", self.timeout),
            "num_retries": kwargs.get("num_retries", self.num_retries),
        }

        # 添加 API Key
        if self.api_key:
            params["api_key"] = self.api_key

        # 添加 API Base（如果配置了）
        if self.api_base:
            params["api_base"] = self.api_base

        # 添加 max_tokens（如果配置了且不为 0）
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        if max_tokens and max_tokens > 0:
            params["max_tokens"] = max_tokens

        # 添加 fallback 模型（如果配置了）
        if self.fallback_models:
            params["fallbacks"] = self.fallback_models

        # 合并其他额外参数
        for key, value in kwargs.items():
            if key not in params:
                params[key] = value

        # 只记录请求元数据和供应商返回的用量，不保存 prompt、响应正文或密钥。
        started_at = datetime.now(timezone.utc)
        try:
            response = completion(**params)
        except Exception as exc:
            self._append_usage(
                operation=usage_operation, model=self.model, status="error",
                started_at=started_at, error_code=type(exc).__name__,
            )
            raise
        completed_at = datetime.now(timezone.utc)
        actual_model = _read_value(response, "model") or self.model
        self._append_usage(
            operation=usage_operation, model=str(actual_model), status="success",
            started_at=started_at, completed_at=completed_at,
            request_id=str(_read_value(response, "id") or ""),
            **_response_usage(response),
        )

        # 提取响应内容
        # 某些模型/提供商返回 list（内容块）而非 str，统一转为 str
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
        request_id: str = "", error_code: str = "", **usage: Any,
    ) -> None:
        """Best-effort persistence; telemetry failures never break AI features."""
        try:
            if self._usage_ledger is None:
                self._usage_ledger = AIUsageLedger()
            self._usage_ledger.append(AIUsageRecord(
                feature=self.feature, operation=operation, provider="litellm",
                model=model, request_status=status, request_id=request_id,
                started_at=started_at,
                completed_at=completed_at or datetime.now(timezone.utc),
                error_code=error_code, attempts=None, attempts_known=False,
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
