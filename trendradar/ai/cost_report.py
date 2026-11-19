"""Historical AI usage cost estimation and aggregation.

This module is pure: callers provide ledger rows and receive estimates and
aggregates. It never mutates the append-only usage ledger.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

from trendradar.ai.cost_engine import AICostEstimate, estimate_deepseek_cost
from trendradar.ai.peak_period import BEIJING_TZ, classify_peak_period


@dataclass(frozen=True)
class UsageCostRow:
    usage_id: int | None
    feature: str
    operation: str
    model: str
    request_status: str
    started_at: datetime
    local_date: date
    estimate: AICostEstimate


@dataclass(frozen=True)
class CostAggregate:
    calculated_count: int
    partial_count: int
    unsupported_count: int
    total_cost: Decimal
    by_day: dict[str, Decimal]
    by_feature: dict[str, Decimal]
    by_model: dict[str, Decimal]


@dataclass(frozen=True)
class UsageCostReport:
    rows: tuple[UsageCostRow, ...]
    aggregate: CostAggregate


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value)
    else:
        raise ValueError("started_at must be an ISO timestamp or datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("started_at must be timezone-aware")
    return parsed


def estimate_usage_record(
    record: dict[str, Any],
    *,
    calendar_path=None,
) -> UsageCostRow:
    """Estimate one ledger row using its start time and provider token usage."""
    started_at = _parse_timestamp(record.get("started_at"))
    local_start = started_at.astimezone(BEIJING_TZ)
    is_peak = classify_peak_period(started_at, calendar_path=calendar_path) if calendar_path else classify_peak_period(started_at)
    estimate = estimate_deepseek_cost(
        model=record.get("model", ""),
        prompt_tokens=record.get("prompt_tokens"),
        completion_tokens=record.get("completion_tokens"),
        prompt_cache_hit_tokens=record.get("prompt_cache_hit_tokens"),
        prompt_cache_miss_tokens=record.get("prompt_cache_miss_tokens"),
        is_peak=is_peak,
    )
    usage_id = record.get("usage_id")
    if usage_id is not None and (isinstance(usage_id, bool) or not isinstance(usage_id, int)):
        raise ValueError("usage_id must be an integer or None")
    return UsageCostRow(
        usage_id=usage_id,
        feature=str(record.get("feature", "")),
        operation=str(record.get("operation", "")),
        model=str(record.get("model", "")),
        request_status=str(record.get("request_status", "")),
        started_at=started_at,
        local_date=local_start.date(),
        estimate=estimate,
    )


def build_cost_report_from_ledger(
    ledger,
    *,
    batch_size: int = 500,
    calendar_path=None,
) -> UsageCostReport:
    """Build a report directly from an AIUsageLedger consistent read snapshot."""
    if not hasattr(ledger, "iter_records"):
        raise TypeError("ledger must provide iter_records(batch_size=...)")
    return build_cost_report(
        ledger.iter_records(batch_size=batch_size),
        calendar_path=calendar_path,
    )


def build_cost_report(
    records: Iterable[dict[str, Any]],
    *,
    calendar_path=None,
) -> UsageCostReport:
    """Estimate and aggregate records; partial/unsupported rows never add cost."""
    rows = tuple(
        estimate_usage_record(record, calendar_path=calendar_path)
        for record in records
    )
    calculated = partial = unsupported = 0
    total = Decimal("0")
    by_day: dict[str, Decimal] = defaultdict(Decimal)
    by_feature: dict[str, Decimal] = defaultdict(Decimal)
    by_model: dict[str, Decimal] = defaultdict(Decimal)

    for row in rows:
        estimate = row.estimate
        if estimate.status == "partial":
            partial += 1
            continue
        if estimate.status == "unsupported":
            unsupported += 1
            continue
        calculated += 1
        amount = estimate.total_cost or Decimal("0")
        total += amount
        by_day[row.local_date.isoformat()] += amount
        by_feature[row.feature or "unknown"] += amount
        by_model[estimate.model_key] += amount

    return UsageCostReport(
        rows=rows,
        aggregate=CostAggregate(
            calculated_count=calculated,
            partial_count=partial,
            unsupported_count=unsupported,
            total_cost=total,
            by_day=dict(sorted(by_day.items())),
            by_feature=dict(sorted(by_feature.items())),
            by_model=dict(sorted(by_model.items())),
        ),
    )


__all__ = [
    "CostAggregate",
    "UsageCostReport",
    "UsageCostRow",
    "build_cost_report",
    "build_cost_report_from_ledger",
    "estimate_usage_record",
]
