from __future__ import annotations

import shutil
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from trendradar.market_data.database_lifecycle import (
    MarketDatabaseLifecycle,
    MarketDatabaseLifecycleError,
    MarketDatabaseLockError,
)
from trendradar.market_data.database_snapshot import (
    create_snapshot,
    validate_snapshot,
)
from trendradar.market_data.storage import MarketStorage


class FakeBackend:
    """Offline backend used by lifecycle tests."""

    def __init__(
        self,
        remote_database: Path | None = None,
        *,
        exists: bool = False,
        fail_upload: bool = False,
        corrupt_download: bool = False,
        fail_exists: bool = False,
    ):
        self.remote_database = remote_database
        self.exists = exists
        self.fail_upload = fail_upload
        self.corrupt_download = corrupt_download
        self.fail_exists = fail_exists
        self.download_calls = 0
        self.upload_calls = 0

    def remote_database_exists(self):
        if self.fail_exists:
            raise RuntimeError("Simulated remote availability failure")
        return self.exists

    def download_database(self, destination):
        self.download_calls += 1
        destination = Path(destination)

        if self.corrupt_download:
            destination.write_bytes(b"not a sqlite database")
            return {"status": "downloaded"}

        if self.remote_database is None:
            raise RuntimeError("Remote database source is missing")

        return create_snapshot(
            self.remote_database,
            destination,
            expected_schema_version=14,
        )

    def upload_database(self, snapshot_path):
        self.upload_calls += 1

        if self.fail_upload:
            raise RuntimeError("Simulated remote upload failure")

        validate_snapshot(
            snapshot_path,
            expected_schema_version=14,
        )

        if self.remote_database is not None:
            shutil.copy2(snapshot_path, self.remote_database)

        return {"status": "uploaded"}


class MarketDatabaseLifecycleTests(unittest.TestCase):

    def test_release_lock_does_not_delete_replacement_lock_file(self):
        lifecycle = MarketDatabaseLifecycle(
            self.db_path,
            storage_mode="local",
            run_id="lock-identity-test",
        )

        lifecycle.start()

        original_stat = Path.stat

        def mismatched_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)

            if path == lifecycle.lock_path:
                return SimpleNamespace(
                    st_dev=result.st_dev,
                    st_ino=result.st_ino + 1,
                )

            return result

        with patch.object(
            Path,
            "stat",
            autospec=True,
            side_effect=mismatched_stat,
        ):
            lifecycle.abort()

        self.assertTrue(lifecycle.lock_path.exists())

        # The lock descriptor has been closed, so Windows can remove it.
        lifecycle.lock_path.unlink()

    def test_release_lock_logs_close_failure_and_still_removes_lock(self):
        lifecycle = MarketDatabaseLifecycle(
            self.db_path,
            storage_mode="local",
            run_id="lock-close-failure-test",
        )

        lifecycle.start()
        self.assertTrue(lifecycle.lock_path.exists())

        original_close = os.close

        def close_then_raise(fd):
            original_close(fd)
            raise OSError("Simulated lock close failure")

        with (
            patch(
                "trendradar.market_data.database_lifecycle.os.close",
                side_effect=close_then_raise,
            ),
            self.assertLogs(
                "trendradar.market_data.database_lifecycle",
                level="WARNING",
            ) as captured,
        ):
            lifecycle.abort()

        self.assertTrue(
            any(
                "Unable to close market database lock file descriptor"
                in message
                for message in captured.output
            )
        )
        self.assertFalse(lifecycle.lock_path.exists())
        self.assertIsNone(lifecycle._lock_fd)
        self.assertIsNone(lifecycle._lock_identity)

    def test_release_lock_logs_unlink_failure(self):
        lifecycle = MarketDatabaseLifecycle(
            self.db_path,
            storage_mode="local",
            run_id="lock-unlink-failure-test",
        )

        lifecycle.start()
        self.assertTrue(lifecycle.lock_path.exists())

        original_unlink = Path.unlink

        def fail_lock_unlink(path, *args, **kwargs):
            if path == lifecycle.lock_path:
                raise OSError("Simulated lock unlink failure")
            return original_unlink(path, *args, **kwargs)

        with (
            patch.object(
                Path,
                "unlink",
                autospec=True,
                side_effect=fail_lock_unlink,
            ),
            self.assertLogs(
                "trendradar.market_data.database_lifecycle",
                level="WARNING",
            ) as captured,
        ):
            lifecycle.abort()

        self.assertTrue(
            any(
                "Unable to remove market database lock file"
                in message
                for message in captured.output
            )
        )
        self.assertTrue(lifecycle.lock_path.exists())
        self.assertIsNone(lifecycle._lock_fd)
        self.assertIsNone(lifecycle._lock_identity)

        # Clean up the deliberately retained lock file.
        original_unlink(lifecycle.lock_path)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        self.db_path = self.root / "market.db"
        self.remote_path = self.root / "remote-market.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_database(self, path: Path):
        MarketStorage(str(path))

    def test_local_mode_never_uploads_even_with_backend(self):
        backend = FakeBackend(exists=False)

        lifecycle = MarketDatabaseLifecycle(
            self.db_path,
            backend=backend,
            storage_mode="local",
            run_id="local-test",
        )

        start_result = lifecycle.start()
        finish_result = lifecycle.finish()

        self.assertEqual(start_result["status"], "success")
        self.assertEqual(finish_result["persistence"], "local_only")
        self.assertEqual(backend.download_calls, 0)
        self.assertEqual(backend.upload_calls, 0)
        self.assertTrue(self.db_path.exists())

    def test_auto_mode_initializes_local_database_when_remote_missing(self):
        backend = FakeBackend(exists=False)

        lifecycle = MarketDatabaseLifecycle(
            self.db_path,
            backend=backend,
            storage_mode="auto",
            run_id="auto-local-test",
        )

        result = lifecycle.start()

        self.assertEqual(result["status"], "success")
        self.assertEqual(backend.download_calls, 0)
        self.assertTrue(self.db_path.exists())

        finish_result = lifecycle.finish()

        self.assertEqual(finish_result["persistence"], "remote")
        self.assertEqual(backend.upload_calls, 1)

    def test_remote_mode_restores_existing_remote_database(self):
        self.create_database(self.remote_path)

        backend = FakeBackend(
            remote_database=self.remote_path,
            exists=True,
        )

        lifecycle = MarketDatabaseLifecycle(
            self.db_path,
            backend=backend,
            storage_mode="remote",
            run_id="remote-restore-test",
        )

        result = lifecycle.start()

        self.assertEqual(result["status"], "success")
        self.assertEqual(backend.download_calls, 1)
        self.assertTrue(self.db_path.exists())

        validation = validate_snapshot(
            self.db_path,
            expected_schema_version=14,
        )
        self.assertEqual(validation["status"], "valid")

        lifecycle.finish()
        self.assertEqual(backend.upload_calls, 1)

    def test_remote_mode_requires_backend(self):
        with self.assertRaises(MarketDatabaseLifecycleError):
            MarketDatabaseLifecycle(
                self.db_path,
                backend=None,
                storage_mode="remote",
            )

    def test_second_lifecycle_cannot_acquire_same_database_lock(self):
        first = MarketDatabaseLifecycle(
            self.db_path,
            storage_mode="local",
            run_id="first-lock-owner",
        )
        second = MarketDatabaseLifecycle(
            self.db_path,
            storage_mode="local",
            run_id="second-lock-owner",
        )

        first.start()

        try:
            with self.assertRaises(MarketDatabaseLockError):
                second.start()
        finally:
            first.abort()

        # The first lifecycle released its lock.
        third = MarketDatabaseLifecycle(
            self.db_path,
            storage_mode="local",
            run_id="third-lock-owner",
        )
        third.start()
        third.abort()

    def test_corrupt_remote_download_does_not_replace_local_database(self):
        self.create_database(self.db_path)
        original_bytes = self.db_path.read_bytes()

        backend = FakeBackend(
            exists=True,
            corrupt_download=True,
        )

        lifecycle = MarketDatabaseLifecycle(
            self.db_path,
            backend=backend,
            storage_mode="remote",
            run_id="corrupt-download-test",
        )

        with self.assertRaises(Exception):
            lifecycle.start()

        self.assertEqual(self.db_path.read_bytes(), original_bytes)
        self.assertFalse(lifecycle.lock_path.exists())

    def test_remote_check_failure_releases_lock(self):
        backend = FakeBackend(fail_exists=True)

        lifecycle = MarketDatabaseLifecycle(
            self.db_path,
            backend=backend,
            storage_mode="auto",
            run_id="remote-check-failure",
        )

        with self.assertRaises(RuntimeError):
            lifecycle.start()

        self.assertFalse(lifecycle.lock_path.exists())

    def test_upload_failure_retains_snapshot_and_releases_lock(self):
        backend = FakeBackend(
            exists=False,
            fail_upload=True,
        )

        lifecycle = MarketDatabaseLifecycle(
            self.db_path,
            backend=backend,
            storage_mode="auto",
            run_id="upload-failure-test",
        )

        lifecycle.start()

        with self.assertRaises(RuntimeError):
            lifecycle.finish()

        retained_snapshot = lifecycle.retained_snapshot_path

        self.assertIsNotNone(retained_snapshot)
        self.assertTrue(retained_snapshot.exists())
        self.assertFalse(lifecycle.lock_path.exists())

        with self.assertRaises(MarketDatabaseLifecycleError):
            lifecycle.finish()

    def test_snapshot_cleanup_failure_keeps_remote_success(self):
        backend = FakeBackend(
            remote_database=self.remote_path,
            exists=False,
        )

        lifecycle = MarketDatabaseLifecycle(
            self.db_path,
            backend=backend,
            storage_mode="auto",
            run_id="snapshot-cleanup-test",
        )

        lifecycle.start()

        original_unlink = Path.unlink

        def fail_snapshot_unlink(path, *args, **kwargs):
            if path == lifecycle._snapshot_path:
                raise OSError("Simulated snapshot cleanup failure")
            return original_unlink(path, *args, **kwargs)

        with patch.object(
            Path,
            "unlink",
            autospec=True,
            side_effect=fail_snapshot_unlink,
        ):
            result = lifecycle.finish()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["persistence"], "remote")
        self.assertEqual(
            result["snapshot_cleanup"]["status"],
            "pending",
        )
        self.assertIn(
            "Simulated snapshot cleanup failure",
            result["snapshot_cleanup"]["error"],
        )

        self.assertEqual(backend.upload_calls, 1)
        self.assertTrue(self.remote_path.exists())

        retained_snapshot = lifecycle.retained_snapshot_path
        self.assertIsNotNone(retained_snapshot)
        self.assertTrue(retained_snapshot.exists())

        self.assertFalse(lifecycle.lock_path.exists())

    def test_abort_releases_lock(self):
        lifecycle = MarketDatabaseLifecycle(
            self.db_path,
            storage_mode="local",
            run_id="abort-test",
        )

        lifecycle.start()
        self.assertTrue(lifecycle.lock_path.exists())

        lifecycle.abort()

        self.assertFalse(lifecycle.lock_path.exists())

    def test_abort_terminates_lifecycle(self):
        lifecycle = MarketDatabaseLifecycle(
            self.db_path,
            storage_mode="local",
            run_id="abort-terminal-test",
        )

        lifecycle.start()
        lifecycle.abort()

        self.assertFalse(lifecycle.lock_path.exists())

        with self.assertRaises(MarketDatabaseLifecycleError):
            lifecycle.start()

    def test_invalid_run_id_is_rejected(self):
        invalid_run_ids = [
            "",
            "../escape",
            "bad/name",
            "bad\\name",
            "white space",
        ]

        for run_id in invalid_run_ids:
            with self.subTest(run_id=run_id):
                with self.assertRaises(ValueError):
                    MarketDatabaseLifecycle(
                        self.db_path,
                        storage_mode="local",
                        run_id=run_id,
                    )

    def test_lock_write_failure_cleans_up_lock_file(self):
        lifecycle = MarketDatabaseLifecycle(
            self.db_path,
            storage_mode="local",
            run_id="lock-write-failure",
        )

        with patch(
            "trendradar.market_data.database_lifecycle.os.write",
            side_effect=OSError("Simulated lock write failure"),
        ):
            with self.assertRaises(OSError):
                lifecycle.start()

        self.assertFalse(lifecycle.lock_path.exists())

if __name__ == "__main__":
    unittest.main()
