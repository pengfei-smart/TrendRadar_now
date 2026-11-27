
"""Safe lifecycle management for the independent market SQLite database."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

from .database_snapshot import (
    _cleanup_temporary_files,
    create_snapshot,
    restore_snapshot,
)
from .database_backend import (
    MarketDatabaseNotFoundError,
    MarketDatabaseRemoteError,
)
from .storage import MarketStorage


class MarketDatabaseLifecycleError(RuntimeError):
    """Raised when the market database lifecycle cannot proceed."""


class MarketDatabaseLockError(MarketDatabaseLifecycleError):
    """Raised when another process owns the market database."""


class MarketDatabaseLifecycle:
    """Coordinate local database initialization, remote restore and upload.

    The lifecycle lock is held from start() until finish() or abort().
    """

    VALID_MODES = {"local", "auto", "remote"}

    def __init__(
        self,
        db_path: str | Path,
        *,
        backend: Any = None,
        storage_mode: str = "local",
        run_id: str | None = None,
        expected_schema_version: int = 14,
    ) -> None:
        self.db_path = Path(db_path)
        self.backend = backend
        self.storage_mode = str(storage_mode).strip().lower()

        if self.storage_mode not in self.VALID_MODES:
            raise ValueError(
                f"Unsupported storage_mode: {self.storage_mode}"
            )

        if self.storage_mode == "remote" and backend is None:
            raise MarketDatabaseLifecycleError(
                "Remote storage is required but no backend is configured"
            )

        if (
            isinstance(expected_schema_version, bool)
            or not isinstance(expected_schema_version, int)
            or expected_schema_version <= 0
        ):
            raise ValueError(
                "expected_schema_version must be a positive integer"
            )

        self.expected_schema_version = expected_schema_version
        self.run_id = uuid4().hex if run_id is None else run_id

        if (
            not isinstance(self.run_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.run_id)
        ):
            raise ValueError(
                "run_id must contain only letters, digits, underscores "
                "or hyphens, with a maximum length of 128"
            )

        self.lock_path = self.db_path.with_name(
            f"{self.db_path.name}.workflow.lock"
        )

        self.storage: MarketStorage | None = None
        self._lock_fd: int | None = None
        self._lock_identity: tuple[int, int] | None = None
        self._started = False
        self._finished = False
        self._snapshot_path: Path | None = None

    def _lock_file_matches_identity(self) -> bool:
        if self._lock_identity is None:
            return False

        try:
            lock_stat = self.lock_path.stat()
        except FileNotFoundError:
            return False
        except OSError:
            logger.warning(
                "Unable to inspect market database lock file: %s",
                self.lock_path,
                exc_info=True,
            )
            return False

        return (
            lock_stat.st_dev,
            lock_stat.st_ino,
        ) == self._lock_identity


    def _acquire_lock(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        lock_payload = json.dumps(
            {
                "run_id": self.run_id,
                "pid": os.getpid(),
            },
            ensure_ascii=False,
        ).encode("utf-8")

        lock_fd = None

        try:
            lock_fd = os.open(
                self.lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            lock_stat = os.fstat(lock_fd)
            self._lock_identity = (
                lock_stat.st_dev,
                lock_stat.st_ino,
            )

            remaining = memoryview(lock_payload)

            while remaining:
                written = os.write(lock_fd, remaining)
                if written <= 0:
                    raise OSError("Failed to write market database lock")
                remaining = remaining[written:]

            os.fsync(lock_fd)
            self._lock_fd = lock_fd

        except FileExistsError as exc:
            raise MarketDatabaseLockError(
                f"Market database is locked: {self.lock_path}. "
                "Check whether another workflow is running. "
                "Do not delete the lock until its owner is confirmed stopped."
            ) from exc

        except Exception:
            if lock_fd is not None:
                try:
                    os.close(lock_fd)
                except OSError:
                    logger.warning(
                        "Unable to close market database lock file descriptor "
                        "during acquisition cleanup",
                        exc_info=True,
                    )

                try:
                    if self._lock_file_matches_identity():
                        self.lock_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "Unable to remove market database lock file "
                        "during acquisition cleanup: %s",
                        self.lock_path,
                        exc_info=True,
                    )
                finally:
                    self._lock_identity = None

            raise

    def _release_lock(self) -> None:
        if self._lock_fd is None:
            return

        lock_fd = self._lock_fd
        self._lock_fd = None

        try:
            os.close(lock_fd)
        except OSError:
            logger.warning(
                "Unable to close market database lock file descriptor",
                exc_info=True,
            )

        try:
            if self._lock_file_matches_identity():
                try:
                    self.lock_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "Unable to remove market database lock file: %s",
                        self.lock_path,
                        exc_info=True,
                    )
        finally:
            self._lock_identity = None

    def _initialize_local_database(self) -> dict[str, Any]:
        self.storage = MarketStorage(str(self.db_path))

        return {
            "status": "initialized_or_opened",
            "source": "local",
            "db_path": str(self.db_path),
        }

    def _restore_remote_database(self) -> dict[str, Any]:
        if self.backend is None:
            if self.storage_mode == "remote":
                raise MarketDatabaseLifecycleError(
                    "Remote storage backend is unavailable"
                )

            return self._initialize_local_database()

        if not self.backend.remote_database_exists():
            # A missing remote object is different from a remote service error.
            # Preserve an existing local database instead of replacing it.
            return self._initialize_local_database()

        download_path = self.db_path.with_name(
            f".{self.db_path.name}.{self.run_id}.download"
        )

        try:
            download_result = self.backend.download_database(
                download_path
            )

            restore_result = restore_snapshot(
                download_path,
                self.db_path,
                expected_schema_version=self.expected_schema_version,
            )

            self.storage = MarketStorage(str(self.db_path))

            return {
                "status": "restored",
                "source": "remote",
                "download": download_result,
                "restore": restore_result,
            }
        finally:
            _cleanup_temporary_files(
                [
                    download_path,
                    Path(f"{download_path}-wal"),
                    Path(f"{download_path}-shm"),
                ]
            )



    def start(self) -> dict[str, Any]:
        """Acquire exclusive ownership and prepare the database."""
        if self._started:
            raise MarketDatabaseLifecycleError(
                "Database lifecycle has already started"
            )

        if self._finished:
            raise MarketDatabaseLifecycleError(
                "Database lifecycle has already finished"
            )

        self._acquire_lock()

        try:
            if self.storage_mode == "local":
                result = self._initialize_local_database()
            else:
                result = self._restore_remote_database()

            database_status = result.get("status", "")

            result["database_status"] = database_status
            result["status"] = "success"

            self._started = True
            return result

        except Exception:
            self._release_lock()
            raise

    def finish(self) -> dict[str, Any]:
        """Create and validate a snapshot, then persist it remotely."""
        if not self._started:
            raise MarketDatabaseLifecycleError(
                "Cannot finish before start()"
            )

        if self._finished:
            raise MarketDatabaseLifecycleError(
                "Database lifecycle has already finished"
            )

        try:
            # Local mode must never access remote storage, even if a
            # backend happens to be configured.
            if self.storage_mode == "local" or self.backend is None:
                self._finished = True
                return {
                    "status": "success",
                    "persistence": "local_only",
                    "db_path": str(self.db_path),
                }

            snapshot_path = self.db_path.with_name(
                f".{self.db_path.name}.{self.run_id}.snapshot"
            )
            self._snapshot_path = snapshot_path

            snapshot_result = create_snapshot(
                self.db_path,
                snapshot_path,
                expected_schema_version=self.expected_schema_version,
            )

            upload_result = self.backend.upload_database(snapshot_path)

            try:
                snapshot_path.unlink(missing_ok=True)
                cleanup_result = {
                    "status": "removed",
                }
            except OSError as exc:
                cleanup_result = {
                    "status": "pending",
                    "error": str(exc),
                }

            return {
                "status": "success",
                "persistence": "remote",
                "snapshot": snapshot_result,
                "upload": upload_result,
                "snapshot_cleanup": cleanup_result,
            }

        finally:
            # A finish attempt is terminal, including failed snapshot
            # creation or failed remote upload. This prevents a second
            # finish() call after the lock has been released.
            self._finished = True
            self._started = False
            self._release_lock()

    def abort(self) -> None:
        """Terminate the lifecycle without attempting a remote upload."""
        if self._finished:
            return

        self._finished = True
        self._started = False
        self._release_lock()

    @property
    def retained_snapshot_path(self) -> Path | None:
        """Return the snapshot path if it still exists after a finish attempt.

        A snapshot may remain because remote upload failed or because
        cleanup failed after a successful upload.
        """
        if self._snapshot_path and self._snapshot_path.exists():
            return self._snapshot_path
        return None

__all__ = [
    "MarketDatabaseLifecycle",
    "MarketDatabaseLifecycleError",
    "MarketDatabaseLockError",
]
