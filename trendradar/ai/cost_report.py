"""Historical AI usage cost estimation and aggregation.

This module is pure: callers provide ledger rows and receive estimates and
aggregates. It never mutates the append-only usage ledger.
"""
from __future__ import annotations

import argparse
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


def _validate_date_range(start_date: date | None, end_date: date | None) -> None:
    for value, name in ((start_date, "start_date"), (end_date, "end_date")):
        if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
            raise TypeError(f"{name} must be a date or None")
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must not be after end_date")


def build_cost_report_from_ledger(
    ledger,
    *,
    batch_size: int = 500,
    start_date: date | None = None,
    end_date: date | None = None,
    calendar_path=None,
) -> UsageCostReport:
    """Build a date-filtered report from a consistent ledger read snapshot."""
    if not hasattr(ledger, "iter_records"):
        raise TypeError("ledger must provide iter_records(batch_size=...)")
    return build_cost_report(
        ledger.iter_records(batch_size=batch_size),
        start_date=start_date,
        end_date=end_date,
        calendar_path=calendar_path,
    )


def build_cost_report(
    records: Iterable[dict[str, Any]],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    calendar_path=None,
) -> UsageCostReport:
    """Estimate and aggregate records; partial/unsupported rows never add cost."""
    _validate_date_range(start_date, end_date)
    selected = []
    for record in records:
        # Filter by Beijing-local calendar date, not the host machine timezone.
        local_date = _parse_timestamp(record.get("started_at")).astimezone(BEIJING_TZ).date()
        if start_date is not None and local_date < start_date:
            continue
        if end_date is not None and local_date > end_date:
            continue
        selected.append(estimate_usage_record(record, calendar_path=calendar_path))
    rows = tuple(selected)
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


def _parse_cli_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def main(argv: list[str] | None = None) -> int:
    """Print a human-readable cost report for a selected Beijing-local period."""
    parser = argparse.ArgumentParser(description="TrendRadar AI API cost report")
    parser.add_argument("--period", choices=("today", "month", "all"), default="month")
    parser.add_argument("--start-date", type=_parse_cli_date)
    parser.add_argument("--end-date", type=_parse_cli_date)
    parser.add_argument("--db", help="SQLite ledger path (defaults to AI_USAGE_DB_PATH or data/ai_usage.db)")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args(argv)

    if args.period != "all" and (args.start_date or args.end_date):
        parser.error("--start-date/--end-date can only be combined with --period all")
    if args.period == "today":
        today = datetime.now(BEIJING_TZ).date()
        start_date = end_date = today
    elif args.period == "month":
        today = datetime.now(BEIJING_TZ).date()
        start_date = today.replace(day=1)
        end_date = today
    else:
        start_date, end_date = args.start_date, args.end_date
    if args.batch_size < 1 or args.batch_size > 500:
        parser.error("--batch-size must be between 1 and 500")

    from trendradar.ai.usage_ledger import AIUsageLedger

    ledger = AIUsageLedger(args.db)
    report = build_cost_report_from_ledger(
        ledger,
        batch_size=args.batch_size,
        start_date=start_date,
        end_date=end_date,
    )
    summary = report.aggregate
    print(f"AI usage cost report ({args.period})")
    print(f"Range: {start_date or 'beginning'} to {end_date or 'latest'} (Asia/Shanghai)")
    print(f"Records: {len(report.rows)}")
    print(f"Calculated: {summary.calculated_count}")
    print(f"Partial: {summary.partial_count}")
    print(f"Unsupported: {summary.unsupported_count}")
    print(f"Estimated total: CNY {summary.total_cost:.6f}")
    print("\nBy model:")
    for key, amount in summary.by_model.items():
        print(f"  {key}: CNY {amount:.6f}")
    print("\nBy feature:")
    for key, amount in summary.by_feature.items():
        print(f"  {key}: CNY {amount:.6f}")
    print("\nBy day:")
    for key, amount in summary.by_day.items():
        print(f"  {key}: CNY {amount:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CostAggregate",
    "UsageCostReport",
    "UsageCostRow",
    "build_cost_report",
    "build_cost_report_from_ledger",
    "estimate_usage_record",
    "main",
]
