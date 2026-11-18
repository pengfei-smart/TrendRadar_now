"""Temporal validation for pre-event expectation baselines."""
from __future__ import annotations

from datetime import datetime


def parse_aware_datetime(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp only when it includes an explicit timezone."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def event_timestamp(event_payload: dict) -> datetime | None:
    """Read the canonical event time; never infer it from article publication."""
    for key in ("event_time", "occurred_at"):
        parsed = parse_aware_datetime(event_payload.get(key))
        if parsed is not None:
            return parsed
    return None


def is_pre_event_baseline(evidence, event_payload: dict) -> bool:
    """True only when publication is timezone-aware and strictly before event time."""
    occurred = event_timestamp(event_payload)
    published = parse_aware_datetime(evidence.published_at)
    if occurred is None or published is None:
        return False
    return published < occurred
