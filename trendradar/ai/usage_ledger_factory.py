"""Shared factory for application-wide AI usage ledgers."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from .usage_ledger import AIUsageLedger


def resolve_ai_usage_db_path(
    path: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve an explicit or configured ledger path to an absolute path."""
    selected = path or os.environ.get("AI_USAGE_DB_PATH") or "data/ai_usage.db"
    return Path(selected).expanduser().resolve()


@lru_cache(maxsize=16)
def _cached_ledger(path: str) -> AIUsageLedger:
    return AIUsageLedger(path)


def get_ai_usage_ledger(
    path: str | os.PathLike[str] | None = None,
) -> AIUsageLedger:
    """Return the shared ledger instance for the resolved database path."""
    resolved = resolve_ai_usage_db_path(path)
    return _cached_ledger(str(resolved))


def clear_ai_usage_ledger_cache() -> None:
    """Clear cached instances, primarily for tests and controlled reconfiguration."""
    _cached_ledger.cache_clear()
