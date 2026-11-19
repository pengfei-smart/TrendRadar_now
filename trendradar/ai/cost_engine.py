"""Deterministic DeepSeek token-cost calculation.

Prices are stored as CNY per one million tokens, copied from the official
Chinese pricing page. The caller must provide a verified peak/off-peak
classification; this module deliberately does not guess Chinese holidays.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

PRICE_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
PRICE_UNIT = "CNY"
TOKENS_PER_MILLION = Decimal("1000000")

# CNY per 1M tokens: cache-hit input, cache-miss input, output.
# Order: off-peak, peak. Keep decimal strings to avoid binary-float errors.
_PRICE_CATALOG = {
    "deepseek-flash": {
        "off_peak": (Decimal("0.02"), Decimal("1"), Decimal("4")),
        "peak": (Decimal("0.04"), Decimal("2"), Decimal("8")),
        "aliases": {"deepseek-flash", "deepseek-v4-flash",
                    "deepseek-v4-flash-vision-exp"},
    },
    "deepseek-v4-pro": {
        "off_peak": (Decimal("0.15"), Decimal("4.5"), Decimal("13.5")),
        "peak": (Decimal("0.30"), Decimal("9"), Decimal("27")),
        "aliases": {"deepseek-v4-pro"},
    },
}


@dataclass(frozen=True)
class AICostEstimate:
    status: Literal["calculated", "partial", "unsupported"]
    currency: str = PRICE_UNIT
    total_cost: Decimal | None = None
    cache_hit_cost: Decimal | None = None
    cache_miss_cost: Decimal | None = None
    output_cost: Decimal | None = None
    model_key: str = ""
    period: str = ""
    reason: str = ""
    price_source: str = PRICE_SOURCE


def _model_key(model: str) -> str | None:
    if not isinstance(model, str) or not model.strip():
        return None
    normalized = model.strip().lower()
    # LiteLLM model identifiers commonly include a provider prefix.
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    for key, item in _PRICE_CATALOG.items():
        if normalized in item["aliases"]:
            return key
    return None


def _valid_count(value: int | None) -> bool:
    return value is not None and not isinstance(value, bool) and isinstance(value, int) and value >= 0


def estimate_deepseek_cost(
    *,
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    prompt_cache_hit_tokens: int | None,
    prompt_cache_miss_tokens: int | None,
    is_peak: bool | None,
) -> AICostEstimate:
    """Estimate cost only when all billable token buckets and period are known.

    Cache hit + cache miss must exactly reconcile to prompt_tokens. Missing
    telemetry is never interpreted as zero. The caller determines peak status
    using an authoritative Chinese holiday calendar.
    """
    model_key = _model_key(model)
    if model_key is None:
        return AICostEstimate(
            status="unsupported", reason="unsupported_or_unmapped_model"
        )

    if is_peak is None:
        return AICostEstimate(
            status="partial", model_key=model_key,
            reason="peak_period_unknown",
        )
    if not isinstance(is_peak, bool):
        raise TypeError("is_peak must be bool or None")

    counts = (
        prompt_tokens, completion_tokens,
        prompt_cache_hit_tokens, prompt_cache_miss_tokens,
    )
    if not all(_valid_count(value) for value in counts):
        return AICostEstimate(
            status="partial", model_key=model_key,
            period="peak" if is_peak else "off_peak",
            reason="token_usage_incomplete",
        )
    if prompt_cache_hit_tokens + prompt_cache_miss_tokens != prompt_tokens:
        return AICostEstimate(
            status="partial", model_key=model_key,
            period="peak" if is_peak else "off_peak",
            reason="cache_breakdown_does_not_reconcile",
        )

    period = "peak" if is_peak else "off_peak"
    hit_rate, miss_rate, output_rate = _PRICE_CATALOG[model_key][period]
    hit_cost = Decimal(prompt_cache_hit_tokens) * hit_rate / TOKENS_PER_MILLION
    miss_cost = Decimal(prompt_cache_miss_tokens) * miss_rate / TOKENS_PER_MILLION
    output_cost = Decimal(completion_tokens) * output_rate / TOKENS_PER_MILLION
    total = hit_cost + miss_cost + output_cost
    return AICostEstimate(
        status="calculated",
        total_cost=total,
        cache_hit_cost=hit_cost,
        cache_miss_cost=miss_cost,
        output_cost=output_cost,
        model_key=model_key,
        period=period,
    )


__all__ = [
    "AICostEstimate",
    "PRICE_SOURCE",
    "estimate_deepseek_cost",
]
