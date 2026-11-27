
import unittest
from unittest.mock import Mock

from trendradar.ai.request_guard import (
    AIRequestGuard,
    AIRequestNotAllowedError,
)


class AIRequestGuardTests(unittest.TestCase):

    def setUp(self):
        self.scheduler = Mock()
        self.scheduler.is_ai_allowed_now.return_value = True

    def test_allowed_when_timeline_allows(self):
        guard = AIRequestGuard(self.scheduler)

        self.assertTrue(guard.is_allowed())
        guard.check()

    def test_denied_when_timeline_disallows(self):
        self.scheduler.is_ai_allowed_now.return_value = False
        guard = AIRequestGuard(self.scheduler)

        self.assertFalse(guard.is_allowed())

        with self.assertRaises(AIRequestNotAllowedError):
            guard.check()

    def test_denied_when_scheduler_is_missing(self):
        guard = AIRequestGuard(None)

        self.assertFalse(guard.is_allowed())

        with self.assertRaises(AIRequestNotAllowedError):
            guard.check()

    def test_debug_flag_does_not_bypass_guard(self):
        self.scheduler.is_ai_allowed_now.return_value = False

        # DEBUG 本身不是授权条件
        guard = AIRequestGuard(self.scheduler)

        self.assertFalse(guard.is_allowed())

    def test_explicit_debug_bypass(self):
        self.scheduler.is_ai_allowed_now.return_value = False

        guard = AIRequestGuard(
            self.scheduler,
            debug_bypass_enabled=True,
        )

        self.assertTrue(guard.is_allowed())
        guard.check()


if __name__ == "__main__":
    unittest.main()
