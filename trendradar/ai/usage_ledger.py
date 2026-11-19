"""Privacy-conscious SQLite ledger for application-wide AI API usage.

Only request metadata and provider-reported token counts are stored. Prompts,
responses, credentials, and raw provider payloads are never persisted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from contextlib import closing
from typing import Any


def _token_count(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer or None")
    return value


def _aware_iso(value: datetime | None, field: str) -> str:
    value = value or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.isoformat()


@dataclass(frozen=True)
class AIUsageRecord:
    feature: str
    operation: str
    provider: str
    model: str
    request_status: str
    request_id: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None
    attempts: int | None = None
    attempts_known: bool = False
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str = ""

    def __post_init__(self):
        for field in ("feature", "operation", "provider", "model"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        if self.request_status not in {"success", "error"}:
            raise ValueError("request_status must be success or error")
        for field in (
            "prompt_tokens", "completion_tokens", "total_tokens",
            "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "attempts",
        ):
            _token_count(getattr(self, field), field)
        if not isinstance(self.attempts_known, bool):
            raise ValueError("attempts_known must be boolean")
        _aware_iso(self.started_at, "started_at")
        _aware_iso(self.completed_at, "completed_at")


class AIUsageLedger:
    """Append-only, process-safe usage ledger backed by a separate SQLite file."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        selected = path or os.environ.get("AI_USAGE_DB_PATH") or "data/ai_usage.db"
        self.path = Path(selected)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30.0)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS ai_usage_records (
                    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    feature TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    request_status TEXT NOT NULL CHECK(request_status IN ('success','error')),
                    request_id TEXT NOT NULL DEFAULT '',
                    prompt_tokens INTEGER CHECK(prompt_tokens IS NULL OR prompt_tokens >= 0),
                    completion_tokens INTEGER CHECK(completion_tokens IS NULL OR completion_tokens >= 0),
                    total_tokens INTEGER CHECK(total_tokens IS NULL OR total_tokens >= 0),
                    prompt_cache_hit_tokens INTEGER CHECK(prompt_cache_hit_tokens IS NULL OR prompt_cache_hit_tokens >= 0),
                    prompt_cache_miss_tokens INTEGER CHECK(prompt_cache_miss_tokens IS NULL OR prompt_cache_miss_tokens >= 0),
                    attempts INTEGER CHECK(attempts IS NULL OR attempts >= 0),
                    attempts_known INTEGER NOT NULL CHECK(attempts_known IN (0,1)),
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    error_code TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_usage_started
                ON ai_usage_records(started_at)
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_usage_feature_model
                ON ai_usage_records(feature, model, started_at)
            """)
            connection.executescript("""
                CREATE TRIGGER IF NOT EXISTS trg_ai_usage_no_update
                BEFORE UPDATE ON ai_usage_records
                BEGIN SELECT RAISE(ABORT, 'AI usage records are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS trg_ai_usage_no_delete
                BEFORE DELETE ON ai_usage_records
                BEGIN SELECT RAISE(ABORT, 'AI usage records are append-only'); END;
            """)

    def append(self, record: AIUsageRecord) -> int:
        if not isinstance(record, AIUsageRecord):
            raise TypeError("record must be an AIUsageRecord")
        started_at = _aware_iso(record.started_at, "started_at")
        completed_at = _aware_iso(record.completed_at, "completed_at")
        created_at = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """INSERT INTO ai_usage_records (
                    feature,operation,provider,model,request_status,request_id,
                    prompt_tokens,completion_tokens,total_tokens,
                    prompt_cache_hit_tokens,prompt_cache_miss_tokens,
                    attempts,attempts_known,started_at,completed_at,error_code,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.feature, record.operation, record.provider, record.model,
                    record.request_status, record.request_id,
                    record.prompt_tokens, record.completion_tokens, record.total_tokens,
                    record.prompt_cache_hit_tokens, record.prompt_cache_miss_tokens,
                    record.attempts, int(record.attempts_known),
                    started_at, completed_at, record.error_code, created_at,
                ),
            )
            return int(cursor.lastrowid)

    def list_records(self, *, limit: int = 100, offset: int = 0) -> tuple[dict[str, Any], ...]:
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if isinstance(offset, bool) or offset < 0:
            raise ValueError("offset must be non-negative")
        with closing(self._connect()) as connection, connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """SELECT * FROM ai_usage_records
                ORDER BY usage_id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return tuple(dict(row) for row in rows)


__all__ = ["AIUsageLedger", "AIUsageRecord"]
