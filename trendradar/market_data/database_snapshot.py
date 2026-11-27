"""Safe SQLite snapshots and restore operations for the market database."""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from pathlib import Path
logger = logging.getLogger(__name__)

class MarketDatabaseSnapshotError(RuntimeError):
    """Base error for market database snapshot operations."""


class MarketDatabaseValidationError(MarketDatabaseSnapshotError):
    """Raised when a database snapshot is invalid or incompatible."""


class MarketDatabaseRestoreError(MarketDatabaseSnapshotError):
    """Raised when restoring a market database fails."""


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _validate_connection(
    connection: sqlite3.Connection,
    *,
    expected_schema_version: int | None = None,
) -> dict:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise MarketDatabaseValidationError(
            f"SQLite integrity check failed: {integrity[0] if integrity else 'no result'}"
        )

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    required_tables = {
        "schema_meta",
        "security_master",
        "market_events",
    }
    missing = required_tables - tables
    if missing:
        raise MarketDatabaseValidationError(
            f"Missing required market tables: {', '.join(sorted(missing))}"
        )

    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()
    if row is None:
        raise MarketDatabaseValidationError(
            "Market database schema_version is missing"
        )

    try:
        schema_version = int(row[0])
    except (TypeError, ValueError) as exc:
        raise MarketDatabaseValidationError(
            "Market database schema_version is invalid"
        ) from exc

    if expected_schema_version is not None:
        if schema_version != expected_schema_version:
            raise MarketDatabaseValidationError(
                f"Schema version mismatch: expected {expected_schema_version}, "
                f"got {schema_version}"
            )

    return {
        "status": "valid",
        "schema_version": schema_version,
        "table_count": len(tables),
    }


def validate_snapshot(
    snapshot_path: str | Path,
    *,
    expected_schema_version: int | None = None,
) -> dict:
    """Validate a SQLite market snapshot without modifying it."""
    path = _resolved(snapshot_path)
    if not path.is_file():
        raise MarketDatabaseValidationError(
            f"Snapshot file does not exist: {path}"
        )

    try:
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro",
            uri=True,
            timeout=20,
        )
        try:
            return _validate_connection(
                connection,
                expected_schema_version=expected_schema_version,
            )
        finally:
            connection.close()
    except MarketDatabaseValidationError:
        raise
    except sqlite3.Error as exc:
        raise MarketDatabaseValidationError(
            f"Unable to read snapshot: {exc}"
        ) from exc




def create_snapshot(
    source_db_path: str | Path,
    snapshot_path: str | Path,
    *,
    expected_schema_version: int | None = None,
) -> dict:
    """Create a consistent SQLite snapshot using SQLite's backup API."""
    source = _resolved(source_db_path)
    destination = _resolved(snapshot_path)

    if not source.is_file():
        raise MarketDatabaseSnapshotError(
            f"Source market database does not exist: {source}"
        )

    if source == destination:
        raise MarketDatabaseSnapshotError(
            "Snapshot destination must differ from source database"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=str(destination.parent),
    )
    os.close(fd)
    temporary = Path(temporary_name)

    source_connection = None
    snapshot_connection = None

    try:
        try:
            source_connection = sqlite3.connect(str(source), timeout=20)
            snapshot_connection = sqlite3.connect(
                str(temporary),
                timeout=20,
            )

            source_connection.backup(snapshot_connection)
            snapshot_connection.commit()

        finally:
            try:
                if snapshot_connection is not None:
                    snapshot_connection.close()
            finally:
                if source_connection is not None:
                    source_connection.close()

        # Both SQLite connections must be closed before validation
        # and atomic replacement, especially on Windows.
        validation = validate_snapshot(
            temporary,
            expected_schema_version=expected_schema_version,
        )

        os.replace(temporary, destination)

        return {
            **validation,
            "status": "success",
            "snapshot_path": str(destination),
        }

    except Exception as exc:
        if isinstance(exc, MarketDatabaseSnapshotError):
            raise

        raise MarketDatabaseSnapshotError(
            f"Unable to create market database snapshot: {exc}"
        ) from exc

    finally:
        if temporary.exists():
            temporary.unlink()

def _cleanup_temporary_files(paths: list[Path | None]) -> None:
    """Best-effort cleanup without stopping after the first failure."""
    for path in paths:
        if path is None:
            continue

        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Unable to remove temporary database file: %s",
                path,
                exc_info=True,
            )

def restore_snapshot(
    snapshot_path: str | Path,
    target_db_path: str | Path,
    *,
    expected_schema_version: int | None = None,
) -> dict:
    """Restore a validated snapshot while preserving the previous database."""
    snapshot = _resolved(snapshot_path)
    target = _resolved(target_db_path)

    if not snapshot.is_file():
        raise MarketDatabaseRestoreError(
            f"Snapshot file does not exist: {snapshot}"
        )

    if snapshot == target:
        raise MarketDatabaseRestoreError(
            "Snapshot and target database paths must differ"
        )

    try:
        validation = validate_snapshot(
            snapshot,
            expected_schema_version=expected_schema_version,
        )
    except MarketDatabaseSnapshotError as exc:
        raise MarketDatabaseRestoreError(
            f"Refusing to restore invalid snapshot: {exc}"
        ) from exc

    target.parent.mkdir(parents=True, exist_ok=True)

    # Never replace a database while SQLite sidecars exist.
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{target}{suffix}")
        if sidecar.exists():
            raise MarketDatabaseRestoreError(
                f"Target database has an active SQLite sidecar: {sidecar}"
            )

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".restore",
        dir=str(target.parent),
    )
    os.close(fd)
    temporary = Path(temporary_name)

    backup_path: Path | None = None
    backup_reservation: Path | None = None
    backup_temporary: Path | None = None

    try:

        source_connection = None
        target_connection = None

        try:
            source_connection = sqlite3.connect(
                f"file:{snapshot.as_posix()}?mode=ro",
                uri=True,
                timeout=20,
            )
            target_connection = sqlite3.connect(
                str(temporary),
                timeout=20,
            )

            source_connection.backup(target_connection)
            target_connection.commit()

        finally:
            try:
                if target_connection is not None:
                    target_connection.close()
            finally:
                if source_connection is not None:
                    source_connection.close()

        validate_snapshot(
            temporary,
            expected_schema_version=expected_schema_version,
        )

        if target.exists():
            # Reserve a unique backup filename without overwriting history.
            fd, backup_name = tempfile.mkstemp(
                prefix=f"{target.name}.pre-restore.",
                suffix=".bak",
                dir=str(target.parent),
            )
            os.close(fd)
            backup_reservation = Path(backup_name)
            backup_path = backup_reservation

            # Copy the old database to a temporary file first.
            fd, backup_temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.backup.",
                suffix=".tmp",
                dir=str(target.parent),
            )
            os.close(fd)
            backup_temporary = Path(backup_temp_name)

            with target.open("rb") as original:
                with backup_temporary.open("wb") as copy:
                    while chunk := original.read(1024 * 1024):
                        copy.write(chunk)
                    copy.flush()
                    os.fsync(copy.fileno())

            # Validate the backup before publishing it.
            # Do not enforce the current schema version here:
            # the previous database may legitimately use an older schema.
            validate_snapshot(backup_temporary)

            # Publish the completed backup before replacing the database.
            os.replace(backup_temporary, backup_path)
            backup_temporary = None
            backup_reservation = None
        # The original database is now safely backed up.
        # If this replacement fails, the backup remains available.
        os.replace(temporary, target)

        return {
            "status": "success",
            "target_path": str(target),
            "schema_version": validation["schema_version"],
            "previous_database_backup": (
                str(backup_path) if backup_path is not None else None
            ),
        }

    except Exception as exc:
        if isinstance(exc, MarketDatabaseRestoreError):
            raise
        raise MarketDatabaseRestoreError(
            f"Unable to restore market database: {exc}"
        ) from exc

    finally:
        _cleanup_temporary_files(
            [
                temporary,
                backup_temporary,
                backup_reservation,
            ]
        )