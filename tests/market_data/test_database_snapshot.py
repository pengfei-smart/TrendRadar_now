import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from trendradar.market_data.database_snapshot import (
    MarketDatabaseRestoreError,
    MarketDatabaseSnapshotError,
    MarketDatabaseValidationError,
    create_snapshot,
    restore_snapshot,
    validate_snapshot,
)


class DatabaseSnapshotTests(unittest.TestCase):

    def test_cleanup_temporary_files_continues_after_unlink_failure(self):
        from trendradar.market_data.database_snapshot import (
            _cleanup_temporary_files,
        )

        first = self.root / "first.tmp"
        second = self.root / "second.tmp"
        third = self.root / "third.tmp"

        for path in (first, second, third):
            path.write_text("temporary", encoding="utf-8")

        real_unlink = Path.unlink
        attempted = []

        def tracked_unlink(path, *args, **kwargs):
            attempted.append(path)

            if path == first:
                raise PermissionError("simulated file lock")

            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", tracked_unlink):
            _cleanup_temporary_files([first, second, third])

        self.assertEqual(attempted, [first, second, third])
        self.assertTrue(first.exists())
        self.assertFalse(second.exists())
        self.assertFalse(third.exists())
    def test_restore_rejects_corrupted_backup_before_publishing_it(self):
        self._create_market_db(self.target, schema_version=13)
        original_bytes = self.target.read_bytes()

        create_snapshot(
            self.source,
            self.snapshot,
            expected_schema_version=14,
        )

        from trendradar.market_data import database_snapshot

        real_validate = database_snapshot.validate_snapshot
        backup_validation_calls = []

        def reject_corrupted_backup(path, *args, **kwargs):
            path = Path(path)

            # 模拟旧数据库备份副本损坏
            if ".backup." in path.name:
                backup_validation_calls.append(path)
                raise ValueError("simulated corrupted backup")

            return real_validate(path, *args, **kwargs)

        with mock.patch(
            "trendradar.market_data.database_snapshot.validate_snapshot",
            side_effect=reject_corrupted_backup,
        ):
            with self.assertRaises(MarketDatabaseRestoreError):
                restore_snapshot(
                    self.snapshot,
                    self.target,
                    expected_schema_version=14,
                )

        self.assertTrue(
            backup_validation_calls,
            "The backup copy should be validated before being published",
        )
        self.assertEqual(
            self.target.read_bytes(),
            original_bytes,
            "The original database must remain unchanged",
        )
        self.assertFalse(
            list(self.root.glob("*.pre-restore.*.bak")),
            "A corrupted backup must not be published",
        )

    def test_restore_preserves_backup_when_database_replace_fails(self):
        self._create_market_db(self.target, schema_version=13)
        create_snapshot(
            self.source,
            self.snapshot,
            expected_schema_version=14,
        )

        original_bytes = self.target.read_bytes()

        real_replace = __import__("os").replace

        def failing_replace(source, destination):
            if Path(destination) == self.target:
                raise OSError("simulated database replacement failure")
            return real_replace(source, destination)

        with mock.patch(
            "trendradar.market_data.database_snapshot.os.replace",
            side_effect=failing_replace,
        ):
            with self.assertRaises(MarketDatabaseRestoreError):
                restore_snapshot(
                    self.snapshot,
                    self.target,
                    expected_schema_version=14,
                )

        self.assertEqual(self.target.read_bytes(), original_bytes)

        backups = list(
            self.target.parent.glob(
                f"{self.target.name}.pre-restore.*.bak"
            )
        )
        self.assertTrue(backups, "原数据库备份应该保留下来")

        self.assertEqual(
            validate_snapshot(backups[-1])["schema_version"],
            13,
        )


    def test_restore_snapshot_closes_source_when_target_connect_fails(self):
        # Prepare a valid snapshot before patching sqlite3.connect.
        create_snapshot(
            self.source,
            self.snapshot,
            expected_schema_version=14,
        )

        real_connect = sqlite3.connect
        connections = []
        connect_count = 0

        class ConnectionProxy:
            def __init__(self, connection):
                self.connection = connection
                self.closed = False

            def backup(self, destination):
                return self.connection.backup(destination)

            def commit(self):
                return self.connection.commit()

            def close(self):
                self.closed = True
                return self.connection.close()

        def tracked_connect(*args, **kwargs):
            nonlocal connect_count
            connect_count += 1

            # First connection: validate_snapshot().
            if connect_count == 1:
                return real_connect(*args, **kwargs)

            # Second connection: restore source snapshot.
            if connect_count == 2:
                connection = ConnectionProxy(
                    real_connect(*args, **kwargs)
                )
                connections.append(connection)
                return connection

            # Third connection: fail to create restore target.
            raise sqlite3.OperationalError(
                "simulated restore target connection failure"
            )

        with mock.patch(
            "trendradar.market_data.database_snapshot.sqlite3.connect",
            side_effect=tracked_connect,
        ):
            with self.assertRaises(MarketDatabaseRestoreError):
                restore_snapshot(
                    self.snapshot,
                    self.target,
                    expected_schema_version=14,
                )

        self.assertEqual(len(connections), 1)
        self.assertTrue(
            connections[0].closed,
            "Source snapshot SQLite connection should be closed",
        )


    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source.db"
        self.snapshot = self.root / "snapshot.db"
        self.target = self.root / "target.db"
        self._create_market_db(self.source)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _create_market_db(path, schema_version=14):
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "CREATE TABLE schema_meta ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE security_master ("
                "code TEXT PRIMARY KEY, name TEXT)"
            )
            connection.execute(
                "CREATE TABLE market_events ("
                "event_id TEXT PRIMARY KEY, title TEXT)"
            )
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES(?, ?)",
                ("schema_version", str(schema_version)),
            )
            connection.commit()
        finally:
            connection.close()

    def test_create_and_validate_snapshot(self):
        result = create_snapshot(
            self.source,
            self.snapshot,
            expected_schema_version=14,
        )

        self.assertEqual(result["status"], "success")
        self.assertTrue(self.snapshot.is_file())

        validation = validate_snapshot(
            self.snapshot,
            expected_schema_version=14,
        )
        self.assertEqual(validation["status"], "valid")
        self.assertEqual(validation["schema_version"], 14)

    def test_snapshot_contains_committed_wal_data(self):
        connection = sqlite3.connect(self.source)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "INSERT INTO security_master(code, name) VALUES(?, ?)",
                ("600000", "浦发银行"),
            )
            connection.commit()

            create_snapshot(
                self.source,
                self.snapshot,
                expected_schema_version=14,
            )
        finally:
            connection.close()

        snapshot_connection = sqlite3.connect(self.snapshot)
        try:
            row = snapshot_connection.execute(
                "SELECT name FROM security_master WHERE code='600000'"
            ).fetchone()
            self.assertEqual(row[0], "浦发银行")
        finally:
            snapshot_connection.close()

    def test_validate_rejects_wrong_schema_version(self):
        with self.assertRaises(MarketDatabaseValidationError):
            validate_snapshot(
                self.source,
                expected_schema_version=13,
            )

    def test_validate_rejects_missing_file(self):
        with self.assertRaises(MarketDatabaseValidationError):
            validate_snapshot(self.root / "missing.db")

    def test_validate_rejects_database_without_market_tables(self):
        invalid = self.root / "invalid.db"
        connection = sqlite3.connect(invalid)
        try:
            connection.execute("CREATE TABLE unrelated (id INTEGER)")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(MarketDatabaseValidationError):
            validate_snapshot(invalid)

    def test_create_snapshot_rejects_same_source_and_destination(self):
        with self.assertRaises(MarketDatabaseSnapshotError):
            create_snapshot(self.source, self.source)

    def test_restore_snapshot_replaces_target_and_keeps_backup(self):
        self._create_market_db(self.target, schema_version=13)
        create_snapshot(
            self.source,
            self.snapshot,
            expected_schema_version=14,
        )

        result = restore_snapshot(
            self.snapshot,
            self.target,
            expected_schema_version=14,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["schema_version"], 14)

        backup = Path(result["previous_database_backup"])
        self.assertTrue(backup.is_file())

        target_validation = validate_snapshot(
            self.target,
            expected_schema_version=14,
        )
        self.assertEqual(target_validation["status"], "valid")

        backup_validation = validate_snapshot(
            backup,
            expected_schema_version=13,
        )
        self.assertEqual(backup_validation["schema_version"], 13)

    def test_restore_rejects_invalid_snapshot_without_changing_target(self):
        self._create_market_db(self.target, schema_version=13)

        with self.assertRaises(MarketDatabaseRestoreError):
            restore_snapshot(
                self.root / "missing.db",
                self.target,
            )

        validation = validate_snapshot(
            self.target,
            expected_schema_version=13,
        )
        self.assertEqual(validation["schema_version"], 13)

    def test_restore_rejects_target_with_wal_sidecar(self):
        self._create_market_db(self.target)
        Path(f"{self.target}-wal").write_bytes(b"")

        with self.assertRaises(MarketDatabaseRestoreError):
            restore_snapshot(self.snapshot, self.target)

    def test_restore_does_not_overwrite_existing_backup(self):
        self._create_market_db(self.target, schema_version=13)
        create_snapshot(
            self.source,
            self.snapshot,
            expected_schema_version=14,
        )

        existing_backup = self.target.with_name(
            f"{self.target.name}.pre-restore.bak"
        )
        existing_backup.write_bytes(b"KEEP_EXISTING_BACKUP")

        restore_snapshot(
            self.snapshot,
            self.target,
            expected_schema_version=14,
        )

        self.assertEqual(
            existing_backup.read_bytes(),
            b"KEEP_EXISTING_BACKUP",
        )



    def test_create_snapshot_closes_source_when_destination_connect_fails(self):
        real_connect = sqlite3.connect
        connections = []

        class ConnectionProxy:
            def __init__(self, connection):
                self.connection = connection
                self.closed = False

            def backup(self, destination):
                return self.connection.backup(destination)

            def commit(self):
                return self.connection.commit()

            def close(self):
                self.closed = True
                return self.connection.close()

        def tracked_connect(*args, **kwargs):
            if not connections:
                connection = ConnectionProxy(real_connect(*args, **kwargs))
                connections.append(connection)
                return connection

            raise sqlite3.OperationalError(
                "simulated destination connection failure"
            )

        with mock.patch(
            "trendradar.market_data.database_snapshot.sqlite3.connect",
            side_effect=tracked_connect,
        ):
            with self.assertRaises(MarketDatabaseSnapshotError):
                create_snapshot(self.source, self.snapshot)

        self.assertEqual(len(connections), 1)
        self.assertTrue(
            connections[0].closed,
            "Source SQLite connection should be closed",
        )

    def test_restore_preserves_target_when_backup_copy_is_corrupted(self):
        self._create_market_db(self.target, schema_version=13)
        original_bytes = self.target.read_bytes()

        create_snapshot(
            self.source,
            self.snapshot,
            expected_schema_version=14,
        )

        real_replace = __import__("os").replace

        def failing_replace(source, destination):
            if Path(destination) == self.target:
                raise OSError("simulated target replacement failure")
            return real_replace(source, destination)

        with mock.patch(
            "trendradar.market_data.database_snapshot.os.replace",
            side_effect=failing_replace,
        ):
            with self.assertRaises(MarketDatabaseRestoreError):
                restore_snapshot(
                    self.snapshot,
                    self.target,
                    expected_schema_version=14,
                )

        self.assertEqual(self.target.read_bytes(), original_bytes)

    def test_restore_rejects_target_with_shm_sidecar(self):
        self._create_market_db(self.target)
        Path(f"{self.target}-shm").write_bytes(b"")

        with self.assertRaises(MarketDatabaseRestoreError):
            restore_snapshot(self.snapshot, self.target)


    def test_restore_rejects_same_snapshot_and_target(self):
        with self.assertRaises(MarketDatabaseRestoreError):
            restore_snapshot(self.source, self.source)


if __name__ == "__main__":
    unittest.main()
