# 本文件负责独立市场SQLite初始化、版本迁移、事务写入与行情/事件持久化。
"""Standalone SQLite storage and safe schema migration for market radar."""

import hashlib
import json
import sqlite3
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from trendradar.market_data.models import MarketEvent, MappingEvidence, QuoteSnapshot, Security


class MarketStorage:
    """Local market DB storage with explicit transactions and compatibility migration."""

    SCHEMA_VERSION = 9

    def __init__(self, db_path: str = "output/market/market.db"):
        self.db_path = Path(db_path).expanduser().resolve()
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

    @contextmanager
    def _connection_scope(
        self, connection: sqlite3.Connection | None = None,
    ) -> Iterator[sqlite3.Connection]:
        """Reuse an outer transaction when supplied; otherwise own a normal transaction."""
        if connection is None:
            with self.connect() as active_connection:
                yield active_connection
        else:
            yield connection

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
                "sector_type": "TEXT NOT NULL DEFAULT 'industry'", "parent_sector_code": "TEXT NOT NULL DEFAULT ''",
                "provider": "TEXT NOT NULL DEFAULT ''", "source_url": "TEXT NOT NULL DEFAULT ''",
                "source_updated_at": "TEXT NOT NULL DEFAULT ''",
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


    def load_securities(self, *, eligible_only: bool = False) -> list[Security]:
        """Load the persisted security universe with currently valid sector memberships.

        The default returns the complete master universe so callers can inspect
        suspended, delisted, pending and unknown records. Set eligible_only=True
        to restrict it to active, non-risk-warning SH/SZ securities, excluding
        the STAR Market in line with eligible_securities().
        """
        query = """
            SELECT sm.code, sm.name, sm.exchange, sm.listing_status, sm.risk_flag,
                   GROUP_CONCAT(DISTINCT sector.sector_code) AS sector_codes,
                   GROUP_CONCAT(DISTINCT CASE WHEN sector.provider='akshare_sw'
                       THEN sector.sector_name END) AS sector_names
            FROM security_master AS sm
            LEFT JOIN sector_membership AS membership
                ON membership.security_code = sm.code
                AND (membership.valid_from = '' OR membership.valid_from <= date('now'))
                AND (membership.valid_to = '' OR membership.valid_to >= date('now'))
            LEFT JOIN sector_master AS sector
                ON sector.sector_code = membership.sector_code
        """
        params: tuple = ()
        if eligible_only:
            query += """
                WHERE sm.exchange IN ('SH', 'SZ')
                  AND NOT (sm.exchange = 'SH' AND sm.code GLOB '68[89]*')
                  AND sm.listing_status = 'active'
                  AND sm.risk_flag = 0
            """
        query += """
            GROUP BY sm.code, sm.name, sm.exchange, sm.listing_status, sm.risk_flag
            ORDER BY sm.code
        """
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            Security(
                code=row["code"],
                name=row["name"],
                exchange=row["exchange"],
                listing_status=row["listing_status"],
                risk_flag=bool(row["risk_flag"]),
                sectors=tuple(sorted(
                    set(filter(None, (row["sector_codes"] or "").split(",")))
                    | set(filter(None, (row["sector_names"] or "").split(",")))
                )),
            )
            for row in rows
        ]

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

    def _validate_actual_delisting_event(
        self, item: dict, security_code: str, *, connection: sqlite3.Connection | None = None,
    ) -> None:
        """实际退市事件必须绑定已审核通过且能直接证明实际日期的官方证据候选。"""
        from trendradar.market_data.neeq_termination_date_validation import (
            validate_neeq_date_mapping,
        )

        candidate_id = str(item.get("evidence_candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError("delisting_effective事件必须提供evidence_candidate_id")
        if connection is None:
            with self.connect() as active_connection:
                row = active_connection.execute(
                    "SELECT * FROM lifecycle_evidence_candidates WHERE candidate_id=?",
                    (candidate_id,),
                ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM lifecycle_evidence_candidates WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"实际退市事件证据候选不存在: {candidate_id}")
        candidate = dict(row)
        if candidate.get("review_status") != "verified":
            raise ValueError("实际退市事件证据候选必须先审核为verified")
        if candidate.get("candidate_kind") != "event":
            raise ValueError("实际退市事件必须关联event类型证据候选")
        if candidate.get("event_type") != "delisting_effective":
            raise ValueError("实际退市事件必须关联delisting_effective类型证据候选")
        if candidate.get("security_code") != security_code:
            raise ValueError("实际退市事件证券代码与证据候选不一致")
        event_date = item.get("delisting_effective_date")
        if not event_date:
            raise ValueError("正式实际退市事件必须提供delisting_effective_date")
        if not item.get("event_date") or item.get("event_date") != event_date:
            raise ValueError("delisting_effective事件的event_date必须与delisting_effective_date一致")
        if candidate.get("event_date") != event_date:
            raise ValueError("实际退市日期必须与已审核证据候选日期完全一致")
        try:
            payload = json.loads(candidate.get("payload_json") or "{}")
        except (TypeError, ValueError) as exc:
            raise ValueError("实际退市证据候选payload_json无效") from exc
        metadata = payload.get("metadata") if isinstance(payload, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        authority = str(metadata.get("source_authority") or "")
        trusted_sources = {
            "neeq_official": "neeq_official",
            "issuer_official": "issuer_official",
            "government_or_registry": "government_or_registry",
        }
        source_authority = trusted_sources.get(authority)
        if source_authority is None or candidate.get("source_name") != source_authority:
            raise ValueError("证据候选source_name必须与审核确认的可信source_authority一致")
        evidence = {
            "evidence_kind": metadata.get("evidence_kind"),
            "source_authority": source_authority,
            "evidence_status": metadata.get("evidence_status"),
            "metadata": metadata,
        }
        procedure = {
            "procedure_type": "final_termination",
            "date_semantics": metadata.get("date_semantics"),
            "event_date": event_date,
            "date_status": candidate.get("date_status"),
        }
        result = validate_neeq_date_mapping(
            "actual_termination", "delisting_date",
            procedure=procedure, evidence=evidence,
            relation_type="supports_procedure",
        )
        if not result.get("valid"):
            errors = "; ".join(x.get("message", "") for x in result.get("errors", []))
            raise ValueError(f"实际退市事件证据校验失败: {errors}")
        if candidate.get("evidence_grade") != "official":
            raise ValueError("实际退市事件证据候选的evidence_grade必须为official")
        if item.get("date_status") != "confirmed" or item.get("evidence_grade") != "official":
            raise ValueError("正式实际退市事件自身必须标记date_status=confirmed且evidence_grade=official")
        candidate_url = str(candidate.get("source_url") or "")
        if not candidate_url.startswith("https://"):
            raise ValueError("实际退市事件证据候选必须提供HTTPS官方来源URL")
        if item.get("source_url") != candidate_url:
            raise ValueError("正式实际退市事件source_url必须与已审核证据候选一致")

    def save_security_lifecycle_events(self, events: Iterable[dict]) -> int:
        """保存证券生命周期事件；外键和 CHECK 约束由 SQLite 最终兜底。"""
        event_types = {"listing", "trading_suspension", "trading_resumption",
                       "delisting_decision", "last_trading_day", "delisting_effective",
                       "transfer_market_entry", "relisting", "code_change", "status_correction", "other"}
        date_statuses = {"confirmed", "approximate", "pending", "unknown"}
        evidence_grades = {"official", "reliable_secondary", "unverified", "unknown"}
        events = list(events)
        rows = []
        for item in events:
            event_id, code, event_type = item.get("event_id"), item.get("security_code"), item.get("event_type")
            if not event_id or not code:
                raise ValueError("event_id 和 security_code 不能为空")
            self._validate_enum(event_type, event_types, "event_type")
            if event_type == "delisting_effective":
                self._validate_actual_delisting_event(item, code)
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
            # 在正式写入事务内再次核验，避免候选审核状态在预校验后发生变化。
            for item in events:
                if item.get("event_type") == "delisting_effective":
                    self._validate_actual_delisting_event(
                        item, item.get("security_code"), connection=connection,
                    )
                    existing_link = connection.execute(
                        """SELECT 1 FROM security_lifecycle_event_evidence
                        WHERE event_id=? LIMIT 1""",
                        (item["event_id"],),
                    ).fetchone()
                    if existing_link:
                        existing_event = connection.execute(
                            """SELECT security_code,event_type,event_date,delisting_effective_date
                            FROM security_lifecycle_events WHERE event_id=?""",
                            (item["event_id"],),
                        ).fetchone()
                        incoming_identity = (
                            item.get("security_code"), item.get("event_type"),
                            item.get("event_date"), item.get("delisting_effective_date"),
                        )
                        if existing_event is None or tuple(existing_event) != incoming_identity:
                            raise ValueError(
                                "已有证据关联的正式实际退市事件不可通过UPSERT修改证券身份或日期"
                            )
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
            connection.executemany(
                """INSERT INTO security_lifecycle_event_evidence
                (event_id,candidate_id,relation_type)
                VALUES(?,?,'supports_procedure')
                ON CONFLICT(event_id,candidate_id,relation_type) DO NOTHING""",
                [
                    (item["event_id"], str(item["evidence_candidate_id"]).strip())
                    for item in events
                    if item.get("event_type") == "delisting_effective"
                ],
            )
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

    def list_security_lifecycle_event_evidence(
        self, *, event_id: str | None = None, candidate_id: str | None = None,
    ) -> list[dict]:
        """查询正式生命周期事件与审核证据候选之间的持久化关联。"""
        clauses, params = [], []
        if event_id is not None:
            clauses.append("event_id=?")
            params.append(event_id)
        if candidate_id is not None:
            clauses.append("candidate_id=?")
            params.append(candidate_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM security_lifecycle_event_evidence" + where +
                " ORDER BY linked_at,event_id,candidate_id",
                params,
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

    def save_lifecycle_evidence_candidates(
        self, candidates: Iterable[dict], *, connection: sqlite3.Connection | None = None,
    ) -> int:
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
        with self._connection_scope(connection) as connection:
            # 已经支撑正式事件的证据候选视为冻结记录，防止后续更新改变历史证据含义。
            immutable_columns = (
                "candidate_kind", "security_code", "related_security_code", "exchange",
                "event_type", "action_type", "relationship_type", "event_date", "date_status",
                "source_name", "source_title", "source_url", "source_published_at",
                "content_sha256", "evidence_excerpt", "payload_json", "evidence_grade",
            )
            for row in rows:
                candidate_id = row[0]
                existing = connection.execute(
                    "SELECT " + ",".join(immutable_columns) +
                    " FROM lifecycle_evidence_candidates WHERE candidate_id=?",
                    (candidate_id,),
                ).fetchone()
                if existing is None:
                    continue
                linked = connection.execute(
                    """SELECT 1 FROM security_lifecycle_event_evidence
                    WHERE candidate_id=? LIMIT 1""",
                    (candidate_id,),
                ).fetchone()
                incoming = (
                    row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8],
                    row[9], row[10], row[11], row[12], row[13], row[15], row[16], row[17], row[18],
                )
                if linked and tuple(existing) != incoming:
                    raise ValueError("已被正式生命周期事件引用的证据候选不可修改证据内容")
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
                review_status=CASE WHEN (lifecycle_evidence_candidates.candidate_kind IS NOT excluded.candidate_kind
                    OR lifecycle_evidence_candidates.security_code IS NOT excluded.security_code
                    OR lifecycle_evidence_candidates.related_security_code IS NOT excluded.related_security_code
                    OR lifecycle_evidence_candidates.exchange IS NOT excluded.exchange
                    OR lifecycle_evidence_candidates.event_type IS NOT excluded.event_type
                    OR lifecycle_evidence_candidates.action_type IS NOT excluded.action_type
                    OR lifecycle_evidence_candidates.relationship_type IS NOT excluded.relationship_type
                    OR lifecycle_evidence_candidates.event_date IS NOT excluded.event_date
                    OR lifecycle_evidence_candidates.date_status IS NOT excluded.date_status
                    OR lifecycle_evidence_candidates.source_name IS NOT excluded.source_name
                    OR lifecycle_evidence_candidates.source_title IS NOT excluded.source_title
                    OR lifecycle_evidence_candidates.source_url IS NOT excluded.source_url
                    OR lifecycle_evidence_candidates.source_published_at IS NOT excluded.source_published_at
                    OR lifecycle_evidence_candidates.content_sha256 IS NOT excluded.content_sha256
                    OR lifecycle_evidence_candidates.evidence_excerpt IS NOT excluded.evidence_excerpt
                    OR lifecycle_evidence_candidates.payload_json IS NOT excluded.payload_json
                    OR lifecycle_evidence_candidates.evidence_grade IS NOT excluded.evidence_grade)
                    THEN 'pending' ELSE lifecycle_evidence_candidates.review_status END,
                reviewer=CASE WHEN (lifecycle_evidence_candidates.candidate_kind IS NOT excluded.candidate_kind
                    OR lifecycle_evidence_candidates.security_code IS NOT excluded.security_code
                    OR lifecycle_evidence_candidates.related_security_code IS NOT excluded.related_security_code
                    OR lifecycle_evidence_candidates.exchange IS NOT excluded.exchange
                    OR lifecycle_evidence_candidates.event_type IS NOT excluded.event_type
                    OR lifecycle_evidence_candidates.action_type IS NOT excluded.action_type
                    OR lifecycle_evidence_candidates.relationship_type IS NOT excluded.relationship_type
                    OR lifecycle_evidence_candidates.event_date IS NOT excluded.event_date
                    OR lifecycle_evidence_candidates.date_status IS NOT excluded.date_status
                    OR lifecycle_evidence_candidates.source_name IS NOT excluded.source_name
                    OR lifecycle_evidence_candidates.source_title IS NOT excluded.source_title
                    OR lifecycle_evidence_candidates.source_url IS NOT excluded.source_url
                    OR lifecycle_evidence_candidates.source_published_at IS NOT excluded.source_published_at
                    OR lifecycle_evidence_candidates.content_sha256 IS NOT excluded.content_sha256
                    OR lifecycle_evidence_candidates.evidence_excerpt IS NOT excluded.evidence_excerpt
                    OR lifecycle_evidence_candidates.payload_json IS NOT excluded.payload_json
                    OR lifecycle_evidence_candidates.evidence_grade IS NOT excluded.evidence_grade)
                    THEN '' ELSE lifecycle_evidence_candidates.reviewer END,
                reviewed_at=CASE WHEN (lifecycle_evidence_candidates.candidate_kind IS NOT excluded.candidate_kind
                    OR lifecycle_evidence_candidates.security_code IS NOT excluded.security_code
                    OR lifecycle_evidence_candidates.related_security_code IS NOT excluded.related_security_code
                    OR lifecycle_evidence_candidates.exchange IS NOT excluded.exchange
                    OR lifecycle_evidence_candidates.event_type IS NOT excluded.event_type
                    OR lifecycle_evidence_candidates.action_type IS NOT excluded.action_type
                    OR lifecycle_evidence_candidates.relationship_type IS NOT excluded.relationship_type
                    OR lifecycle_evidence_candidates.event_date IS NOT excluded.event_date
                    OR lifecycle_evidence_candidates.date_status IS NOT excluded.date_status
                    OR lifecycle_evidence_candidates.source_name IS NOT excluded.source_name
                    OR lifecycle_evidence_candidates.source_title IS NOT excluded.source_title
                    OR lifecycle_evidence_candidates.source_url IS NOT excluded.source_url
                    OR lifecycle_evidence_candidates.source_published_at IS NOT excluded.source_published_at
                    OR lifecycle_evidence_candidates.content_sha256 IS NOT excluded.content_sha256
                    OR lifecycle_evidence_candidates.evidence_excerpt IS NOT excluded.evidence_excerpt
                    OR lifecycle_evidence_candidates.payload_json IS NOT excluded.payload_json
                    OR lifecycle_evidence_candidates.evidence_grade IS NOT excluded.evidence_grade)
                    THEN '' ELSE lifecycle_evidence_candidates.reviewed_at END,
                review_notes=CASE WHEN (lifecycle_evidence_candidates.candidate_kind IS NOT excluded.candidate_kind
                    OR lifecycle_evidence_candidates.security_code IS NOT excluded.security_code
                    OR lifecycle_evidence_candidates.related_security_code IS NOT excluded.related_security_code
                    OR lifecycle_evidence_candidates.exchange IS NOT excluded.exchange
                    OR lifecycle_evidence_candidates.event_type IS NOT excluded.event_type
                    OR lifecycle_evidence_candidates.action_type IS NOT excluded.action_type
                    OR lifecycle_evidence_candidates.relationship_type IS NOT excluded.relationship_type
                    OR lifecycle_evidence_candidates.event_date IS NOT excluded.event_date
                    OR lifecycle_evidence_candidates.date_status IS NOT excluded.date_status
                    OR lifecycle_evidence_candidates.source_name IS NOT excluded.source_name
                    OR lifecycle_evidence_candidates.source_title IS NOT excluded.source_title
                    OR lifecycle_evidence_candidates.source_url IS NOT excluded.source_url
                    OR lifecycle_evidence_candidates.source_published_at IS NOT excluded.source_published_at
                    OR lifecycle_evidence_candidates.content_sha256 IS NOT excluded.content_sha256
                    OR lifecycle_evidence_candidates.evidence_excerpt IS NOT excluded.evidence_excerpt
                    OR lifecycle_evidence_candidates.payload_json IS NOT excluded.payload_json
                    OR lifecycle_evidence_candidates.evidence_grade IS NOT excluded.evidence_grade)
                    THEN '' ELSE lifecycle_evidence_candidates.review_notes END,
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
            linked = connection.execute(
                """SELECT 1 FROM security_lifecycle_event_evidence
                WHERE candidate_id=? LIMIT 1""",
                (candidate_id,),
            ).fetchone()
            if linked and review_status != "verified":
                raise ValueError("已被正式生命周期事件引用的证据候选不得降级或撤销verified状态")
            cursor = connection.execute(
                """UPDATE lifecycle_evidence_candidates SET review_status=?,reviewer=?,
                reviewed_at=?,review_notes=?,updated_at=CURRENT_TIMESTAMP
                WHERE candidate_id=?""",
                (review_status, reviewer.strip(), reviewed_at.strip(), review_notes, candidate_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"生命周期证据候选不存在: {candidate_id}")

    def save_neeq_issuer_identity_candidates(
        self, candidates: Iterable[dict], *, connection: sqlite3.Connection | None = None,
    ) -> int:
        """保存NEEQ公告元数据身份候选；即使审核为verified也不自动创建发行人主档。"""
        rows = []
        for item in candidates:
            candidate_id = str(item.get("identity_candidate_id") or "").strip()
            company_code = str(item.get("company_code") or "").strip()
            company_name = str(item.get("company_name") or "").strip()
            attachment_url = str(item.get("attachment_url") or "").strip()
            metadata_source_url = str(item.get("metadata_source_url") or "").strip()
            response_hash = str(item.get("metadata_response_sha256") or "").lower().strip()
            retrieved_at = str(item.get("retrieved_at") or "").strip()
            if not candidate_id or len(company_code) != 6 or not company_code.isdigit():
                raise ValueError("身份候选必须提供候选ID和六位数字company_code")
            if not company_name or not attachment_url.startswith("https://") or not metadata_source_url.startswith("https://"):
                raise ValueError("身份候选必须提供企业名称和HTTPS附件/元数据来源URL")
            if len(response_hash) != 64 or any(ch not in "0123456789abcdef" for ch in response_hash):
                raise ValueError("metadata_response_sha256 必须是64位十六进制SHA-256")
            if not retrieved_at:
                raise ValueError("retrieved_at 不能为空")
            raw_record = item.get("raw_record", {})
            if not isinstance(raw_record, dict):
                raise ValueError("raw_record 必须是对象")
            rows.append((
                candidate_id, company_code, company_name,
                str(item.get("disclosure_title") or ""),
                str(item.get("publish_date") or ""), attachment_url,
                metadata_source_url, response_hash, retrieved_at,
                json.dumps(raw_record, ensure_ascii=False, sort_keys=True),
            ))
        with self._connection_scope(connection) as connection:
            connection.executemany(
                """INSERT INTO neeq_issuer_identity_candidates
                (identity_candidate_id,company_code,company_name,disclosure_title,publish_date,
                 attachment_url,metadata_source_url,metadata_response_sha256,retrieved_at,raw_record_json)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(identity_candidate_id) DO UPDATE SET
                company_code=excluded.company_code,company_name=excluded.company_name,
                disclosure_title=excluded.disclosure_title,publish_date=excluded.publish_date,
                attachment_url=excluded.attachment_url,metadata_source_url=excluded.metadata_source_url,
                metadata_response_sha256=excluded.metadata_response_sha256,
                retrieved_at=excluded.retrieved_at,raw_record_json=excluded.raw_record_json,
                review_status=CASE WHEN neeq_issuer_identity_candidates.metadata_response_sha256
                    <> excluded.metadata_response_sha256 THEN 'pending'
                    ELSE neeq_issuer_identity_candidates.review_status END,
                reviewer=CASE WHEN neeq_issuer_identity_candidates.metadata_response_sha256
                    <> excluded.metadata_response_sha256 THEN '' ELSE neeq_issuer_identity_candidates.reviewer END,
                reviewed_at=CASE WHEN neeq_issuer_identity_candidates.metadata_response_sha256
                    <> excluded.metadata_response_sha256 THEN '' ELSE neeq_issuer_identity_candidates.reviewed_at END,
                review_notes=CASE WHEN neeq_issuer_identity_candidates.metadata_response_sha256
                    <> excluded.metadata_response_sha256 THEN '' ELSE neeq_issuer_identity_candidates.review_notes END,
                updated_at=CURRENT_TIMESTAMP""", rows)
        return len(rows)

    def save_neeq_lifecycle_candidate_batch(
        self, identity_candidates: Iterable[dict],
        lifecycle_candidates: Iterable[dict],
    ) -> dict[str, int | bool]:
        """Atomically persist both NEEQ review queues and verify committed rows by ID."""
        identity_rows = list(identity_candidates)
        lifecycle_rows = list(lifecycle_candidates)
        identity_ids = [str(row.get("identity_candidate_id") or "").strip() for row in identity_rows]
        lifecycle_ids = [str(row.get("candidate_id") or "").strip() for row in lifecycle_rows]
        if any(not value for value in identity_ids + lifecycle_ids):
            raise ValueError("NEEQ候选批次中的候选ID不得为空")

        with self.connect() as connection:
            identity_saved = self.save_neeq_issuer_identity_candidates(
                identity_rows, connection=connection,
            )
            lifecycle_saved = self.save_lifecycle_evidence_candidates(
                lifecycle_rows, connection=connection,
            )
            for table, column, expected_ids in (
                ("neeq_issuer_identity_candidates", "identity_candidate_id", identity_ids),
                ("lifecycle_evidence_candidates", "candidate_id", lifecycle_ids),
            ):
                if not expected_ids:
                    continue
                placeholders = ",".join("?" for _ in expected_ids)
                found = {
                    row[0] for row in connection.execute(
                        f"SELECT {column} FROM {table} WHERE {column} IN ({placeholders})",
                        expected_ids,
                    ).fetchall()
                }
                if found != set(expected_ids):
                    raise RuntimeError(f"NEEQ候选批次事务内回读不完整: {table}")

        # Verify again through a fresh connection after COMMIT, not the write transaction.
        with self.connect() as connection:
            for table, column, expected_ids in (
                ("neeq_issuer_identity_candidates", "identity_candidate_id", identity_ids),
                ("lifecycle_evidence_candidates", "candidate_id", lifecycle_ids),
            ):
                if not expected_ids:
                    continue
                placeholders = ",".join("?" for _ in expected_ids)
                found = {
                    row[0] for row in connection.execute(
                        f"SELECT {column} FROM {table} WHERE {column} IN ({placeholders})",
                        expected_ids,
                    ).fetchall()
                }
                if found != set(expected_ids):
                    raise RuntimeError(f"NEEQ候选提交后回读不完整: {table}")

        return {
            "identity_candidates_saved": identity_saved,
            "lifecycle_candidates_saved": lifecycle_saved,
            "readback_verified": True,
        }

    def list_neeq_issuer_identity_candidates(
        self, *, review_status: str | None = None, company_code: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        if not 1 <= limit <= 5000:
            raise ValueError("limit 必须在1到5000之间")
        clauses, params = [], []
        if review_status is not None:
            self._validate_enum(review_status, {"pending", "verified", "rejected", "needs_more_evidence"}, "review_status")
            clauses.append("review_status=?")
            params.append(review_status)
        if company_code is not None:
            if len(company_code) != 6 or not company_code.isdigit():
                raise ValueError("company_code 必须是六位数字")
            clauses.append("company_code=?")
            params.append(company_code)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM neeq_issuer_identity_candidates" + where +
                " ORDER BY CASE review_status WHEN 'pending' THEN 0 "
                "WHEN 'needs_more_evidence' THEN 1 ELSE 2 END,retrieved_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            result = [dict(row) for row in rows]
            for item in result:
                item["raw_record"] = json.loads(item.pop("raw_record_json"))
            return result

    def review_neeq_issuer_identity_candidate(
        self, identity_candidate_id: str, *, review_status: str,
        reviewer: str, reviewed_at: str, review_notes: str = "",
    ) -> None:
        """审核身份候选；verified仅表示候选证据通过审核，不自动晋升主档。"""
        self._validate_enum(
            review_status, {"pending", "verified", "rejected", "needs_more_evidence"},
            "review_status",
        )
        if not identity_candidate_id or not reviewer.strip() or not reviewed_at.strip():
            raise ValueError("候选ID、审核人和审核时间不能为空")
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE neeq_issuer_identity_candidates SET review_status=?,reviewer=?,
                reviewed_at=?,review_notes=?,updated_at=CURRENT_TIMESTAMP
                WHERE identity_candidate_id=?""",
                (review_status, reviewer.strip(), reviewed_at.strip(), review_notes, identity_candidate_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"NEEQ身份候选不存在: {identity_candidate_id}")

    def upsert_neeq_issuer_master(self, issuers: Iterable[dict]) -> int:
        """显式写入已核验的NEEQ发行人主档；不接受A股security_master外键或自动提升。"""
        rows = []
        for item in issuers:
            code = str(item.get("company_code") or "").strip()
            name = str(item.get("current_name") or "").strip()
            source_name = str(item.get("source_name") or "").strip()
            source_url = str(item.get("source_url") or "").strip()
            source_hash = str(item.get("source_sha256") or "").lower().strip()
            retrieved_at = str(item.get("source_retrieved_at") or "").strip()
            if len(code) != 6 or not code.isdigit() or not name:
                raise ValueError("NEEQ发行人主档必须提供六位company_code和current_name")
            if not source_name or not source_url.startswith("https://") or not retrieved_at:
                raise ValueError("NEEQ发行人主档必须提供官方来源名称、HTTPS URL和采集时间")
            if len(source_hash) != 64 or any(ch not in "0123456789abcdef" for ch in source_hash):
                raise ValueError("source_sha256 必须是64位十六进制SHA-256")
            self._validate_enum(item.get("market_layer", "unknown"), {"base", "innovation", "unknown"}, "market_layer")
            self._validate_enum(item.get("issuer_status", "unknown"), {"active", "delisting_arrangement", "terminated", "unknown"}, "issuer_status")
            rows.append((code, name, str(item.get("full_name") or ""),
                item.get("market_layer", "unknown"), item.get("issuer_status", "unknown"),
                source_name, source_url, source_hash, retrieved_at, str(item.get("notes") or "")))
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO neeq_issuer_master
                (company_code,current_name,full_name,market_layer,issuer_status,source_name,
                 source_url,source_sha256,source_retrieved_at,notes)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(company_code) DO UPDATE SET
                current_name=excluded.current_name,full_name=excluded.full_name,
                market_layer=excluded.market_layer,issuer_status=excluded.issuer_status,
                source_name=excluded.source_name,source_url=excluded.source_url,
                source_sha256=excluded.source_sha256,source_retrieved_at=excluded.source_retrieved_at,
                notes=excluded.notes,updated_at=CURRENT_TIMESTAMP""", rows)
        return len(rows)

    def save_neeq_issuer_aliases(self, aliases: Iterable[dict]) -> int:
        """保存NEEQ发行人名称别名；默认pending，只有人工核验后才可用于名称匹配。"""
        rows = []
        for item in aliases:
            alias_id = str(item.get("alias_id") or "").strip()
            code = str(item.get("company_code") or "").strip()
            name = str(item.get("alias_name") or "").strip()
            alias_type = item.get("alias_type", "historical_name")
            if not alias_id or len(code) != 6 or not code.isdigit() or not name:
                raise ValueError("别名必须提供alias_id、六位company_code和alias_name")
            self._validate_enum(alias_type, {"historical_name", "short_name", "full_name", "other"}, "alias_type")
            self._validate_enum(item.get("review_status", "pending"), {"pending", "verified", "rejected"}, "review_status")
            rows.append((alias_id, code, name, alias_type, str(item.get("valid_from") or ""),
                str(item.get("valid_to") or ""), item.get("review_status", "pending"),
                str(item.get("source_name") or ""), str(item.get("source_url") or ""),
                str(item.get("source_sha256") or ""), str(item.get("notes") or "")))
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO neeq_issuer_aliases
                (alias_id,company_code,alias_name,alias_type,valid_from,valid_to,review_status,
                 source_name,source_url,source_sha256,notes)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(alias_id) DO UPDATE SET
                alias_name=excluded.alias_name,alias_type=excluded.alias_type,
                valid_from=excluded.valid_from,valid_to=excluded.valid_to,
                review_status=excluded.review_status,source_name=excluded.source_name,
                source_url=excluded.source_url,source_sha256=excluded.source_sha256,
                notes=excluded.notes,updated_at=CURRENT_TIMESTAMP""", rows)
        return len(rows)

    def list_neeq_issuer_aliases(
        self, *, company_code: str | None = None, alias_name: str | None = None,
        review_status: str | None = None, limit: int = 1000,
    ) -> list[dict]:
        if not 1 <= limit <= 5000:
            raise ValueError("limit 必须在1到5000之间")
        clauses, params = [], []
        if company_code is not None:
            clauses.append("company_code=?")
            params.append(company_code)
        if alias_name is not None:
            clauses.append("alias_name=?")
            params.append(alias_name)
        if review_status is not None:
            self._validate_enum(review_status, {"pending", "verified", "rejected"}, "review_status")
            clauses.append("review_status=?")
            params.append(review_status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM neeq_issuer_aliases" + where +
                " ORDER BY valid_from DESC,alias_id LIMIT ?", (*params, limit)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_neeq_issuer(self, company_code: str) -> dict | None:
        if len(company_code) != 6 or not company_code.isdigit():
            raise ValueError("company_code 必须是六位数字")
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM neeq_issuer_master WHERE company_code=?", (company_code,)
            ).fetchone()
            return dict(row) if row else None


    @staticmethod
    def _neeq_sha256(value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return digest

    @staticmethod
    def _neeq_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def save_neeq_termination_cases(
        self, cases: Iterable[dict], *, connection: sqlite3.Connection | None = None,
    ) -> int:
        """幂等保存NEEQ终止挂牌调查案件；原始快照哈希不一致时拒绝覆盖。"""
        rows = []
        for item in cases:
            case_id = str(item.get("case_id") or "").strip()
            code = str(item.get("company_code") or "").strip()
            if not case_id or len(code) != 6 or not code.isdigit():
                raise ValueError("案件必须提供case_id和六位数字company_code")
            raw_record = item.get("raw_record")
            if not isinstance(raw_record, dict):
                raise ValueError("raw_record必须是JSON对象")
            raw_json = self._neeq_json(raw_record)
            raw_hash = self._neeq_sha256(raw_json)
            supplied_hash = str(item.get("raw_record_sha256") or "").strip().lower()
            if supplied_hash and supplied_hash != raw_hash:
                raise ValueError("raw_record_sha256与原始记录内容不一致")
            status = item.get("case_status", "pending_review")
            self._validate_enum(
                status,
                {"pending_review", "evidence_gathering", "ready_for_review",
                 "reviewed", "closed", "invalid"},
                "case_status",
            )
            rows.append((
                case_id, code, str(item.get("company_name_snapshot") or ""),
                str(item.get("source_record_id") or ""), str(item.get("source_api") or ""),
                str(item.get("interface_result_date") or ""),
                str(item.get("interface_create_at") or ""), raw_json, raw_hash, status,
            ))
        if not rows:
            return 0
        with self._connection_scope(connection) as connection:
            for row in rows:
                existing = connection.execute(
                    "SELECT raw_record_sha256 FROM neeq_termination_case WHERE case_id=?",
                    (row[0],),
                ).fetchone()
                if existing and existing["raw_record_sha256"] != row[8]:
                    raise ValueError(f"案件ID对应的原始快照发生变化，拒绝覆盖: {row[0]}")
            connection.executemany(
                """INSERT OR IGNORE INTO neeq_termination_case
                (case_id,company_code,company_name_snapshot,source_record_id,source_api,
                 interface_result_date,interface_create_at,raw_record_json,raw_record_sha256,case_status)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            for row in rows:
                found = connection.execute(
                    "SELECT raw_record_sha256 FROM neeq_termination_case WHERE case_id=?",
                    (row[0],),
                ).fetchone()
                if not found or found["raw_record_sha256"] != row[8]:
                    raise RuntimeError(f"案件提交后回读校验失败: {row[0]}")
        return len(rows)

    def save_neeq_termination_evidence(
        self, evidence: Iterable[dict], *, connection: sqlite3.Connection | None = None,
    ) -> int:
        """幂等保存证据元数据；相同ID但内容哈希不同则拒绝覆盖。"""
        rows = []
        for item in evidence:
            evidence_id = str(item.get("evidence_id") or "").strip()
            source_url = str(item.get("source_url") or "").strip()
            retrieved_at = str(item.get("retrieved_at") or "").strip()
            if not evidence_id or not source_url.startswith("https://") or not retrieved_at:
                raise ValueError("证据必须提供ID、HTTPS来源URL和采集时间")
            self._validate_enum(item.get("evidence_kind"), {
                "batch_notice", "formal_decision", "review_application", "review_decision",
                "supplementary_decision", "final_termination_notice", "transfer_exit_record",
                "issuer_notice", "sponsor_notice", "third_party_lead", "other",
            }, "evidence_kind")
            self._validate_enum(item.get("source_authority", "unknown"), {
                "neeq_official", "issuer_official", "sponsor_official",
                "government_or_registry", "reliable_secondary", "third_party", "unknown",
            }, "source_authority")
            self._validate_enum(item.get("evidence_status", "located"), {
                "located", "downloaded", "extracted", "crosschecked", "unverified", "rejected",
            }, "evidence_status")
            content_hash = str(item.get("content_sha256") or "").lower().strip()
            if content_hash and (
                len(content_hash) != 64 or any(ch not in "0123456789abcdef" for ch in content_hash)
            ):
                raise ValueError("content_sha256必须是64位十六进制SHA-256")
            metadata = item.get("metadata", {})
            if not isinstance(metadata, dict):
                raise ValueError("metadata必须是JSON对象")
            rows.append((
                evidence_id, item["evidence_kind"], item.get("source_authority", "unknown"),
                str(item.get("notice_number") or ""), str(item.get("document_title") or ""),
                source_url, str(item.get("published_date") or ""), retrieved_at, content_hash,
                item.get("evidence_status", "located"), str(item.get("content_excerpt") or ""),
                self._neeq_json(metadata),
            ))
        if not rows:
            return 0
        with self._connection_scope(connection) as connection:
            for row in rows:
                existing = connection.execute(
                    "SELECT source_url,content_sha256 FROM neeq_termination_evidence WHERE evidence_id=?",
                    (row[0],),
                ).fetchone()
                if existing and (
                    existing["source_url"] != row[5] or existing["content_sha256"] != row[8]
                ):
                    raise ValueError(f"证据ID内容冲突，拒绝覆盖: {row[0]}")
            connection.executemany(
                """INSERT OR IGNORE INTO neeq_termination_evidence
                (evidence_id,evidence_kind,source_authority,notice_number,document_title,
                 source_url,published_date,retrieved_at,content_sha256,evidence_status,
                 content_excerpt,metadata_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            for row in rows:
                found = connection.execute(
                    "SELECT source_url,content_sha256 FROM neeq_termination_evidence WHERE evidence_id=?",
                    (row[0],),
                ).fetchone()
                if not found or found["source_url"] != row[5] or found["content_sha256"] != row[8]:
                    raise RuntimeError(f"证据提交后回读校验失败: {row[0]}")
        return len(rows)

    def save_neeq_termination_procedures(
        self, procedures: Iterable[dict], *, connection: sqlite3.Connection | None = None,
    ) -> int:
        """保存程序节点；日期语义与日期核验状态必须显式分开。"""
        rows = []
        for item in procedures:
            procedure_id = str(item.get("procedure_id") or "").strip()
            case_id = str(item.get("case_id") or "").strip()
            if not procedure_id or not case_id:
                raise ValueError("程序节点必须提供procedure_id和case_id")
            self._validate_enum(item.get("procedure_type"), {
                "decision_issued", "decision_received", "review_filed", "review_accepted",
                "review_rejected", "supplementary_decision", "conditional_termination",
                "final_termination", "transfer_market_entry", "transfer_exit", "other",
            }, "procedure_type")
            self._validate_enum(item.get("date_semantics", "unknown"), {
                "decision_issued", "decision_received", "announcement", "conditional_termination",
                "last_trading_day", "actual_termination", "transfer_start", "transfer_exit", "unknown",
            }, "date_semantics")
            self._validate_enum(item.get("date_status", "unknown"), {
                "confirmed", "approximate", "pending", "unknown",
            }, "date_status")
            rows.append((
                procedure_id, case_id, item["procedure_type"], str(item.get("event_date") or ""),
                item.get("date_semantics", "unknown"), item.get("date_status", "unknown"),
                str(item.get("notes") or ""),
            ))
        if not rows:
            return 0
        with self._connection_scope(connection) as connection:
            connection.executemany(
                """INSERT OR IGNORE INTO neeq_termination_procedure
                (procedure_id,case_id,procedure_type,event_date,date_semantics,date_status,notes)
                VALUES(?,?,?,?,?,?,?)""",
                rows,
            )
            for row in rows:
                found = connection.execute(
                    "SELECT case_id,procedure_type,event_date,date_semantics,date_status "
                    "FROM neeq_termination_procedure WHERE procedure_id=?",
                    (row[0],),
                ).fetchone()
                if not found or tuple(found) != tuple(row[1:6]):
                    raise ValueError(f"程序节点ID冲突或案件不存在: {row[0]}")
        return len(rows)

    def link_neeq_termination_evidence(
        self, links: Iterable[dict], *, connection: sqlite3.Connection | None = None,
    ) -> int:
        """关联案件、证据及可选程序节点，外键约束负责拒绝孤立引用。"""
        rows = []
        for item in links:
            link_id = str(item.get("link_id") or "").strip()
            case_id = str(item.get("case_id") or "").strip()
            evidence_id = str(item.get("evidence_id") or "").strip()
            relation = item.get("relation_type", "supports_case")
            if not link_id or not case_id or not evidence_id:
                raise ValueError("证据关联必须提供link_id、case_id和evidence_id")
            self._validate_enum(
                relation, {"supports_case", "supports_procedure", "contradicts", "context_only"},
                "relation_type",
            )
            procedure_id = str(item.get("procedure_id") or "").strip() or None
            if relation == "supports_procedure" and not procedure_id:
                raise ValueError("supports_procedure关联必须提供procedure_id")
            rows.append((link_id, case_id, evidence_id, procedure_id, relation, str(item.get("notes") or "")))
        if not rows:
            return 0
        with self._connection_scope(connection) as connection:
            connection.executemany(
                """INSERT OR IGNORE INTO neeq_termination_evidence_link
                (link_id,case_id,evidence_id,procedure_id,relation_type,notes)
                VALUES(?,?,?,?,?,?)""",
                rows,
            )
            for row in rows:
                found = connection.execute(
                    "SELECT case_id,evidence_id,procedure_id,relation_type "
                    "FROM neeq_termination_evidence_link WHERE link_id=?",
                    (row[0],),
                ).fetchone()
                if not found or tuple(found) != tuple(row[1:5]):
                    raise ValueError(f"证据关联ID冲突或引用不存在: {row[0]}")
        return len(rows)

    def record_neeq_termination_evidence_search(
        self, search: dict, *, connection: sqlite3.Connection | None = None,
    ) -> int:
        """追加一次证据检索状态；不覆盖历史检索，也不确认实际终止挂牌事实。"""
        search_id = str(search.get("search_id") or "").strip()
        case_id = str(search.get("case_id") or "").strip()
        evidence_kind = search.get("evidence_kind")
        search_status = search.get("search_status")
        searched_at = str(search.get("searched_at") or "").strip()
        if not search_id or not case_id or not searched_at:
            raise ValueError("检索记录必须提供search_id、case_id和searched_at")
        self._validate_enum(evidence_kind, {
            "batch_notice", "formal_decision", "review_application", "review_decision",
            "supplementary_decision", "final_termination_notice", "transfer_exit_record",
            "issuer_notice", "sponsor_notice", "third_party_lead", "other",
        }, "evidence_kind")
        self._validate_enum(search_status, {
            "not_searched", "searched_not_found", "found_not_registered",
            "registered", "manually_verified",
        }, "search_status")
        try:
            parsed = datetime.fromisoformat(searched_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("missing timezone")
        except ValueError as exc:
            raise ValueError("searched_at必须是包含时区偏移的ISO 8601日期时间") from exc
        urls = search.get("source_urls", [])
        if not isinstance(urls, list) or any(
            not isinstance(url, str) or not url.startswith("https://") for url in urls
        ):
            raise ValueError("source_urls必须是HTTPS URL数组")
        row = (
            search_id, case_id, evidence_kind, search_status, searched_at,
            str(search.get("search_scope") or ""), str(search.get("search_notes") or ""),
            self._neeq_json(urls), str(search.get("reviewer") or ""),
        )
        with self._connection_scope(connection) as connection:
            if not connection.execute(
                "SELECT 1 FROM neeq_termination_case WHERE case_id=?", (case_id,),
            ).fetchone():
                raise ValueError(f"NEEQ案件不存在: {case_id}")
            connection.execute(
                """INSERT OR IGNORE INTO neeq_termination_evidence_search
                (search_id,case_id,evidence_kind,search_status,searched_at,search_scope,
                 search_notes,source_urls_json,reviewer)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                row,
            )
            found = connection.execute(
                """SELECT case_id,evidence_kind,search_status,searched_at,search_scope,
                          search_notes,source_urls_json,reviewer
                FROM neeq_termination_evidence_search WHERE search_id=?""",
                (search_id,),
            ).fetchone()
            if not found or tuple(found) != tuple(row[1:]):
                raise ValueError(f"检索记录ID冲突或案件不存在: {search_id}")
        return 1

    def list_neeq_termination_evidence_searches(
        self, *, case_id: str | None = None, evidence_kind: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """查询证据检索历史；结果按案件、证据类型和检索时间排序。"""
        if not 1 <= limit <= 5000:
            raise ValueError("limit必须在1到5000之间")
        clauses, params = [], []
        if case_id is not None:
            clauses.append("case_id=?")
            params.append(case_id)
        if evidence_kind is not None:
            self._validate_enum(evidence_kind, {
                "batch_notice", "formal_decision", "review_application", "review_decision",
                "supplementary_decision", "final_termination_notice", "transfer_exit_record",
                "issuer_notice", "sponsor_notice", "third_party_lead", "other",
            }, "evidence_kind")
            clauses.append("evidence_kind=?")
            params.append(evidence_kind)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM neeq_termination_evidence_search" + where
                + " ORDER BY case_id,evidence_kind,searched_at,search_id LIMIT ?",
                (*params, limit),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["source_urls"] = json.loads(item.pop("source_urls_json") or "[]")
                result.append(item)
            return result

    def record_neeq_termination_review(self, review: dict) -> int:
        """追加人工审核历史，不覆盖旧审核结论。"""
        case_id = str(review.get("case_id") or "").strip()
        if not case_id:
            raise ValueError("审核记录必须提供case_id")
        self._validate_enum(review.get("review_status", "pending"), {
            "pending", "verified", "rejected", "needs_more_evidence",
        }, "review_status")
        if not str(review.get("reviewer") or "").strip() or not str(review.get("reviewed_at") or "").strip():
            raise ValueError("审核记录必须提供审核人和审核时间")
        conclusion = str(review.get("conclusion_code") or "")
        if conclusion == "actual_termination_confirmed":
            raise ValueError("此台账接口不允许直接确认正式实际终止挂牌事实")
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO neeq_termination_review
                (case_id,evidence_id,procedure_id,review_status,conclusion_code,
                 reviewer,reviewed_at,review_notes)
                VALUES(?,?,?,?,?,?,?,?)""",
                (case_id, review.get("evidence_id"), review.get("procedure_id"),
                 review.get("review_status", "pending"), conclusion,
                 review["reviewer"].strip(), review["reviewed_at"].strip(),
                 str(review.get("review_notes") or "")),
            )
            return int(cursor.lastrowid)

    def list_neeq_termination_cases(
        self, *, company_code: str | None = None, case_status: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        if not 1 <= limit <= 5000:
            raise ValueError("limit必须在1到5000之间")
        clauses, params = [], []
        if company_code is not None:
            if len(company_code) != 6 or not company_code.isdigit():
                raise ValueError("company_code必须是六位数字")
            clauses.append("company_code=?")
            params.append(company_code)
        if case_status is not None:
            self._validate_enum(case_status, {
                "pending_review", "evidence_gathering", "ready_for_review",
                "reviewed", "closed", "invalid",
            }, "case_status")
            clauses.append("case_status=?")
            params.append(case_status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            result = []
            for row in connection.execute(
                "SELECT * FROM neeq_termination_case" + where +
                " ORDER BY interface_result_date,company_code,case_id LIMIT ?",
                (*params, limit),
            ).fetchall():
                item = dict(row)
                item["raw_record"] = json.loads(item.pop("raw_record_json"))
                result.append(item)
            return result

    def save_radar_run(self, run_at: str, candidate_count: int, pushed_count: int,
                       source_status: str, notes: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO radar_runs
                (run_at,candidate_count,pushed_count,source_status,notes)
                VALUES(?,?,?,?,?)""",
                (run_at,candidate_count,pushed_count,source_status,notes),
            )
