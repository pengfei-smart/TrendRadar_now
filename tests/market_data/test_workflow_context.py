"""Tests for the independent market workflow context."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch, call

from trendradar.market_data.workflow_context import MarketWorkflowContext


class MarketWorkflowContextTests(unittest.TestCase):

    def setUp(self):
        self.guard = Mock()
        self.app_context = Mock()
        self.app_context.get_ai_request_guard.return_value = self.guard

    def create_context(self, config=None, **kwargs):
        if config is None:
            config = {}

        with patch(
            "trendradar.market_data.workflow_context.AppContext",
            return_value=self.app_context,
        ):
            return MarketWorkflowContext(config, **kwargs)

    def test_generates_run_id_when_not_supplied(self):
        context = self.create_context()

        self.assertTrue(context.run_id)
        self.assertEqual(len(context.run_id), 32)

    def test_preserves_explicit_run_id(self):
        context = self.create_context(run_id="market-run-001")

        self.assertEqual(context.run_id, "market-run-001")

    def test_rejects_empty_run_id(self):
        with self.assertRaisesRegex(ValueError, "run_id"):
            self.create_context(run_id="")

    def test_rejects_non_string_run_id(self):
        with self.assertRaisesRegex(ValueError, "run_id"):
            self.create_context(run_id=123)

    def test_rejects_empty_remote_object_key(self):
        with self.assertRaisesRegex(ValueError, "remote_object_key"):
            self.create_context(remote_object_key="")

    def test_rejects_object_key_containing_only_slashes(self):
        with self.assertRaisesRegex(ValueError, "remote_object_key"):
            self.create_context(remote_object_key="///")

    def test_local_configuration_does_not_create_remote_backend(self):
        config = {
            "STORAGE": {
                "BACKEND": "auto",
                "REMOTE": {},
            }
        }

        with patch(
            "trendradar.market_data.workflow_context.AppContext",
            return_value=self.app_context,
        ), patch(
            "trendradar.market_data.workflow_context.MarketDatabaseBackend"
        ) as backend:
            context = MarketWorkflowContext(config)

        self.assertFalse(context.is_remote_database_enabled)
        self.assertIsNone(context.remote_database_backend)
        backend.assert_not_called()

    def test_incomplete_remote_configuration_does_not_create_backend(self):
        config = {
            "STORAGE": {
                "BACKEND": "remote",
                "REMOTE": {
                    "BUCKET_NAME": "trendradar-data",
                    "ENDPOINT_URL": "https://example.test",
                },
            }
        }

        with patch(
            "trendradar.market_data.workflow_context.AppContext",
            return_value=self.app_context,
        ), patch(
            "trendradar.market_data.workflow_context.MarketDatabaseBackend"
        ) as backend:
            context = MarketWorkflowContext(config)

        self.assertFalse(context.is_remote_database_enabled)
        backend.assert_not_called()

    def test_complete_remote_configuration_creates_backend(self):
        remote_config = {
            "BUCKET_NAME": "trendradar-data",
            "ENDPOINT_URL": "https://example.test",
            "ACCESS_KEY_ID": "test-access",
            "SECRET_ACCESS_KEY": "test-secret",
            "REGION": "",
        }
        config = {
            "STORAGE": {
                "BACKEND": "remote",
                "REMOTE": remote_config,
            }
        }

        with patch(
            "trendradar.market_data.workflow_context.AppContext",
            return_value=self.app_context,
        ), patch(
            "trendradar.market_data.workflow_context.MarketDatabaseBackend"
        ) as backend:
            context = MarketWorkflowContext(config)

        self.assertTrue(context.is_remote_database_enabled)
        backend.assert_called_once_with(
            remote_config,
            object_key="market/market.db",
            expected_schema_version=14,
        )

    def test_remote_backend_is_not_used_during_context_initialization(self):
        remote_config = {
            "BUCKET_NAME": "trendradar-data",
            "ENDPOINT_URL": "https://example.test",
            "ACCESS_KEY_ID": "test-access",
            "SECRET_ACCESS_KEY": "test-secret",
        }
        config = {
            "STORAGE": {
                "BACKEND": "remote",
                "REMOTE": remote_config,
            }
        }

        backend_instance = Mock()

        with patch(
            "trendradar.market_data.workflow_context.AppContext",
            return_value=self.app_context,
        ), patch(
            "trendradar.market_data.workflow_context.MarketDatabaseBackend",
            return_value=backend_instance,
        ):
            context = MarketWorkflowContext(config)

        self.assertIs(context.remote_database_backend, backend_instance)
        backend_instance.remote_database_exists.assert_not_called()
        backend_instance.download_database.assert_not_called()
        backend_instance.upload_database.assert_not_called()

    def test_reuses_application_ai_request_guard(self):
        context = self.create_context()

        first = context.get_ai_request_guard()
        second = context.get_ai_request_guard()

        self.assertIs(first, self.guard)
        self.assertIs(second, self.guard)
        self.app_context.get_ai_request_guard.assert_has_calls([call(), call()])

    def test_event_review_runtime_is_created_lazily_and_cached(self):
        config = {
            "AI": {
                "MODEL": "deepseek/example-model",
                "API_KEY": "test-secret",
            }
        }

        context = self.create_context(config)
        runtime = Mock()

        with patch(
            "trendradar.market_data.workflow_context.build_event_review_runtime",
            return_value=runtime,
        ) as builder:
            self.assertIsNone(context._event_review_runtime)

            first = context.get_event_review_runtime()
            second = context.get_event_review_runtime()

        self.assertIs(first, runtime)
        self.assertIs(second, runtime)
        builder.assert_called_once_with(
            config["AI"],
            request_guard=self.guard,
        )

    def test_event_review_runtime_rejects_missing_ai_config(self):
        context = self.create_context({})

        with self.assertRaisesRegex(ValueError, "AI configuration"):
            context.get_event_review_runtime()


    def test_storage_backend_mode_returns_configured_mode(self):
        context = MarketWorkflowContext({
            "STORAGE": {
                "BACKEND": "local",
                "REMOTE": {},
            }
        })

        self.assertEqual(context.storage_backend_mode, "local")

    def test_storage_backend_mode_rejects_unsupported_mode(self):
        context = MarketWorkflowContext({
            "STORAGE": {
                "BACKEND": "invalid",
                "REMOTE": {},
            }
        })

        with self.assertRaisesRegex(
            ValueError, "Unsupported storage backend"
        ):
            context.validate_storage_backend()

    def test_validate_storage_backend_accepts_local_mode(self):
        context = MarketWorkflowContext({
            "STORAGE": {
                "BACKEND": "local",
                "REMOTE": {},
            }
        })

        context.validate_storage_backend()

    def test_validate_storage_backend_rejects_incomplete_remote(self):
        context = MarketWorkflowContext({
            "STORAGE": {
                "BACKEND": "remote",
                "REMOTE": {
                    "BUCKET_NAME": "test-bucket",
                    "ENDPOINT_URL": "https://example.com",
                },
            }
        })

        with self.assertRaisesRegex(
            ValueError, "Remote storage is required"
        ):
            context.validate_storage_backend()

    def test_validate_storage_backend_accepts_complete_remote(self):
        context = MarketWorkflowContext({
            "STORAGE": {
                "BACKEND": "remote",
                "REMOTE": {
                    "BUCKET_NAME": "test-bucket",
                    "ENDPOINT_URL": "https://example.com",
                    "ACCESS_KEY_ID": "test-access-key",
                    "SECRET_ACCESS_KEY": "test-secret-key",
                },
            }
        })

        context.validate_storage_backend()

if __name__ == "__main__":
    unittest.main()
