# 本文件负责独立市场SQLite初始化、版本迁移、事务写入与行情/事件持久化。
"""Standalone SQLite storage and safe schema migration for market radar."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from trendradar.market_data.models import MarketEvent, MappingEvidence, QuoteSnapshot, Security


class MarketStorage:
    """Local market DB storage with explicit transactions and compatibility migration."""

    SCHEMA_VERSION = 5

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

    def _ensure_lifecycle_columns(self, connection: sqlite3.Connection) -> None:
        # 为旧数据库补齐 v4 可空生命周期字段。
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "security_master" not in tables:
            return
        existing = self._columns(connection, "security_master")
        for column in (
            "last_trading_date", "decision_issued_date", "decision_received_date",
            "delisting_effective_date", "transfer_market_code",
            "transfer_market_start_date", "successor_security_code",
            "delisting_method", "lifecycle_notes",
        ):
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE security_master ADD COLUMN {column} TEXT"
                )

    def _initialize(self) -> None:
        schema_path = Path(__file__).resolve().parent.parent / "storage" / "market_schema.sql"
        with self.connect() as connection:
            self._preflight_legacy_schema(connection)
            self._migrate_snapshot_uniqueness(connection)
            connection.executescript(schema_path.read_text(encoding="utf-8"))
            self._ensure_lifecycle_columns(connection)
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
                        and code.startswith(("300", "301", "302")))
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
                        return code.startswith(("300", "301", "302"))
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


    @staticmethod
    def _validate_enum(value: str, allowed: set[str], field: str) -> None:
        if value not in allowed:
            raise ValueError(f"{field} 不合法: {value!r}")

    def save_corporate_actions(self, actions: Iterable[dict]) -> int:
        """保存公司行动；以 corporate_action_id 幂等更新，证据等级保留审核状态。"""
        allowed_types = {"share_swap_merger", "cash_tender_offer", "asset_restructuring",
                         "delisted_then_relisted", "code_change", "spin_off", "other"}
        allowed_status = {"announced", "in_progress", "completed", "cancelled", "unknown"}
        allowed_evidence = {"official", "reliable_secondary", "unverified", "unknown"}
        rows = []
        for item in actions:
            action_id = item.get("corporate_action_id")
            action_type = item.get("action_type")
            if not action_id:
                raise ValueError("corporate_action_id 不能为空")
            self._validate_enum(action_type, allowed_types, "action_type")
            self._validate_enum(item.get("status", "unknown"), allowed_status, "status")
            self._validate_enum(item.get("evidence_grade", "unknown"), allowed_evidence, "evidence_grade")
            rows.append((
                action_id, action_type, item.get("action_name", ""),
                item.get("announced_date"), item.get("effective_date"),
                item.get("status", "unknown"), item.get("evidence_grade", "unknown"),
                item.get("source_title", ""), item.get("source_url", ""), item.get("notes", ""),
            ))
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO corporate_actions
                (corporate_action_id,action_type,action_name,announced_date,effective_date,
                 status,evidence_grade,source_title,source_url,notes)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(corporate_action_id) DO UPDATE SET
                action_type=excluded.action_type,action_name=excluded.action_name,
                announced_date=excluded.announced_date,effective_date=excluded.effective_date,
                status=excluded.status,evidence_grade=excluded.evidence_grade,
                source_title=excluded.source_title,source_url=excluded.source_url,
                notes=excluded.notes,updated_at=CURRENT_TIMESTAMP""", rows)
        return len(rows)

    def get_corporate_action(self, corporate_action_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM corporate_actions WHERE corporate_action_id=?",
                (corporate_action_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_corporate_actions(self, *, action_type: str | None = None,
                               status: str | None = None,
                               evidence_grade: str | None = None,
                               limit: int = 500) -> list[dict]:
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        clauses, params = [], []
        for field, value, allowed in (
            ("action_type", action_type, {"share_swap_merger", "cash_tender_offer",
             "asset_restructuring", "delisted_then_relisted", "code_change", "spin_off", "other"}),
            ("status", status, {"announced", "in_progress", "completed", "cancelled", "unknown"}),
            ("evidence_grade", evidence_grade, {"official", "reliable_secondary", "unverified", "unknown"}),
        ):
            if value is not None:
                self._validate_enum(value, allowed, field)
                clauses.append(f"{field}=?")
                params.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM corporate_actions" + where +
                " ORDER BY COALESCE(announced_date,'') DESC, corporate_action_id LIMIT ?",
                (*params, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def save_security_lifecycle_events(self, events: Iterable[dict]) -> int:
        """保存证券生命周期事件；外键和 CHECK 约束由 SQLite 最终兜底。"""
        event_types = {"listing", "trading_suspension", "trading_resumption",
                       "delisting_decision", "last_trading_day", "delisting_effective",
                       "transfer_market_entry", "relisting", "code_change", "status_correction", "other"}
        date_statuses = {"confirmed", "approximate", "pending", "unknown"}
        evidence_grades = {"official", "reliable_secondary", "unverified", "unknown"}
        rows = []
        for item in events:
            event_id, code, event_type = item.get("event_id"), item.get("security_code"), item.get("event_type")
            if not event_id or not code:
                raise ValueError("event_id 和 security_code 不能为空")
            self._validate_enum(event_type, event_types, "event_type")
            self._validate_enum(item.get("date_status", "unknown"), date_statuses, "date_status")
            self._validate_enum(item.get("evidence_grade", "unknown"), evidence_grades, "evidence_grade")
            rows.append((
                event_id, code, item.get("exchange", ""), event_type, item.get("event_date"),
                item.get("decision_issued_date"), item.get("decision_received_date"),
                item.get("last_trading_date"), item.get("delisting_effective_date"),
                item.get("transfer_market_code"), item.get("transfer_market_start_date"),
                item.get("date_status", "unknown"), item.get("evidence_grade", "unknown"),
                item.get("source_title", ""), item.get("source_url", ""),
                item.get("corporate_action_id"), item.get("notes", ""),
            ))
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO security_lifecycle_events
                (event_id,security_code,exchange,event_type,event_date,decision_issued_date,
                 decision_received_date,last_trading_date,delisting_effective_date,
                 transfer_market_code,transfer_market_start_date,date_status,evidence_grade,
                 source_title,source_url,corporate_action_id,notes)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_id) DO UPDATE SET
                security_code=excluded.security_code,exchange=excluded.exchange,
                event_type=excluded.event_type,event_date=excluded.event_date,
                decision_issued_date=excluded.decision_issued_date,
                decision_received_date=excluded.decision_received_date,
                last_trading_date=excluded.last_trading_date,
                delisting_effective_date=excluded.delisting_effective_date,
                transfer_market_code=excluded.transfer_market_code,
                transfer_market_start_date=excluded.transfer_market_start_date,
                date_status=excluded.date_status,evidence_grade=excluded.evidence_grade,
                source_title=excluded.source_title,source_url=excluded.source_url,
                corporate_action_id=excluded.corporate_action_id,notes=excluded.notes,
                updated_at=CURRENT_TIMESTAMP""", rows)
        return len(rows)

    def list_security_lifecycle_events(self, *, security_code: str | None = None,
                                       event_type: str | None = None,
                                       corporate_action_id: str | None = None,
                                       limit: int = 1000) -> list[dict]:
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        clauses, params = [], []
        if security_code is not None:
            clauses.append("security_code=?")
            params.append(security_code)
        if event_type is not None:
            self._validate_enum(event_type, {"listing", "trading_suspension", "trading_resumption",
                "delisting_decision", "last_trading_day", "delisting_effective",
                "transfer_market_entry", "relisting", "code_change", "status_correction", "other"}, "event_type")
            clauses.append("event_type=?")
            params.append(event_type)
        if corporate_action_id is not None:
            clauses.append("corporate_action_id=?")
            params.append(corporate_action_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM security_lifecycle_events" + where +
                " ORDER BY COALESCE(event_date,'') DESC,event_id LIMIT ?",
                (*params, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def save_security_relationships(self, relationships: Iterable[dict]) -> int:
        """保存证券关系；重复自然键不会覆盖其他 relationship_id。"""
        relationship_types = {"share_swap_merger", "cash_tender_offer", "same_entity_code_change",
                              "delisted_transfer", "delisted_then_relisted", "spin_off",
                              "merger_into", "restructuring_successor", "other"}
        evidence_grades = {"official", "reliable_secondary", "unverified", "unknown"}
        rows = []
        for item in relationships:
            rid = item.get("relationship_id")
            source, target, rel_type = (item.get("from_security_code"),
                                        item.get("to_security_code"), item.get("relationship_type"))
            if not rid or not source or not target:
                raise ValueError("relationship_id、from_security_code、to_security_code 不能为空")
            if source == target:
                raise ValueError("证券关系的起点和终点不能相同")
            self._validate_enum(rel_type, relationship_types, "relationship_type")
            self._validate_enum(item.get("evidence_grade", "unknown"), evidence_grades, "evidence_grade")
            successor = item.get("is_listed_successor", 0)
            if successor not in (0, 1, False, True):
                raise ValueError("is_listed_successor 必须为 0 或 1")
            rows.append((
                rid, item.get("corporate_action_id"), source, target, rel_type,
                item.get("effective_date"), item.get("ratio_from"), item.get("ratio_to"),
                int(successor), item.get("evidence_grade", "unknown"),
                item.get("source_title", ""), item.get("source_url", ""), item.get("notes", ""),
            ))
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO security_relationships
                (relationship_id,corporate_action_id,from_security_code,to_security_code,
                 relationship_type,effective_date,ratio_from,ratio_to,is_listed_successor,
                 evidence_grade,source_title,source_url,notes)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(relationship_id) DO UPDATE SET
                corporate_action_id=excluded.corporate_action_id,
                from_security_code=excluded.from_security_code,to_security_code=excluded.to_security_code,
                relationship_type=excluded.relationship_type,effective_date=excluded.effective_date,
                ratio_from=excluded.ratio_from,ratio_to=excluded.ratio_to,
                is_listed_successor=excluded.is_listed_successor,evidence_grade=excluded.evidence_grade,
                source_title=excluded.source_title,source_url=excluded.source_url,
                notes=excluded.notes,updated_at=CURRENT_TIMESTAMP""", rows)
        return len(rows)

    def list_security_relationships(self, *, from_security_code: str | None = None,
                                    to_security_code: str | None = None,
                                    corporate_action_id: str | None = None,
                                    relationship_type: str | None = None,
                                    limit: int = 1000) -> list[dict]:
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        clauses, params = [], []
        if from_security_code is not None:
            clauses.append("from_security_code=?")
            params.append(from_security_code)
        if to_security_code is not None:
            clauses.append("to_security_code=?")
            params.append(to_security_code)
        if corporate_action_id is not None:
            clauses.append("corporate_action_id=?")
            params.append(corporate_action_id)
        if relationship_type is not None:
            self._validate_enum(relationship_type, {"share_swap_merger", "cash_tender_offer",
                "same_entity_code_change", "delisted_transfer", "delisted_then_relisted",
                "spin_off", "merger_into", "restructuring_successor", "other"}, "relationship_type")
            clauses.append("relationship_type=?")
            params.append(relationship_type)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM security_relationships" + where +
                " ORDER BY COALESCE(effective_date,'') DESC,relationship_id LIMIT ?",
                (*params, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def save_lifecycle_evidence_candidates(self, candidates: Iterable[dict]) -> int:
        """保存待核验线索；采集行为绝不自动写入正式生命周期事实表。"""
        kinds = {"event", "corporate_action", "relationship"}
        date_statuses = {"confirmed", "approximate", "pending", "unknown"}
        evidence_grades = {"official", "reliable_secondary", "unverified", "unknown"}
        rows = []
        for item in candidates:
            candidate_id = str(item.get("candidate_id") or "").strip()
            source_name = str(item.get("source_name") or "").strip()
            source_title = str(item.get("source_title") or "").strip()
            source_url = str(item.get("source_url") or "").strip()
            retrieved_at = str(item.get("retrieved_at") or "").strip()
            content_sha256 = str(item.get("content_sha256") or "").lower().strip()
            if not all((candidate_id, source_name, source_title, source_url, retrieved_at)):
                raise ValueError("candidate_id、来源名称、标题、URL和采集时间不能为空")
            if len(content_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in content_sha256):
                raise ValueError("content_sha256 必须是64位十六进制SHA-256")
            self._validate_enum(item.get("candidate_kind"), kinds, "candidate_kind")
            self._validate_enum(item.get("date_status", "unknown"), date_statuses, "date_status")
            self._validate_enum(item.get("evidence_grade", "unknown"), evidence_grades, "evidence_grade")
            payload = item.get("payload_json", "{}")
            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            try:
                json.loads(payload)
            except (TypeError, ValueError) as exc:
                raise ValueError("payload_json 必须是有效JSON") from exc
            rows.append((
                candidate_id, item["candidate_kind"], item.get("security_code", ""),
                item.get("related_security_code", ""), item.get("exchange", ""),
                item.get("event_type", ""), item.get("action_type", ""),
                item.get("relationship_type", ""), item.get("event_date", ""),
                item.get("date_status", "unknown"), source_name, source_title, source_url,
                item.get("source_published_at", ""), retrieved_at, content_sha256,
                item.get("evidence_excerpt", ""), payload, item.get("evidence_grade", "unknown"),
            ))
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO lifecycle_evidence_candidates
                (candidate_id,candidate_kind,security_code,related_security_code,exchange,
                 event_type,action_type,relationship_type,event_date,date_status,source_name,
                 source_title,source_url,source_published_at,retrieved_at,content_sha256,
                 evidence_excerpt,payload_json,evidence_grade)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                candidate_kind=excluded.candidate_kind,security_code=excluded.security_code,
                related_security_code=excluded.related_security_code,exchange=excluded.exchange,
                event_type=excluded.event_type,action_type=excluded.action_type,
                relationship_type=excluded.relationship_type,event_date=excluded.event_date,
                date_status=excluded.date_status,source_name=excluded.source_name,
                source_title=excluded.source_title,source_url=excluded.source_url,
                source_published_at=excluded.source_published_at,retrieved_at=excluded.retrieved_at,
                evidence_excerpt=excluded.evidence_excerpt,payload_json=excluded.payload_json,
                evidence_grade=excluded.evidence_grade,
                review_status=CASE WHEN lifecycle_evidence_candidates.content_sha256
                    <> excluded.content_sha256 THEN 'pending'
                    ELSE lifecycle_evidence_candidates.review_status END,
                reviewer=CASE WHEN lifecycle_evidence_candidates.content_sha256
                    <> excluded.content_sha256 THEN '' ELSE lifecycle_evidence_candidates.reviewer END,
                reviewed_at=CASE WHEN lifecycle_evidence_candidates.content_sha256
                    <> excluded.content_sha256 THEN '' ELSE lifecycle_evidence_candidates.reviewed_at END,
                review_notes=CASE WHEN lifecycle_evidence_candidates.content_sha256
                    <> excluded.content_sha256 THEN '' ELSE lifecycle_evidence_candidates.review_notes END,
                content_sha256=excluded.content_sha256,updated_at=CURRENT_TIMESTAMP""",
                rows,
            )
        return len(rows)

    def list_lifecycle_evidence_candidates(
        self, *, review_status: str | None = None, candidate_kind: str | None = None,
        security_code: str | None = None, evidence_grade: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """按审核状态、类型、证券代码筛选候选线索。"""
        if not 1 <= limit <= 5000:
            raise ValueError("limit 必须在1到5000之间")
        clauses, params = [], []
        for field, value, allowed in (
            ("review_status", review_status,
             {"pending", "verified", "rejected", "needs_more_evidence"}),
            ("candidate_kind", candidate_kind, {"event", "corporate_action", "relationship"}),
            ("evidence_grade", evidence_grade,
             {"official", "reliable_secondary", "unverified", "unknown"}),
        ):
            if value is not None:
                self._validate_enum(value, allowed, field)
                clauses.append(f"{field}=?")
                params.append(value)
        if security_code is not None:
            clauses.append("security_code=?")
            params.append(security_code)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM lifecycle_evidence_candidates" + where +
                " ORDER BY CASE review_status WHEN 'pending' THEN 0 "
                "WHEN 'needs_more_evidence' THEN 1 ELSE 2 END, retrieved_at DESC, candidate_id LIMIT ?",
                (*params, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def review_lifecycle_evidence_candidate(
        self, candidate_id: str, *, review_status: str, reviewer: str,
        reviewed_at: str, review_notes: str = "",
    ) -> None:
        """只更新候选线索审核结果；verified 不等于自动晋升为正式事实。"""
        self._validate_enum(
            review_status, {"pending", "verified", "rejected", "needs_more_evidence"},
            "review_status",
        )
        if not candidate_id or not reviewer.strip() or not reviewed_at.strip():
            raise ValueError("candidate_id、审核人和审核时间不能为空")
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE lifecycle_evidence_candidates SET review_status=?,reviewer=?,
                reviewed_at=?,review_notes=?,updated_at=CURRENT_TIMESTAMP
                WHERE candidate_id=?""",
                (review_status, reviewer.strip(), reviewed_at.strip(), review_notes, candidate_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"生命周期证据候选不存在: {candidate_id}")

    def save_radar_run(self, run_at: str, candidate_count: int, pushed_count: int,
                       source_status: str, notes: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO radar_runs
                (run_at,candidate_count,pushed_count,source_status,notes)
                VALUES(?,?,?,?,?)""",
                (run_at,candidate_count,pushed_count,source_status,notes),
            )
