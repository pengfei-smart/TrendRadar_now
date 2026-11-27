import unittest
from unittest.mock import Mock

from trendradar.storage.manager import StorageManager


class StorageManagerCleanupTests(unittest.TestCase):
    def make_manager(self, local, remote):
        manager = StorageManager.__new__(StorageManager)
        manager._backend = local
        manager._remote_backend = remote
        return manager

    def test_remote_cleanup_runs_when_local_cleanup_fails(self):
        local = Mock()
        remote = Mock()
        local.cleanup.side_effect = OSError("local cleanup failed")

        manager = self.make_manager(local, remote)

        with self.assertRaisesRegex(OSError, "local cleanup failed"):
            manager.cleanup()

        local.cleanup.assert_called_once()
        remote.cleanup.assert_called_once()

    def test_both_cleanup_errors_are_preserved(self):
        local = Mock()
        remote = Mock()
        local.cleanup.side_effect = OSError("local failed")
        remote.cleanup.side_effect = RuntimeError("remote failed")

        manager = self.make_manager(local, remote)

        with self.assertRaisesRegex(OSError, "local failed") as caught:
            manager.cleanup()

        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
        self.assertIn("remote failed", str(caught.exception.__cause__))

    def test_none_backend_is_ignored(self):
        remote = Mock()
        manager = self.make_manager(None, remote)

        manager.cleanup()

        remote.cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
