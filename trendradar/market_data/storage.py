# 本文件负责独立市场SQLite初始化、版本迁移、事务写入与行情/事件持久化。
"""Standalone SQLite storage and safe schema migration for market radar."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from trendradar.market_data.models import MarketEvent, MappingEvidence, QuoteSnapshot, Security


class MarketStorage:
    """Local market DB storage with explicit transactions and compatibility migration."""

    SCHEMA_VERSION = 3

    def __init__(self, db_path: str = "output/market/market.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a configured connection and always close it after the transaction."""
        connection = sqlite3.connect(str(self.db_path), timeout=20)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=20000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}

    @classmethod
    def _add_missing_columns(cls, connection: sqlite3.Connection, table: str,
                             columns: dict[str, str]) -> None:
        present = cls._columns(connection, table)
        for name, definition in columns.items():
            if name not in present:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    @classmethod
    def _preflight_legacy_schema(cls, connection: sqlite3.Connection) -> None:
        """Add columns referenced by V2 indexes before applying the full schema script."""
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='security_master'"
        ).fetchone():
            cls._add_missing_columns(connection, "security_master", {
                "name_en": "TEXT NOT NULL DEFAULT ''", "board": "TEXT NOT NULL DEFAULT ''",
                "listing_date": "TEXT NOT NULL DEFAULT ''", "delisting_date": "TEXT NOT NULL DEFAULT ''",
                "source": "TEXT NOT NULL DEFAULT ''", "source_updated_at": "TEXT NOT NULL DEFAULT ''",
                "listing_status_source": "TEXT NOT NULL DEFAULT ''",
                "listing_status_updated_at": "TEXT NOT NULL DEFAULT ''",
                "first_seen_at": "TEXT NOT NULL DEFAULT ''", "last_seen_at": "TEXT NOT NULL DEFAULT ''",
            })
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sector_master'"
        ).fetchone():
            cls._add_missing_columns(connection, "sector_master", {
                "sector_type": "TEXT NOT NULL DEFAULT 'industry'", "provider": "TEXT NOT NULL DEFAULT ''",
                "source_url": "TEXT NOT NULL DEFAULT ''", "source_updated_at": "TEXT NOT NULL DEFAULT ''",
            })
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sector_membership'"
        ).fetchone():
            cls._add_missing_columns(connection, "sector_membership", {
                "source_url": "TEXT NOT NULL DEFAULT ''", "updated_at": "TEXT NOT NULL DEFAULT ''",
            })
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cross_market_map'"
        ).fetchone():
            cls._add_missing_columns(connection, "cross_market_map", {
                "us_ticker": "TEXT NOT NULL DEFAULT ''", "reviewer": "TEXT NOT NULL DEFAULT ''",
                "valid_from": "TEXT NOT NULL DEFAULT ''", "valid_to": "TEXT NOT NULL DEFAULT ''",
            })
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_events'"
        ).fetchone():
            cls._add_missing_columns(connection, "market_events", {
                "source_event_id": "TEXT NOT NULL DEFAULT ''", "language": "TEXT NOT NULL DEFAULT ''",
                "content_hash": "TEXT NOT NULL DEFAULT ''", "updated_at": "TEXT NOT NULL DEFAULT ''",
            })
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_candidates'"
        ).fetchone():
            cls._add_missing_columns(connection, "event_candidates", {
                "evidence_hash": "TEXT NOT NULL DEFAULT ''", "updated_at": "TEXT NOT NULL DEFAULT ''",
            })
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_snapshots'"
        ).fetchone():
            cls._add_missing_columns(connection, "market_snapshots", {
                "trading_date": "TEXT NOT NULL DEFAULT ''", "collected_at": "TEXT NOT NULL DEFAULT ''",
            })
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='radar_runs'"
        ).fetchone():
            cls._add_missing_columns(connection, "radar_runs", {
                "run_type": "TEXT NOT NULL DEFAULT 'snapshot'", "status": "TEXT NOT NULL DEFAULT 'started'",
                "quote_source": "TEXT NOT NULL DEFAULT ''", "quote_data_at": "TEXT NOT NULL DEFAULT ''",
                "duration_ms": "INTEGER NOT NULL DEFAULT 0",
            })

    @classmethod
    def _migrate_snapshot_uniqueness(cls, connection: sqlite3.Connection) -> None:
        """Rebuild legacy snapshots to permit multiple quote sources per timestamp."""
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='market_snapshots'"
        ).fetchone()
        if not row or "UNIQUE(code, timestamp, source)" in (row["sql"] or "").replace("\n", " "):
            return
        connection.execute("DROP INDEX IF EXISTS idx_market_snapshot_time")
        connection.execute("DROP INDEX IF EXISTS idx_market_snapshot_code_time")
        connection.execute("DROP INDEX IF EXISTS idx_market_snapshot_trading_date")
        connection.execute("ALTER TABLE market_snapshots RENAME TO market_snapshots_legacy")
        connection.execute("""CREATE TABLE market_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            trading_date TEXT NOT NULL DEFAULT '',
            price REAL,
            change_pct REAL,
            turnover REAL,
            volume_ratio REAL,
            source TEXT NOT NULL DEFAULT '',
            delayed INTEGER NOT NULL DEFAULT 1 CHECK (delayed IN (0, 1)),
            collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(code, timestamp, source),
            FOREIGN KEY (code) REFERENCES security_master(code) ON UPDATE CASCADE
        )""")
        connection.execute("""INSERT INTO market_snapshots
            (snapshot_id,code,timestamp,trading_date,price,change_pct,turnover,
             volume_ratio,source,delayed,collected_at)
            SELECT snapshot_id,code,timestamp,trading_date,price,change_pct,turnover,
                   volume_ratio,source,delayed,collected_at
            FROM market_snapshots_legacy""")
        connection.execute("DROP TABLE market_snapshots_legacy")
        connection.execute("CREATE INDEX idx_market_snapshot_time ON market_snapshots(timestamp DESC)")
        connection.execute("CREATE INDEX idx_market_snapshot_code_time ON market_snapshots(code, timestamp DESC)")
        connection.execute("CREATE INDEX idx_market_snapshot_trading_date ON market_snapshots(trading_date, code)")

    def _initialize(self) -> None:
        schema_path = Path(__file__).resolve().parent.parent / "storage" / "market_schema.sql"
        with self.connect() as connection:
            self._preflight_legacy_schema(connection)
            self._migrate_snapshot_uniqueness(connection)
            connection.executescript(schema_path.read_text(encoding="utf-8"))
            connection.execute(
                """INSERT INTO schema_meta(key,value) VALUES('schema_version',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP""",
                (str(self.SCHEMA_VERSION),),
            )
            # Legacy active flags came from a source without lifecycle status; invalidate unverified values.
            connection.execute(
                """UPDATE security_master SET listing_status='unknown',
                updated_at=CURRENT_TIMESTAMP
                WHERE listing_status='active' AND listing_status_source=''"""
            )

    def count_securities(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM security_master").fetchone()
            return int(row["count"])

    def upsert_securities(self, securities: Iterable[Security]) -> int:
        rows = [(s.code, s.name, s.exchange, s.listing_status, int(s.risk_flag)) for s in securities]
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO security_master(code,name,exchange,listing_status,risk_flag)
                VALUES(?,?,?,?,?)
                ON CONFLICT(code) DO UPDATE SET
                name=excluded.name,exchange=excluded.exchange,
                listing_status=CASE WHEN excluded.listing_status='unknown'
                    THEN security_master.listing_status ELSE excluded.listing_status END,
                risk_flag=excluded.risk_flag,
                last_seen_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP""", rows)
        return len(rows)


    def apply_listing_status_snapshots(self, snapshots: list[dict]) -> dict[str, int]:
        """Persist all validated exchange-board snapshots in one SQLite transaction."""
        allowed = {"active", "suspended", "delisted", "pending", "unknown"}
        if not snapshots:
            raise ValueError("上市状态快照不得为空")
        keys: set[tuple[str, str, str]] = set()
        for item in snapshots:
            source_id = item.get("source_id", "")
            exchange = item.get("exchange", "")
            board_scope = item.get("board_scope", "")
            fetched_at = item.get("fetched_at", "")
            statuses = item.get("statuses", {})
            if not source_id or exchange not in {"SH", "SZ"} or not board_scope:
                raise ValueError("上市状态快照必须提供来源、沪深交易所和板块范围")
            if not fetched_at or not statuses:
                raise ValueError("上市状态快照时间不能为空，且名单不得为空")
            key = (source_id, exchange, board_scope)
            if key in keys:
                raise ValueError("同一批次包含重复交易所/板块来源")
            keys.add(key)
            if any(len(code) != 6 or not code.isdigit() or status not in allowed
                   for code, status in statuses.items()):
                raise ValueError("上市状态快照包含非法证券代码或状态")
            for code in statuses:
                valid_board = (
                    (exchange == "SH" and board_scope == "main_board_a" and code.startswith("60"))
                    or (exchange == "SZ" and board_scope == "main_board_a"
                        and code.startswith(("000", "001", "002", "003")))
                    or (exchange == "SZ" and board_scope == "chinext"
                        and code.startswith(("300", "301")))
                )
                if not valid_board:
                    raise ValueError(f"证券代码与交易所/板块范围不匹配: {code}/{exchange}/{board_scope}")
        results: dict[str, int] = {}
        with self.connect() as connection:
            for item in snapshots:
                source_id = item["source_id"]
                exchange = item["exchange"]
                board_scope = item["board_scope"]
                fetched_at = item["fetched_at"]
                source_as_of = item.get("source_as_of", "")
                statuses = item["statuses"]
                latest = connection.execute(
                    """SELECT fetched_at FROM listing_status_snapshots
                    WHERE source_id=? AND exchange=? AND board_scope=?
                    ORDER BY fetched_at DESC LIMIT 1""",
                    (source_id, exchange, board_scope),
                ).fetchone()
                if latest and fetched_at <= latest["fetched_at"]:
                    raise ValueError("拒绝使用不新于当前有效快照的状态数据")
                cursor = connection.execute(
                    """INSERT INTO listing_status_snapshots
                    (source_id,exchange,board_scope,fetched_at,source_as_of,record_count,content_sha256)
                    VALUES(?,?,?,?,?,?,?)""",
                    (source_id, exchange, board_scope, fetched_at, source_as_of,
                     len(statuses), item.get("content_sha256", "")),
                )
                snapshot_id = cursor.lastrowid
                for code, status in statuses.items():
                    exists = connection.execute(
                        "SELECT 1 FROM security_master WHERE code=? AND exchange=?",
                        (code, exchange),
                    ).fetchone()
                    if not exists:
                        continue
                    connection.execute(
                        """INSERT INTO listing_status_evidence
                        (snapshot_id,code,listing_status,source_url,source_as_of)
                        VALUES(?,?,?,?,?)""",
                        (snapshot_id, code, status, item.get("source_url", ""), source_as_of),
                    )
                    connection.execute(
                        """UPDATE security_master SET listing_status=?,
                        listing_status_source=?,listing_status_updated_at=?,
                        updated_at=CURRENT_TIMESTAMP WHERE code=? AND exchange=?""",
                        (status, source_id, source_as_of or fetched_at, code, exchange),
                    )
                # A complete positive-membership snapshot can downgrade absent local records
                # to unknown, but absence is never sufficient evidence for delisting.
                known_rows = connection.execute(
                    "SELECT code FROM security_master WHERE exchange=?", (exchange,)
                ).fetchall()
                def in_scope(code: str) -> bool:
                    if exchange == "SH":
                        return board_scope == "main_board_a" and code.startswith("60")
                    if board_scope == "main_board_a":
                        return code.startswith(("000", "001", "002", "003"))
                    if board_scope == "chinext":
                        return code.startswith(("300", "301"))
                    return False
                for row in known_rows:
                    code = row["code"]
                    if in_scope(code) and code not in statuses:
                        connection.execute(
                            """INSERT INTO listing_status_evidence
                            (snapshot_id,code,listing_status,source_url,source_as_of)
                            VALUES(?,?,'unknown',?,?)""",
                            (snapshot_id, code, item.get("source_url", ""), source_as_of),
                        )
                        connection.execute(
                            """UPDATE security_master SET listing_status='unknown',
                            listing_status_source=?,listing_status_updated_at=?,
                            updated_at=CURRENT_TIMESTAMP WHERE code=? AND exchange=?""",
                            (source_id, source_as_of or fetched_at, code, exchange),
                        )
                results[f"{exchange}_{board_scope}"] = len(statuses)
        return results

    def apply_listing_status_snapshot(
        self, *, source_id: str, exchange: str, board_scope: str,
        fetched_at: str, source_as_of: str, statuses: dict[str, str],
        source_url: str = "", content_sha256: str = "",
    ) -> int:
        """Compatibility wrapper for applying a single validated listing snapshot."""
        result = self.apply_listing_status_snapshots([{
            "source_id": source_id, "exchange": exchange, "board_scope": board_scope,
            "fetched_at": fetched_at, "source_as_of": source_as_of,
            "statuses": statuses, "source_url": source_url,
            "content_sha256": content_sha256,
        }])
        return result[f"{exchange}_{board_scope}"]

    def save_events(self, events: Iterable[MarketEvent]) -> int:
        rows = [(e.event_id, e.source, e.title, e.published_at, e.event_type,
                 e.direction, e.region, e.url, e.summary) for e in events]
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO market_events
                (event_id,source,title,published_at,event_type,direction,region,url,summary)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_id) DO UPDATE SET
                title=excluded.title,published_at=excluded.published_at,
                event_type=excluded.event_type,direction=excluded.direction,
                summary=excluded.summary,updated_at=CURRENT_TIMESTAMP""", rows)
        return len(rows)

    def save_mapping_evidence(self, event_id: str, evidence: Iterable[MappingEvidence]) -> int:
        rows = [(event_id, e.target_code, e.mapping_type, e.confidence, e.evidence,
                 e.source, e.source_url, e.direction, int(e.verified)) for e in evidence]
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO event_candidates
                (event_id,target_code,mapping_type,confidence,evidence,source,source_url,direction,verified)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_id,target_code,mapping_type) DO UPDATE SET
                confidence=excluded.confidence,evidence=excluded.evidence,
                source=excluded.source,source_url=excluded.source_url,
                direction=excluded.direction,verified=excluded.verified,
                updated_at=CURRENT_TIMESTAMP""", rows)
        return len(rows)

    def save_quotes(self, quotes: Iterable[QuoteSnapshot]) -> int:
        rows = [(q.code, q.timestamp, q.price, q.change_pct, q.turnover,
                 q.volume_ratio, q.source, int(q.delayed)) for q in quotes]
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO market_snapshots
                (code,timestamp,price,change_pct,turnover,volume_ratio,source,delayed)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(code,timestamp,source) DO UPDATE SET
                price=excluded.price,change_pct=excluded.change_pct,
                turnover=excluded.turnover,volume_ratio=excluded.volume_ratio,
                delayed=excluded.delayed,collected_at=CURRENT_TIMESTAMP""", rows)
        return len(rows)

    def save_radar_run(self, run_at: str, candidate_count: int, pushed_count: int,
                       source_status: str, notes: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO radar_runs
                (run_at,candidate_count,pushed_count,source_status,notes)
                VALUES(?,?,?,?,?)""",
                (run_at,candidate_count,pushed_count,source_status,notes),
            )
