
"""Cloud object storage backend for the market SQLite database."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from trendradar.market_data.database_snapshot import (
    MarketDatabaseValidationError,
    validate_snapshot,
)

try:
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover - depends on optional environment
    boto3 = None
    BotoConfig = None
    ClientError = Exception


DEFAULT_OBJECT_KEY = "market/market.db"


class MarketDatabaseBackendError(RuntimeError):
    """Base error for market database remote storage."""


class MarketDatabaseConfigurationError(MarketDatabaseBackendError):
    """Remote storage configuration is incomplete."""


class MarketDatabaseNotFoundError(MarketDatabaseBackendError):
    """The remote market database does not exist."""


class MarketDatabaseRemoteError(MarketDatabaseBackendError):
    """Remote storage operation failed."""


class MarketDatabaseBackend:
    """S3-compatible remote storage for the market database."""

    def __init__(
        self,
        remote_config: dict[str, Any],
        *,
        object_key: str = DEFAULT_OBJECT_KEY,
        client: Any = None,
	expected_schema_version: int | None = 14,
    ) -> None:
        if not isinstance(remote_config, dict):
            raise MarketDatabaseConfigurationError(
                "远程存储配置必须是字典。"
            )

        self.object_key = object_key.strip("/")

        if (
            expected_schema_version is not None
            and (
                isinstance(expected_schema_version, bool)
                or not isinstance(expected_schema_version, int)
                or expected_schema_version < 1
            )
        ):
            raise MarketDatabaseConfigurationError(
                "expected_schema_version 必须是正整数或 None。"
            )

        self.expected_schema_version = expected_schema_version
        if not self.object_key:
            raise MarketDatabaseConfigurationError(
                "市场数据库对象键不能为空。"
            )

        self.bucket_name = str(
            remote_config.get("BUCKET_NAME") or ""
        ).strip()
        endpoint_url = str(
            remote_config.get("ENDPOINT_URL") or ""
        ).strip()
        access_key_id = str(
            remote_config.get("ACCESS_KEY_ID") or ""
        ).strip()
        secret_access_key = str(
            remote_config.get("SECRET_ACCESS_KEY") or ""
        ).strip()
        region = str(remote_config.get("REGION") or "").strip()

        missing = [
            name
            for name, value in (
                ("BUCKET_NAME", self.bucket_name),
                ("ENDPOINT_URL", endpoint_url),
                ("ACCESS_KEY_ID", access_key_id),
                ("SECRET_ACCESS_KEY", secret_access_key),
            )
            if not value
        ]
        if missing:
            raise MarketDatabaseConfigurationError(
                "远程存储配置缺少必要字段: " + ", ".join(missing)
            )

        if client is not None:
            self.client = client
            return

        if boto3 is None or BotoConfig is None:
            raise MarketDatabaseConfigurationError(
                "远程存储需要 boto3，请安装 boto3。"
            )

        use_sigv2 = (
            "myqcloud.com" in endpoint_url.lower()
            or "aliyuncs.com" in endpoint_url.lower()
        )
        signature_version = "s3" if use_sigv2 else "s3v4"

        config = BotoConfig(
            s3={"addressing_style": "virtual"},
            signature_version=signature_version,
            connect_timeout=10,
            read_timeout=60,
            retries={
                "max_attempts": 3,
                "mode": "standard",
            },
        )

        client_kwargs = {
            "endpoint_url": endpoint_url,
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "config": config,
        }
        if region:
            client_kwargs["region_name"] = region

        self.client = boto3.client("s3", **client_kwargs)

    @staticmethod
    def _is_not_found_error(exc: Exception) -> bool:
        if not isinstance(exc, ClientError):
            return False

        response = getattr(exc, "response", {})
        error = response.get("Error", {})
        metadata = response.get("ResponseMetadata", {})

        code = str(error.get("Code", ""))
        status = metadata.get("HTTPStatusCode")

        return code in {"404", "NoSuchKey", "NotFound"} or status == 404

    def remote_database_exists(self) -> bool:
        """Return False only when the object is confirmed missing."""
        try:
            self.client.head_object(
                Bucket=self.bucket_name,
                Key=self.object_key,
            )
            return True
        except Exception as exc:
            if self._is_not_found_error(exc):
                return False
            raise MarketDatabaseRemoteError(
                f"检查远程市场数据库失败: {exc}"
            ) from exc


    def download_database(self, destination: str | Path) -> dict[str, Any]:
        """Download and validate the remote database before replacing local data."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".download",
            dir=str(destination.parent),
        )
        os.close(fd)
        temp_path = Path(temp_name)

        try:
            try:
                self.client.download_file(
                    self.bucket_name,
                    self.object_key,
                    str(temp_path),
                )
            except Exception as exc:
                if self._is_not_found_error(exc):
                    raise MarketDatabaseNotFoundError(
                        f"远程市场数据库不存在: {self.object_key}"
                    ) from exc
                raise MarketDatabaseRemoteError(
                    f"下载远程市场数据库失败: {exc}"
                ) from exc

            if not temp_path.is_file() or temp_path.stat().st_size == 0:
                raise MarketDatabaseRemoteError(
                    "远程市场数据库下载结果为空。"
                )

            try:
                validation = validate_snapshot(
                    temp_path,
                    expected_schema_version=self.expected_schema_version,
                )
            except MarketDatabaseValidationError as exc:
                raise MarketDatabaseRemoteError(
                    f"远程市场数据库完整性验证失败，拒绝替换本地数据库: {exc}"
                ) from exc

            os.replace(temp_path, destination)

            return {
                "status": "success",
                "object_key": self.object_key,
                "destination": str(destination),
                "size_bytes": destination.stat().st_size,
                "schema_version": validation["schema_version"],
                "table_count": validation["table_count"],
            }
        finally:
            temp_path.unlink(missing_ok=True)


    def upload_database(self, snapshot_path: str | Path) -> dict[str, Any]:
        """Validate a SQLite snapshot before uploading it."""
        snapshot_path = Path(snapshot_path)

        if not snapshot_path.is_file():
            raise MarketDatabaseRemoteError(
                f"市场数据库快照不存在: {snapshot_path}"
            )

        if snapshot_path.stat().st_size == 0:
            raise MarketDatabaseRemoteError(
                "市场数据库快照为空，拒绝上传。"
            )

        try:
            validation = validate_snapshot(
                snapshot_path,
                expected_schema_version=self.expected_schema_version,
            )
        except MarketDatabaseValidationError as exc:
            raise MarketDatabaseRemoteError(
                f"市场数据库快照验证失败，拒绝上传: {exc}"
            ) from exc

        try:
            self.client.upload_file(
                str(snapshot_path),
                self.bucket_name,
                self.object_key,
            )
        except Exception as exc:
            raise MarketDatabaseRemoteError(
                f"上传市场数据库失败: {exc}"
            ) from exc

        return {
            "status": "success",
            "object_key": self.object_key,
            "size_bytes": snapshot_path.stat().st_size,
            "schema_version": validation["schema_version"],
            "table_count": validation["table_count"],
        }
