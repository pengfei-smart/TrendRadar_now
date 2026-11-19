"""Read-only analytics for persisted AI event-review model usage.

Token counts are provider-reported observations. Missing values are never
estimated, and this module deliberately does not calculate monetary cost.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .event_review_temporal import parse_aware_datetime
from .storage import MarketStorage


@dataclass(frozen=True)
class UsageTotals:
    calls: int
    successful_calls: int
    failed_calls: int
    complete_usage_calls: int
    incomplete_usage_calls: int
    missing_usage_calls: int
    partial_usage_calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_token_observations: int
    completion_token_observations: int
    total_token_observations: int


@dataclass(frozen=True)
class UsageBreakdown:
    key: str
    totals: UsageTotals


@dataclass(frozen=True)
class EventReviewUsageReport:
    start_at: str
    end_at: str
    event_id: str
    totals: UsageTotals
    by_role: tuple[UsageBreakdown, ...]
    by_model: tuple[UsageBreakdown, ...]
    by_event: tuple[UsageBreakdown, ...]
    by_day: tuple[UsageBreakdown, ...]


_ZERO_TOTALS = UsageTotals(
    calls=0, successful_calls=0, failed_calls=0,
    complete_usage_calls=0, incomplete_usage_calls=0,
    missing_usage_calls=0, partial_usage_calls=0,
    prompt_tokens=0, completion_tokens=0, total_tokens=0,
    prompt_token_observations=0, completion_token_observations=0,
    total_token_observations=0,
)


def _validate_filter_timestamp(value: str | None, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or parse_aware_datetime(value) is None:
        raise ValueError(f"{field} must be an ISO-8601 timestamp with timezone")
    return value


def _totals(row) -> UsageTotals:
    if row is None:
        return _ZERO_TOTALS
    return UsageTotals(
        calls=row["calls"],
        successful_calls=row["successful_calls"],
        failed_calls=row["failed_calls"],
        complete_usage_calls=row["complete_usage_calls"],
        incomplete_usage_calls=row["incomplete_usage_calls"],
        missing_usage_calls=row["missing_usage_calls"],
        partial_usage_calls=row["partial_usage_calls"],
        prompt_tokens=row["prompt_tokens"] or 0,
        completion_tokens=row["completion_tokens"] or 0,
        total_tokens=row["total_tokens"] or 0,
        prompt_token_observations=row["prompt_token_observations"],
        completion_token_observations=row["completion_token_observations"],
        total_token_observations=row["total_token_observations"],
    )


def _aggregate_sql(group_column: str = "") -> str:
    group = f", {group_column} AS group_key" if group_column else ""
    suffix = f" GROUP BY {group_column} ORDER BY group_key" if group_column else ""
    return f"""
        SELECT
            COUNT(*) AS calls,
            SUM(CASE WHEN request_status='success' THEN 1 ELSE 0 END) AS successful_calls,
            SUM(CASE WHEN request_status='error' THEN 1 ELSE 0 END) AS failed_calls,
            SUM(CASE WHEN usage_complete=1 THEN 1 ELSE 0 END) AS complete_usage_calls,
            SUM(CASE WHEN usage_complete=0 THEN 1 ELSE 0 END) AS incomplete_usage_calls,
            SUM(CASE WHEN usage_complete=0
                AND prompt_tokens IS NULL AND completion_tokens IS NULL
                AND total_tokens IS NULL THEN 1 ELSE 0 END) AS missing_usage_calls,
            SUM(CASE WHEN usage_complete=0
                AND (prompt_tokens IS NOT NULL OR completion_tokens IS NOT NULL
                     OR total_tokens IS NOT NULL) THEN 1 ELSE 0 END) AS partial_usage_calls,
            COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens),0) AS completion_tokens,
            COALESCE(SUM(total_tokens),0) AS total_tokens,
            COUNT(prompt_tokens) AS prompt_token_observations,
            COUNT(completion_tokens) AS completion_token_observations,
            COUNT(total_tokens) AS total_token_observations
            {group}
        FROM event_review_model_usage
        WHERE 1=1
            {{where_clause}}
        {suffix}
    """


def get_event_review_usage_report(
    storage: MarketStorage,
    *,
    start_at: str | None = None,
    end_at: str | None = None,
    event_id: str = "",
    limit: int = 100,
) -> EventReviewUsageReport:
    """Aggregate recorded usage; start_at is inclusive and end_at is exclusive.

    Date buckets use the calendar date written in call_started_at (the
    timestamp's own offset), while range filtering compares absolute instants.
    """
    if not isinstance(storage, MarketStorage):
        raise TypeError("storage must be a MarketStorage instance")
    start = _validate_filter_timestamp(start_at, "start_at")
    end = _validate_filter_timestamp(end_at, "end_at")
    if start and end and parse_aware_datetime(end) <= parse_aware_datetime(start):
        raise ValueError("end_at must be later than start_at")
    if not isinstance(event_id, str):
        raise TypeError("event_id must be a string")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit must be an integer between 1 and 500")

    clauses: list[str] = []
    params: list[object] = []
    if start:
        clauses.append("julianday(call_started_at) >= julianday(?)")
        params.append(start)
    if end:
        clauses.append("julianday(call_started_at) < julianday(?)")
        params.append(end)
    if event_id.strip():
        clauses.append("event_id=?")
        params.append(event_id.strip())
    where_clause = "".join(f" AND ({item})" for item in clauses)

    def query(connection, group_column: str = ""):
        sql = _aggregate_sql(group_column).replace("{where_clause}", where_clause)
        rows = connection.execute(sql, params).fetchall()
        if not group_column:
            return _totals(rows[0] if rows else None)
        return tuple(
            UsageBreakdown(key=row["group_key"], totals=_totals(row))
            for row in rows[:limit]
        )

    with storage.connect() as connection:
        totals = query(connection)
        by_role = query(connection, "reviewer_role")
        by_model = query(connection, "model")
        by_event = query(connection, "event_id")
        by_day = query(connection, "substr(call_started_at,1,10)")

    return EventReviewUsageReport(
        start_at=start, end_at=end, event_id=event_id.strip(),
        totals=totals, by_role=by_role, by_model=by_model,
        by_event=by_event, by_day=by_day,
    )
