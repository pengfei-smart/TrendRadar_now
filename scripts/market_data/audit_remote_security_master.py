"""Read-only audit for a downloaded TrendRadar market database."""

import json
import os
import sqlite3
import tempfile
from pathlib import Path

from trendradar.market_data.database_backend import MarketDatabaseBackend


EXPECTED_SCHEMA_VERSION = 14

REQUIRED_SECURITY_COLUMNS = {
    "code",
    "name",
    "exchange",
    "board",
    "listing_status",
    "listing_status_source",
    "listing_status_updated_at",
    "risk_flag",
}


def _board_scope(code: str, exchange: str) -> str:
    """Classify supported A-share boards using the production code rules."""
    code = str(code).strip()

    if exchange == "SH":
        return "科创板" if code.startswith(("688", "689")) else "沪主板"

    if exchange == "SZ":
        return "创业板" if code.startswith(("300", "301", "302")) else "深主板"

    if exchange == "BJ":
        return "北交所"

    return "其他"


def _read_schema_version(connection: sqlite3.Connection) -> int:
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    if "schema_meta" not in tables:
        raise ValueError("Market database schema_meta table is missing")

    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(schema_meta)")
    }

    if not {"key", "value"}.issubset(columns):
        raise ValueError("schema_meta is missing required key/value columns")

    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()

    if row is None:
        raise ValueError("Market database schema_version is missing")

    try:
        return int(row["value"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Market database schema_version is invalid") from exc


def _validate_security_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    if "security_master" not in tables:
        raise ValueError("security_master table is missing")

    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(security_master)")
    }

    missing = sorted(REQUIRED_SECURITY_COLUMNS - columns)
    if missing:
        raise ValueError(
            "security_master is missing required columns: "
            + ", ".join(missing)
        )


def audit_database(db_path: str | Path) -> dict:
    """Audit a market database through a strictly read-only SQLite connection."""
    path = Path(db_path).expanduser().resolve()

    if not path.is_file():
        raise FileNotFoundError(f"Market database does not exist: {path}")

    uri = path.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row

    try:
        connection.execute("PRAGMA query_only=ON")

        query_only = connection.execute(
            "PRAGMA query_only"
        ).fetchone()[0]

        if query_only != 1:
            raise RuntimeError("SQLite query_only could not be enabled")

        schema_version = _read_schema_version(connection)

        if schema_version != EXPECTED_SCHEMA_VERSION:
            raise ValueError(
                f"Schema version mismatch: expected "
                f"{EXPECTED_SCHEMA_VERSION}, got {schema_version}"
            )

        _validate_security_schema(connection)

        security_total = connection.execute(
            "SELECT COUNT(*) FROM security_master"
        ).fetchone()[0]

        status_rows = connection.execute("""
            SELECT listing_status, COUNT(*) AS count
            FROM security_master
            GROUP BY listing_status
            ORDER BY listing_status
        """).fetchall()

        source_rows = connection.execute("""
            SELECT
                CASE
                    WHEN TRIM(listing_status_source) = '' THEN '(empty)'
                    ELSE listing_status_source
                END AS source,
                COUNT(*) AS count
            FROM security_master
            GROUP BY
                CASE
                    WHEN TRIM(listing_status_source) = '' THEN '(empty)'
                    ELSE listing_status_source
                END
            ORDER BY source
        """).fetchall()

        exchange_rows = connection.execute("""
            SELECT exchange, COUNT(*) AS count
            FROM security_master
            GROUP BY exchange
            ORDER BY exchange
        """).fetchall()

        risk_rows = connection.execute("""
            SELECT risk_flag, COUNT(*) AS count
            FROM security_master
            GROUP BY risk_flag
            ORDER BY risk_flag
        """).fetchall()

        active_without_source = connection.execute("""
            SELECT COUNT(*)
            FROM security_master
            WHERE listing_status = 'active'
              AND TRIM(listing_status_source) = ''
        """).fetchone()[0]

        active_without_updated_at = connection.execute("""
            SELECT COUNT(*)
            FROM security_master
            WHERE listing_status = 'active'
              AND TRIM(listing_status_updated_at) = ''
        """).fetchone()[0]

        delisted_count = connection.execute("""
            SELECT COUNT(*)
            FROM security_master
            WHERE listing_status = 'delisted'
        """).fetchone()[0]

        eligible_rows = connection.execute("""
            SELECT code, exchange
            FROM security_master
            WHERE exchange IN ('SH', 'SZ')
              AND NOT (
                  exchange = 'SH'
                  AND (code GLOB '688*' OR code GLOB '689*')
              )
              AND listing_status = 'active'
              AND risk_flag = 0
            ORDER BY code
        """).fetchall()

        eligible_board_counts = {
            "沪主板": 0,
            "深主板": 0,
            "创业板": 0,
        }

        for row in eligible_rows:
            board = _board_scope(row["code"], row["exchange"])
            if board in eligible_board_counts:
                eligible_board_counts[board] += 1

        excluded_star_count = connection.execute("""
            SELECT COUNT(*)
            FROM security_master
            WHERE exchange = 'SH'
              AND (code GLOB '688*' OR code GLOB '689*')
        """).fetchone()[0]

        excluded_bj_count = connection.execute("""
            SELECT COUNT(*)
            FROM security_master
            WHERE exchange = 'BJ'
        """).fetchone()[0]

        risk_flagged_count = connection.execute("""
            SELECT COUNT(*)
            FROM security_master
            WHERE risk_flag <> 0
        """).fetchone()[0]

        return {
            "status": "passed",
            "schema_version": schema_version,
            "security_total": security_total,
            "listing_status_counts": {
                row["listing_status"]: row["count"]
                for row in status_rows
            },
            "listing_status_source_counts": {
                row["source"]: row["count"]
                for row in source_rows
            },
            "exchange_counts": {
                row["exchange"]: row["count"]
                for row in exchange_rows
            },
            "risk_flag_counts": {
                str(row["risk_flag"]): row["count"]
                for row in risk_rows
            },
            "active_without_listing_status_source_count": active_without_source,
            "active_without_listing_status_updated_at_count": active_without_updated_at,
            "delisted_master_count": delisted_count,
            "eligible_total": len(eligible_rows),
            "eligible_board_counts": eligible_board_counts,
            "excluded_star_count": excluded_star_count,
            "excluded_bj_count": excluded_bj_count,
            "risk_flagged_count": risk_flagged_count,
            "database_open_mode": "ro",
            "sqlite_query_only": True,
            "write_performed": False,
            "remote_upload_performed": False,
            "database_artifact_uploaded": False,
        }
    finally:
        connection.close()



def download_and_audit_remote(
    remote_config: dict,
    destination: str | Path,
) -> dict:
    """Download a remote snapshot to a new local path and audit it read-only.

    The destination must not exist. This prevents the backend's atomic
    replacement behavior from overwriting any pre-existing local file.
    """
    destination = Path(destination).expanduser().resolve()

    if destination.exists():
        raise FileExistsError(
            "Refusing to overwrite existing audit database: "
            + str(destination)
        )

    destination.parent.mkdir(parents=True, exist_ok=True)

    backend = MarketDatabaseBackend(
        remote_config,
        object_key="market/market.db",
        expected_schema_version=EXPECTED_SCHEMA_VERSION,
    )

    download_result = backend.download_database(destination)

    # Only the downloaded local snapshot is inspected. No upload or
    # market workflow lifecycle operation is invoked here.
    report = audit_database(destination)

    report["remote_download"] = {
        "status": download_result.get("status", "success"),
        "object_key": "market/market.db",
        "schema_version": download_result.get("schema_version"),
        "size_bytes": download_result.get("size_bytes"),
    }
    report["remote_download_performed"] = True
    report["remote_upload_performed"] = False
    report["database_artifact_uploaded"] = False

    return report



def _write_report(report: dict, report_path: str | Path | None) -> str:
    output = json.dumps(report, ensure_ascii=False, indent=2)

    if report_path:
        path = Path(report_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + "\n", encoding="utf-8")

    return output


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Read-only audit of a TrendRadar market database"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--database",
        help="Audit an existing local database",
    )
    mode.add_argument(
        "--remote",
        action="store_true",
        help="Download and audit the remote market/market.db snapshot",
    )
    parser.add_argument(
        "--report",
        help="Optional JSON report output path",
    )
    args = parser.parse_args()

    if not args.remote:
        report = audit_database(args.database)
        print(_write_report(report, args.report))
        return 0

    required_env = {
        "BUCKET_NAME": "S3_BUCKET_NAME",
        "ENDPOINT_URL": "S3_ENDPOINT_URL",
        "ACCESS_KEY_ID": "S3_ACCESS_KEY_ID",
        "SECRET_ACCESS_KEY": "S3_SECRET_ACCESS_KEY",
    }

    missing = [
        env_name
        for env_name in required_env.values()
        if not os.environ.get(env_name, "").strip()
    ]
    if missing:
        parser.error(
            "Remote audit configuration is incomplete; missing: "
            + ", ".join(missing)
        )

    remote_config = {
        config_name: os.environ[env_name]
        for config_name, env_name in required_env.items()
    }
    remote_config["REGION"] = os.environ.get("S3_REGION", "")

    try:
        with tempfile.TemporaryDirectory(
            prefix="trendradar-market-audit-"
        ) as temp_dir:
            database_path = Path(temp_dir) / "market.db"

            if database_path.exists():
                raise FileExistsError(
                    "Temporary audit database path unexpectedly exists"
                )

            report = download_and_audit_remote(
                remote_config,
                database_path,
            )

        # The temporary database has been removed before the report
        # is written. Only the JSON report is retained.
        print(_write_report(report, args.report))
        return 0

    except Exception as exc:
        # Do not print provider exception text: it may contain endpoint
        # or credential details. The exception type is safe to report.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "remote_upload_performed": False,
                    "database_artifact_uploaded": False,
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
