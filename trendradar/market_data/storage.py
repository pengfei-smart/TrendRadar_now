# 本文件负责独立市场SQLite初始化、版本迁移、事务写入与行情/事件持久化，属于市场数据库详细设计阶段。
"""Standalone SQLite storage and safe schema migration for market radar."""

import sqlite3
from pathlib import Path
from typing import Iterable

from trendradar.market_data.models import MarketEvent, MappingEvidence, QuoteSnapshot, Security


class MarketStorage:
    """Local market DB storage with explicit transactions and compatibility migration."""

    SCHEMA_VERSION = 2

    def __init__(self, db_path: str = "output/market/market.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=20000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

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
                "name_en": "TEXT NOT NULL DEFAULT ''",
                "board": "TEXT NOT NULL DEFAULT ''",
                "listing_date": "TEXT NOT NULL DEFAULT ''",
                "delisting_date": "TEXT NOT NULL DEFAULT ''",
                "source": "TEXT NOT NULL DEFAULT ''",
                "source_updated_at": "TEXT NOT NULL DEFAULT ''",
                "first_seen_at": "TEXT NOT NULL DEFAULT ''",
                "last_seen_at": "TEXT NOT NULL DEFAULT ''",
            })
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sector_master'"
        ).fetchone():
            cls._add_missing_columns(connection, "sector_master", {
                "sector_type": "TEXT NOT NULL DEFAULT 'industry'",
                "provider": "TEXT NOT NULL DEFAULT ''",
                "source_url": "TEXT NOT NULL DEFAULT ''",
                "source_updated_at": "TEXT NOT NULL DEFAULT ''",
            })
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sector_membership'"
        ).fetchone():
            cls._add_missing_columns(connection, "sector_membership", {
                "source_url": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            })
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cross_market_map'"
        ).fetchone():
            cls._add_missing_columns(connection, "cross_market_map", {
                "us_ticker": "TEXT NOT NULL DEFAULT ''",
                "reviewer": "TEXT NOT NULL DEFAULT ''",
                "valid_from": "TEXT NOT NULL DEFAULT ''",
                "valid_to": "TEXT NOT NULL DEFAULT ''",
            })
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_events'"
        ).fetchone():
            cls._add_missing_columns(connection, "market_events", {
                "source_event_id": "TEXT NOT NULL DEFAULT ''",
                "language": "TEXT NOT NULL DEFAULT ''",
                "content_hash": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            })
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_candidates'"
        ).fetchone():
            cls._add_missing_columns(connection, "event_candidates", {
                "evidence_hash": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            })
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_snapshots'"
        ).fetchone():
            cls._add_missing_columns(connection, "market_snapshots", {
                "trading_date": "TEXT NOT NULL DEFAULT ''",
                "collected_at": "TEXT NOT NULL DEFAULT ''",
            })
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='radar_runs'"
        ).fetchone():
            cls._add_missing_columns(connection, "radar_runs", {
                "run_type": "TEXT NOT NULL DEFAULT 'snapshot'",
                "status": "TEXT NOT NULL DEFAULT 'started'",
                "quote_source": "TEXT NOT NULL DEFAULT ''",
                "quote_data_at": "TEXT NOT NULL DEFAULT ''",
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

    def upsert_securities(self, securities: Iterable[Security]) -> int:
        rows = [(s.code, s.name, s.exchange, s.listing_status, int(s.risk_flag)) for s in securities]
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO security_master(code,name,exchange,listing_status,risk_flag)
                VALUES(?,?,?,?,?)
                ON CONFLICT(code) DO UPDATE SET
                name=excluded.name,exchange=excluded.exchange,
                listing_status=excluded.listing_status,risk_flag=excluded.risk_flag,
                last_seen_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP""", rows)
        return len(rows)

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
