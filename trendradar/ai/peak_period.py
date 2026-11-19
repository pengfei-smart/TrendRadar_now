"""DeepSeek peak/off-peak classification using sourced China holiday dates.

The classifier intentionally returns unknown for weekdays in years without
an audited holiday calendar. Weekends are always off-peak per provider rules.
"""
from __future__ import annotations

from datetime import date, datetime, time
import json
from pathlib import Path
from zoneinfo import ZoneInfo

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
_DEFAULT_CALENDAR = Path(__file__).with_name("data") / "china_public_holidays.json"


def _load_calendar(path: str | Path = _DEFAULT_CALENDAR) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("timezone") != "Asia/Shanghai":
        raise ValueError("holiday calendar timezone must be Asia/Shanghai")
    years = payload.get("years")
    if not isinstance(years, dict):
        raise ValueError("holiday calendar years must be an object")
    return years


def classify_peak_period(
    timestamp: datetime,
    *,
    calendar_path: str | Path = _DEFAULT_CALENDAR,
) -> bool | None:
    """Return True for peak, False for off-peak, or None when not verifiable.

    Input must be timezone-aware. Classification uses the request start time
    converted to Beijing time. Peak windows are [09:00,12:00) and [14:00,18:00).
    """
    if not isinstance(timestamp, datetime):
        raise TypeError("timestamp must be a datetime")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")

    local = timestamp.astimezone(BEIJING_TZ)
    local_date = local.date()

    # Provider documentation explicitly states that all weekends are off-peak.
    if local_date.weekday() >= 5:
        return False

    years = _load_calendar(calendar_path)
    year_data = years.get(str(local_date.year))
    if year_data is None:
        return None

    holiday_dates = year_data.get("holiday_dates")
    if not isinstance(holiday_dates, list):
        raise ValueError(f"invalid holiday_dates for {local_date.year}")
    if local_date.isoformat() in holiday_dates:
        return False

    current_time = local.timetz().replace(tzinfo=None)
    in_morning_peak = time(9, 0) <= current_time < time(12, 0)
    in_afternoon_peak = time(14, 0) <= current_time < time(18, 0)
    return in_morning_peak or in_afternoon_peak


__all__ = ["BEIJING_TZ", "classify_peak_period"]
