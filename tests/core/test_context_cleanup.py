import unittest
from unittest.mock import Mock

from trendradar.context import AppContext


class AppContextCleanupTests(unittest.TestCase):
    def make_context(self, manager):
        context = AppContext.__new__(AppContext)
        context._storage_manager = manager
        return context

    def test_resource_cleanup_runs_when_retention_cleanup_fails(self):
        manager = Mock()
        manager.cleanup_old_data.side_effect = OSError("retention failed")
        context = self.make_context(manager)

        with self.assertRaisesRegex(OSError, "retention failed"):
            context.cleanup()

        manager.cleanup.assert_called_once()
        self.assertIsNone(context._storage_manager)

    def test_manager_reference_is_kept_when_resource_cleanup_fails(self):
        manager = Mock()
        manager.cleanup.side_effect = RuntimeError("resource cleanup failed")
        context = self.make_context(manager)

        with self.assertRaisesRegex(RuntimeError, "resource cleanup failed"):
            context.cleanup()

        self.assertIs(context._storage_manager, manager)

    def test_both_cleanup_errors_are_preserved(self):
        manager = Mock()
        manager.cleanup_old_data.side_effect = OSError("retention failed")
        manager.cleanup.side_effect = RuntimeError("resource failed")
        context = self.make_context(manager)

        with self.assertRaisesRegex(OSError, "retention failed") as caught:
            context.cleanup()

        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
        self.assertIn("resource failed", str(caught.exception.__cause__))
        self.assertIs(context._storage_manager, manager)

    def test_original_business_exception_is_preserved(self):
        manager = Mock()
        manager.cleanup.side_effect = OSError("cleanup failed")
        context = self.make_context(manager)

        business_error = ValueError("business failed")

        try:
            raise business_error
        except ValueError as original:
            with self.assertRaises(ValueError) as caught:
                context.cleanup()

        self.assertIs(caught.exception, business_error)
        self.assertIsInstance(caught.exception.__cause__, OSError)


if __name__ == "__main__":
    unittest.main()
