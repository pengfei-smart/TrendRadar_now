# 本文件负责独立市场数据库的初始化、事务写入和快照查询，属于逐文件开发清单第八项。
"""Standalone SQLite storage for market radar data."""

import sqlite3
from pathlib import Path
from typing import Iterable

from trendradar.market_data.models import MarketEvent, MappingEvidence, QuoteSnapshot, Security


class MarketStorage:
    """Local market DB storage with explicit transactions and schema validation."""

    def __init__(self, db_path: str = "output/market/market.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=20000")
        return connection

    def _initialize(self) -> None:
        schema_path = Path(__file__).resolve().parent.parent / "storage" / "market_schema.sql"
        with self.connect() as connection:
            connection.executescript(schema_path.read_text(encoding="utf-8"))

    def upsert_securities(self, securities: Iterable[Security]) -> int:
        """Persist security master rows before snapshots/mapping references."""
        rows = [(s.code, s.name, s.exchange, s.listing_status, int(s.risk_flag)) for s in securities]
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO security_master(code,name,exchange,listing_status,risk_flag)
                VALUES(?,?,?,?,?)
                ON CONFLICT(code) DO UPDATE SET
                name=excluded.name,exchange=excluded.exchange,
                listing_status=excluded.listing_status,risk_flag=excluded.risk_flag,
                updated_at=CURRENT_TIMESTAMP""", rows)
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
                title=excluded.title, published_at=excluded.published_at,
                event_type=excluded.event_type, direction=excluded.direction,
                summary=excluded.summary""", rows)
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
                direction=excluded.direction,verified=excluded.verified""", rows)
        return len(rows)

    def save_quotes(self, quotes: Iterable[QuoteSnapshot]) -> int:
        rows = [(q.code, q.timestamp, q.price, q.change_pct, q.turnover,
                 q.volume_ratio, q.source, int(q.delayed)) for q in quotes]
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO market_snapshots
                (code,timestamp,price,change_pct,turnover,volume_ratio,source,delayed)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(code,timestamp) DO UPDATE SET
                price=excluded.price,change_pct=excluded.change_pct,
                turnover=excluded.turnover,volume_ratio=excluded.volume_ratio,
                source=excluded.source,delayed=excluded.delayed""", rows)
        return len(rows)

    def save_radar_run(self, run_at: str, candidate_count: int, pushed_count: int,
                       source_status: str, notes: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO radar_runs
                (run_at,candidate_count,pushed_count,source_status,notes)
                VALUES(?,?,?,?,?)""",
                (run_at, candidate_count, pushed_count, source_status, notes),
            )
