"""Tests for the safe remote market database audit entry."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.market_data import audit_remote_security_master as audit


class RemoteDatabaseAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.destination = Path(self.temp_dir.name) / "market.audit.db"
        self.remote_config = {
            "BUCKET_NAME": "test-bucket",
            "ENDPOINT_URL": "https://storage.example.invalid",
            "ACCESS_KEY_ID": "test-access-key",
            "SECRET_ACCESS_KEY": "test-secret-key",
            "REGION": "auto",
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_downloads_to_new_destination_and_audits_without_upload(self):
        expected_report = {
            "status": "passed",
            "security_total": 10,
            "write_performed": False,
        }

        with (
            patch.object(audit, "MarketDatabaseBackend") as backend_class,
            patch.object(audit, "audit_database", return_value=expected_report) as audit_mock,
        ):
            backend = backend_class.return_value
            backend.download_database.return_value = {
                "status": "success",
                "schema_version": 14,
            }

            result = audit.download_and_audit_remote(
                self.remote_config,
                self.destination,
            )

        backend_class.assert_called_once_with(
            self.remote_config,
            object_key="market/market.db",
            expected_schema_version=14,
        )
        backend.download_database.assert_called_once_with(self.destination)
        audit_mock.assert_called_once_with(self.destination)
        backend.upload_database.assert_not_called()

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["remote_download"]["status"], "success")
        self.assertFalse(result["remote_upload_performed"])
        self.assertFalse(result["database_artifact_uploaded"])

    def test_refuses_to_overwrite_existing_destination(self):
        self.destination.write_bytes(b"preserve-existing-file")
        original = self.destination.read_bytes()

        with (
            patch.object(audit, "MarketDatabaseBackend") as backend_class,
            self.assertRaises(FileExistsError),
        ):
            audit.download_and_audit_remote(
                self.remote_config,
                self.destination,
            )

        backend_class.assert_not_called()
        self.assertEqual(self.destination.read_bytes(), original)

    def test_report_does_not_expose_remote_credentials(self):
        with (
            patch.object(audit, "MarketDatabaseBackend") as backend_class,
            patch.object(
                audit,
                "audit_database",
                return_value={"status": "passed"},
            ),
        ):
            backend = backend_class.return_value
            backend.download_database.return_value = {"status": "success"}

            result = audit.download_and_audit_remote(
                self.remote_config,
                self.destination,
            )

        serialized = repr(result)
        self.assertNotIn("test-access-key", serialized)
        self.assertNotIn("test-secret-key", serialized)
        self.assertNotIn("test-bucket", serialized)


    def test_report_file_is_valid_json_and_ends_with_newline(self):
        report_path = Path(self.temp_dir.name) / "audit-report.json"
        report = {"status": "passed", "security_total": 10}

        audit._write_report(report, report_path)

        content = report_path.read_text(encoding="utf-8")
        self.assertTrue(content.endswith("\n"))
        self.assertFalse(content.endswith("\\n"))
        self.assertEqual(json.loads(content), report)



if __name__ == "__main__":
    unittest.main()
