"""Shared application context for the independent market workflow."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from trendradar.context import AppContext
from trendradar.core.loader import load_config

from .database_backend import MarketDatabaseBackend
from .event_review_runtime import (
    EventReviewRuntime,
    build_event_review_runtime,
)


DEFAULT_MARKET_DATABASE_PATH = Path("output/market/market.db")
DEFAULT_MARKET_OBJECT_KEY = "market/market.db"


class MarketWorkflowContext:
    """Shared dependencies and resources for one market workflow run.

    This context reuses TrendRadar's application configuration, scheduler,
    and AI request guard. Remote database operations are deliberately
    explicit and are never performed during initialization.
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        db_path: str | Path = DEFAULT_MARKET_DATABASE_PATH,
        run_id: str | None = None,
        remote_object_key: str = DEFAULT_MARKET_OBJECT_KEY,
    ) -> None:
        if not isinstance(config, Mapping):
            raise TypeError("config must be a mapping")

        self.config = config
        self.db_path = Path(db_path)

        if run_id is None:
            self.run_id = uuid4().hex
        elif not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        else:
            self.run_id = run_id

        if not isinstance(remote_object_key, str):
            raise ValueError("remote_object_key must be a non-empty string")

        normalized_object_key = remote_object_key.strip("/")
        if not normalized_object_key.strip():
            raise ValueError("remote_object_key must be a non-empty string")

        self.remote_object_key = normalized_object_key

        # Reuse the same application-level configuration and authorization
        # mechanisms as the main TrendRadar application.
        self.app_context = AppContext(config)

        self._remote_database_backend = self._build_remote_database_backend()
        self._event_review_runtime: EventReviewRuntime | None = None

    @classmethod
    def from_config(
        cls,
        config_path: str | Path | None = None,
        *,
        db_path: str | Path = DEFAULT_MARKET_DATABASE_PATH,
        run_id: str | None = None,
        remote_object_key: str = DEFAULT_MARKET_OBJECT_KEY,
    ) -> "MarketWorkflowContext":
        """Load TrendRadar configuration and create a market context."""
        config = load_config(
            str(config_path) if config_path is not None else None
        )

        return cls(
            config,
            db_path=db_path,
            run_id=run_id,
            remote_object_key=remote_object_key,
        )


    @property
    def storage_backend_mode(self) -> str:
        """Return the configured market database backend mode."""
        storage_config = self.config.get("STORAGE", {})
        if not isinstance(storage_config, Mapping):
            raise ValueError(
                "TrendRadar storage configuration is invalid"
            )

        mode = str(
            storage_config.get("BACKEND", "auto")
        ).strip().lower()

        if mode not in {"auto", "local", "remote"}:
            raise ValueError(
                f"Unsupported storage backend: {mode}"
            )

        return mode

    def validate_storage_backend(self) -> None:
        """Validate that the configured storage backend is usable."""
        mode = self.storage_backend_mode

        if mode == "remote" and self.remote_database_backend is None:
            raise ValueError(
                "Remote storage is required but R2 configuration "
                "is incomplete"
            )

    def _build_remote_database_backend(
        self,
    ) -> MarketDatabaseBackend | None:
        """Create the R2 backend only when remote configuration is complete.

        Local development does not require R2 credentials. In CI, the
        existing S3-compatible environment variables are loaded by
        TrendRadar's normal configuration loader.
        """
        storage_config = self.config.get("STORAGE", {})
        if not isinstance(storage_config, Mapping):
            return None

        backend_mode = str(
            storage_config.get("BACKEND", "auto")
        ).strip().lower()

        if backend_mode not in {"auto", "remote"}:
            return None

        remote_config = storage_config.get("REMOTE", {})
        if not isinstance(remote_config, dict):
            return None

        required_keys = (
            "BUCKET_NAME",
            "ENDPOINT_URL",
            "ACCESS_KEY_ID",
            "SECRET_ACCESS_KEY",
        )

        if not all(
            str(remote_config.get(key) or "").strip()
            for key in required_keys
        ):
            return None

        return MarketDatabaseBackend(
            remote_config,
            object_key=self.remote_object_key,
            expected_schema_version=14,
        )

    @property
    def remote_database_backend(self) -> MarketDatabaseBackend | None:
        """Return the configured remote database backend, if available."""
        return self._remote_database_backend

    @property
    def is_remote_database_enabled(self) -> bool:
        """Whether a remote database backend has been configured."""
        return self._remote_database_backend is not None

    def get_ai_request_guard(self) -> Any:
        """Return the shared TrendRadar AI request guard."""
        return self.app_context.get_ai_request_guard()

    def get_event_review_runtime(self) -> EventReviewRuntime:
        """Build and cache the guarded Event Review runtime on demand."""
        if self._event_review_runtime is None:
            ai_config = self.config.get("AI")

            if not isinstance(ai_config, Mapping):
                raise ValueError("TrendRadar AI configuration is missing")

            self._event_review_runtime = build_event_review_runtime(
                ai_config,
                request_guard=self.get_ai_request_guard(),
            )

        return self._event_review_runtime


__all__ = [
    "DEFAULT_MARKET_DATABASE_PATH",
    "DEFAULT_MARKET_OBJECT_KEY",
    "MarketWorkflowContext",
]
