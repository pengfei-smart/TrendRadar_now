
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trendradar.ai.usage_ledger_factory import (
    clear_ai_usage_ledger_cache,
    get_ai_usage_ledger,
    resolve_ai_usage_db_path,
)


class AIUsageLedgerFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(clear_ai_usage_ledger_cache)

    def test_default_path(self):
        with patch.dict(os.environ, {}, clear=True):
            resolved = resolve_ai_usage_db_path()

        expected = (Path.cwd() / "data" / "ai_usage.db").resolve()
        self.assertEqual(expected, resolved)

    def test_environment_variable_path(self):
        configured_path = Path(self.temp_dir.name) / "custom.db"

        with patch.dict(
            os.environ,
            {"AI_USAGE_DB_PATH": str(configured_path)},
        ):
            resolved = resolve_ai_usage_db_path()

        self.assertEqual(configured_path.resolve(), resolved)

    def test_explicit_path_takes_precedence_over_environment(self):
        explicit_path = Path(self.temp_dir.name) / "explicit.db"
        environment_path = Path(self.temp_dir.name) / "environment.db"

        with patch.dict(
            os.environ,
            {"AI_USAGE_DB_PATH": str(environment_path)},
        ):
            resolved = resolve_ai_usage_db_path(explicit_path)

        self.assertEqual(explicit_path.resolve(), resolved)

    def test_same_path_returns_same_instance(self):
        path = Path(self.temp_dir.name) / "usage.db"

        first = get_ai_usage_ledger(path)
        second = get_ai_usage_ledger(path)

        self.assertIs(first, second)

    def test_different_paths_return_different_instances(self):
        first_path = Path(self.temp_dir.name) / "first.db"
        second_path = Path(self.temp_dir.name) / "second.db"

        first = get_ai_usage_ledger(first_path)
        second = get_ai_usage_ledger(second_path)

        self.assertIsNot(first, second)
        self.assertEqual(first_path.resolve(), first.path.resolve())
        self.assertEqual(second_path.resolve(), second.path.resolve())

    def test_equivalent_paths_share_same_instance(self):
        path = Path(self.temp_dir.name) / "usage.db"
        equivalent_path = path.parent / "." / path.name

        first = get_ai_usage_ledger(path)
        second = get_ai_usage_ledger(equivalent_path)

        self.assertIs(first, second)

    def test_environment_path_is_used_by_factory(self):
        path = Path(self.temp_dir.name) / "environment.db"

        with patch.dict(
            os.environ,
            {"AI_USAGE_DB_PATH": str(path)},
        ):
            ledger = get_ai_usage_ledger()

        self.assertEqual(path.resolve(), ledger.path.resolve())

    def test_clear_cache_creates_new_instance(self):
        path = Path(self.temp_dir.name) / "usage.db"

        first = get_ai_usage_ledger(path)
        clear_ai_usage_ledger_cache()
        second = get_ai_usage_ledger(path)

        self.assertIsNot(first, second)
        self.assertEqual(first.path.resolve(), second.path.resolve())


if __name__ == "__main__":
    unittest.main()
