"""Independent append-only journal for review persistence failures.

This sidecar is intentionally minimal: it records identifiers, timestamps and
exception types only. It never stores prompts, evidence excerpts, credentials,
model responses or raw exception messages.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Mapping


def append_review_failure(
    *,
    journal_path: str | Path,
    record: Mapping[str, object],
) -> Path:
    """Append one bounded JSONL record and fsync it to the sidecar file."""
    path = Path(journal_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_record = {
        "journal_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "record": dict(record),
    }
    payload = (
        json.dumps(
            safe_record, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ) + "\n"
    ).encode("utf-8")
    if len(payload) > 16_384:
        raise ValueError("failure journal record exceeds 16 KiB")
    descriptor = os.open(
        str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    try:
        written = os.write(descriptor, payload)
        if written != len(payload):
            raise OSError("short write to review failure journal")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


__all__ = ["append_review_failure"]
