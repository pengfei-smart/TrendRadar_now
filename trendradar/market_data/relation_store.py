# 本文件实现跨市场可信关系库的SQLite持久化、审核状态流转及审计记录，属于逐文件开发清单第八项。
"""Persistence and review workflow for cross-market mapping relations."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from trendradar.market_data.models import MappingEvidence


_ALLOWED_STATUS = {"pending", "verified", "rejected", "stale"}
_ALLOWED_TYPES = {
    "direct_business", "supply_chain", "industry_sentiment", "macro_transmission",
    "competitor", "customer_supplier", "other",
}
_ALLOWED_DIRECTIONS = {"positive", "negative", "mixed", "neutral", "unknown"}


class CrossMarketRelationStore:
    """Store mapping proposals and expose only reviewed, currently valid relations."""

    def __init__(self, db_path: str = "output/market/market.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS cross_market_map (
                relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                us_entity TEXT NOT NULL,
                us_ticker TEXT NOT NULL DEFAULT '',
                cn_security_code TEXT NOT NULL,
                mapping_type TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'neutral',
                confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                evidence TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','verified','rejected','stale')),
                reviewer TEXT NOT NULL DEFAULT '',
                verified_at TEXT NOT NULL DEFAULT '',
                valid_from TEXT NOT NULL DEFAULT '',
                valid_to TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(us_entity,us_ticker,cn_security_code,mapping_type,source_url)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS cross_market_map_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                relation_id INTEGER NOT NULL,
                old_status TEXT NOT NULL,
                new_status TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relation_lookup ON cross_market_map(us_entity,status,confidence DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relation_cn ON cross_market_map(cn_security_code,status)")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=20000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def propose(self, *, us_entity: str, us_ticker: str, cn_security_code: str,
                mapping_type: str, direction: str, confidence: float, evidence: str,
                source: str = "", source_url: str = "") -> int:
        """Insert/update a proposal; proposals always remain pending unless reviewed."""
        if not us_entity.strip() or not cn_security_code.strip() or not evidence.strip():
            raise ValueError("us_entity, cn_security_code and evidence are required")
        if mapping_type not in _ALLOWED_TYPES or direction not in _ALLOWED_DIRECTIONS:
            raise ValueError("unsupported mapping_type or direction")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be in [0, 1]")
        with self.connect() as conn:
            row = conn.execute("""INSERT INTO cross_market_map
                (us_entity,us_ticker,cn_security_code,mapping_type,direction,confidence,
                 evidence,source,source_url,status)
                VALUES(?,?,?,?,?,?,?,?,?,'pending')
                ON CONFLICT(us_entity,us_ticker,cn_security_code,mapping_type,source_url)
                DO UPDATE SET direction=excluded.direction,confidence=excluded.confidence,
                    evidence=excluded.evidence,source=excluded.source,
                    status=CASE WHEN cross_market_map.status='verified' THEN 'verified' ELSE 'pending' END,
                    updated_at=CURRENT_TIMESTAMP
                RETURNING relation_id""",
                (us_entity.strip(),us_ticker.strip().upper(),cn_security_code.strip(),mapping_type,
                 direction,confidence,evidence.strip(),source.strip(),source_url.strip())).fetchone()
            return int(row["relation_id"])

    def review(self, relation_id: int, status: str, reviewer: str, note: str = "") -> None:
        """Review a proposal and append an immutable status-transition audit row."""
        if status not in _ALLOWED_STATUS or not reviewer.strip():
            raise ValueError("valid status and reviewer are required")
        with self.connect() as conn:
            row = conn.execute("SELECT status FROM cross_market_map WHERE relation_id=?", (relation_id,)).fetchone()
            if row is None:
                raise LookupError(f"relation {relation_id} not found")
            old_status = row["status"]
            verified_at = "CURRENT_TIMESTAMP" if status == "verified" else "''"
            conn.execute("UPDATE cross_market_map SET status=?,reviewer=?,verified_at=" + verified_at +
                         ",updated_at=CURRENT_TIMESTAMP WHERE relation_id=?", (status,reviewer.strip(),relation_id))
            conn.execute("""INSERT INTO cross_market_map_audit
                (relation_id,old_status,new_status,reviewer,note) VALUES(?,?,?,?,?)""",
                (relation_id,old_status,status,reviewer.strip(),note.strip()))

    def trusted_by_sector(self, sector_name: str) -> list[MappingEvidence]:
        """Return verified relations whose evidence explicitly identifies the sector."""
        # The sector tag is persisted as part of evidence text for the current schema.
        marker = f"sector:{sector_name}"
        with self.connect() as conn:
            rows = conn.execute("""SELECT cn_security_code,mapping_type,confidence,evidence,
                source,source_url,direction FROM cross_market_map
                WHERE status='verified' AND evidence LIKE ?
                AND (valid_to='' OR valid_to>=date('now'))
                ORDER BY confidence DESC""", (f"%{marker}%",)).fetchall()
        return [MappingEvidence(target_code=row["cn_security_code"],
            mapping_type=row["mapping_type"],confidence=float(row["confidence"]),
            evidence=row["evidence"],source=row["source"],source_url=row["source_url"],
            direction=row["direction"],verified=True) for row in rows]

    def list_by_status(self, status: str, limit: int = 100) -> list[dict]:
        if status not in _ALLOWED_STATUS or not 1 <= limit <= 1000:
            raise ValueError("invalid status or limit")
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM cross_market_map WHERE status=? ORDER BY confidence DESC LIMIT ?",
                                (status,limit)).fetchall()
        return [dict(row) for row in rows]
