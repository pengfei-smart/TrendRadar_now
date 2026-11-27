
import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError

from trendradar.market_data.database_backend import (
    MarketDatabaseBackend,
    MarketDatabaseConfigurationError,
    MarketDatabaseNotFoundError,
    MarketDatabaseRemoteError,
)


def make_client_error(code, status):
    return ClientError(
        {
            "Error": {
                "Code": code,
                "Message": "test error",
            },
            "ResponseMetadata": {
                "HTTPStatusCode": status,
            },
        },
        "TestOperation",
    )

def make_valid_database_bytes():
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO schema_meta (key, value)
            VALUES ('schema_version', '14')
            """
        )
        connection.execute(
            """
            CREATE TABLE security_master (
                id INTEGER PRIMARY KEY
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE market_events (
                id INTEGER PRIMARY KEY
            )
            """
        )
        connection.commit()
        return connection.serialize()
    finally:
        connection.close()

class FakeS3Client:
    def __init__(self):
        self.head_error = None
        self.download_error = None
        self.upload_error = None
        self.download_content = make_valid_database_bytes()
        self.upload_calls = []

    def head_object(self, **kwargs):
        if self.head_error:
            raise self.head_error
        return {}

    def download_file(self, bucket, key, filename):
        if self.download_error:
            raise self.download_error
        Path(filename).write_bytes(self.download_content)

    def upload_file(self, filename, bucket, key):
        if self.upload_error:
            raise self.upload_error
        self.upload_calls.append((filename, bucket, key))


class MarketDatabaseBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.client = FakeS3Client()
        self.backend = MarketDatabaseBackend(
            {
                "BUCKET_NAME": "trendradar-data",
                "ENDPOINT_URL": "https://example.r2.cloudflarestorage.com",
                "ACCESS_KEY_ID": "test-access-key",
                "SECRET_ACCESS_KEY": "test-secret-key",
                "REGION": "",
            },
            client=self.client,
        )

    def path(self, name):
        return Path(self.temp_dir.name) / name

    def test_rejects_incomplete_configuration(self):
        with self.assertRaises(MarketDatabaseConfigurationError):
            MarketDatabaseBackend(
                {"BUCKET_NAME": "trendradar-data"},
                client=self.client,
            )

    def test_remote_database_exists(self):
        self.assertTrue(self.backend.remote_database_exists())

    def test_remote_database_missing_returns_false(self):
        self.client.head_error = make_client_error("NoSuchKey", 404)

        self.assertFalse(self.backend.remote_database_exists())

    def test_remote_database_forbidden_raises_error(self):
        self.client.head_error = make_client_error("403", 403)

        with self.assertRaises(MarketDatabaseRemoteError):
            self.backend.remote_database_exists()

    def test_remote_database_server_error_raises_error(self):
        self.client.head_error = make_client_error("500", 500)

        with self.assertRaises(MarketDatabaseRemoteError):
            self.backend.remote_database_exists()

    def test_download_database(self):
        destination = self.path("market.db")

        result = self.backend.download_database(destination)

        self.assertEqual(
            destination.read_bytes(),
            self.client.download_content,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["object_key"], "market/market.db")
        self.assertEqual(
            result["size_bytes"],
            len(self.client.download_content),
        )
        self.assertEqual(result["schema_version"], 14)

    def test_download_missing_database_raises_not_found(self):
        self.client.download_error = make_client_error(
            "NoSuchKey", 404
        )
        destination = self.path("market.db")

        with self.assertRaises(MarketDatabaseNotFoundError):
            self.backend.download_database(destination)

        self.assertFalse(destination.exists())

    def test_download_forbidden_raises_remote_error(self):
        self.client.download_error = make_client_error("403", 403)

        with self.assertRaises(MarketDatabaseRemoteError):
            self.backend.download_database(self.path("market.db"))

    def test_download_empty_file_is_rejected(self):
        self.client.download_content = b""

        with self.assertRaises(MarketDatabaseRemoteError):
            self.backend.download_database(self.path("market.db"))

    def test_download_failure_preserves_existing_database(self):
        destination = self.path("market.db")
        destination.write_bytes(b"old database")
        self.client.download_error = RuntimeError("network failure")

        with self.assertRaises(MarketDatabaseRemoteError):
            self.backend.download_database(destination)

        self.assertEqual(destination.read_bytes(), b"old database")

    def test_download_replace_failure_preserves_existing_database(self):
        destination = self.path("market.db")
        destination.write_bytes(b"old database")

        with patch(
            "trendradar.market_data.database_backend.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with self.assertRaises(OSError):
                self.backend.download_database(destination)

        self.assertEqual(destination.read_bytes(), b"old database")

    def test_upload_database(self):
        snapshot = self.path("snapshot.db")
        snapshot.write_bytes(make_valid_database_bytes())

        result = self.backend.upload_database(snapshot)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["object_key"], "market/market.db")
        self.assertEqual(len(self.client.upload_calls), 1)
        self.assertEqual(
            self.client.upload_calls[0][1:],
            ("trendradar-data", "market/market.db"),
        )

    def test_upload_missing_snapshot_is_rejected(self):
        with self.assertRaises(MarketDatabaseRemoteError):
            self.backend.upload_database(self.path("missing.db"))

        self.assertEqual(self.client.upload_calls, [])

    def test_upload_empty_snapshot_is_rejected(self):
        snapshot = self.path("empty.db")
        snapshot.write_bytes(b"")

        with self.assertRaises(MarketDatabaseRemoteError):
            self.backend.upload_database(snapshot)

        self.assertEqual(self.client.upload_calls, [])

    def test_upload_failure_raises_remote_error(self):
        snapshot = self.path("snapshot.db")
        snapshot.write_bytes(make_valid_database_bytes())
        self.client.upload_error = RuntimeError("network failure")

        with self.assertRaises(MarketDatabaseRemoteError):
            self.backend.upload_database(snapshot)


if __name__ == "__main__":
    unittest.main()
