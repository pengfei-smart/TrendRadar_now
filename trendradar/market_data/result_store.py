# 本文件持久化市场雷达每次运行的状态、全量排序候选、评分证据与推送标记，供历史复盘和质量评估使用。
"""SQLite persistence for radar runs and complete candidate snapshots."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from trendradar.market_data.models import Candidate


class RadarResultStore:
    """Persist run metadata and all ranked candidates, not only push selections."""

    def __init__(self, db_path: str = "output/market/market.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS radar_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                run_type TEXT NOT NULL DEFAULT 'snapshot',
                status TEXT NOT NULL DEFAULT 'started' CHECK(status IN ('started','success','partial','failed')),
                candidate_count INTEGER NOT NULL DEFAULT 0,
                pushed_count INTEGER NOT NULL DEFAULT 0,
                source_status TEXT NOT NULL DEFAULT '',
                quote_source TEXT NOT NULL DEFAULT '',
                quote_data_at TEXT NOT NULL DEFAULT '',
                duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(duration_ms >= 0),
                notes TEXT NOT NULL DEFAULT ''
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS radar_candidates (
                run_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                tier TEXT NOT NULL CHECK(tier IN ('A','B','C')),
                score REAL NOT NULL DEFAULT 0,
                causal_status TEXT NOT NULL DEFAULT 'unverified',
                reasons TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                quote_timestamp TEXT NOT NULL DEFAULT '',
                pushed INTEGER NOT NULL DEFAULT 0 CHECK(pushed IN (0,1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(run_id,code),
                FOREIGN KEY(run_id) REFERENCES radar_runs(run_id) ON DELETE CASCADE
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_radar_runs_time_status ON radar_runs(run_at DESC,status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_radar_candidates_code_run ON radar_candidates(code,run_id DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_radar_candidates_run_score ON radar_candidates(run_id,score DESC)")

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

    def start_run(self, run_at: str, run_type: str = "snapshot", notes: str = "") -> int:
        if not run_at.strip() or run_type not in {"snapshot", "intraday", "backfill", "manual"}:
            raise ValueError("run_at and a valid run_type are required")
        with self.connect() as conn:
            row = conn.execute("INSERT INTO radar_runs(run_at,run_type,status,notes) VALUES(?,?,'started',?) RETURNING run_id",
                               (run_at.strip(), run_type, notes.strip())).fetchone()
            return int(row["run_id"])

    @staticmethod
    def _evidence_json(candidate: Candidate) -> str:
        return json.dumps([{
            "target_code": item.target_code,
            "mapping_type": item.mapping_type,
            "confidence": item.confidence,
            "evidence": item.evidence,
            "source": item.source,
            "source_url": item.source_url,
            "direction": item.direction,
            "verified": item.verified,
        } for item in candidate.evidence], ensure_ascii=False, separators=(",", ":"))

    def finish_run(self, run_id: int, candidates: Sequence[Candidate], *, status: str,
                   pushed_codes: Sequence[str] = (), source_status: str = "",
                   quote_source: str = "", quote_data_at: str = "", duration_ms: int = 0,
                   notes: str = "") -> None:
        if status not in {"success", "partial", "failed"} or duration_ms < 0:
            raise ValueError("invalid run status or duration")
        pushed = set(pushed_codes)
        with self.connect() as conn:
            exists = conn.execute("SELECT run_id FROM radar_runs WHERE run_id=?", (run_id,)).fetchone()
            if exists is None:
                raise LookupError(f"radar run {run_id} not found")
            conn.execute("DELETE FROM radar_candidates WHERE run_id=?", (run_id,))
            conn.executemany("""INSERT INTO radar_candidates
                (run_id,code,tier,score,causal_status,reasons,evidence_json,quote_timestamp,pushed)
                VALUES(?,?,?,?,?,?,?,?,?)""", [(
                run_id, c.security.code, c.tier, float(c.score), c.causal_status,
                " | ".join(c.reasons), self._evidence_json(c),
                c.quote.timestamp if c.quote else "", int(c.security.code in pushed),
            ) for c in candidates])
            conn.execute("""UPDATE radar_runs SET status=?,candidate_count=?,pushed_count=?,source_status=?,
                quote_source=?,quote_data_at=?,duration_ms=?,notes=? WHERE run_id=?""",
                (status, len(candidates), len(pushed.intersection({c.security.code for c in candidates})),
                 source_status, quote_source, quote_data_at, duration_ms, notes, run_id))

    def get_run(self, run_id: int) -> dict:
        with self.connect() as conn:
            run = conn.execute("SELECT * FROM radar_runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None:
                raise LookupError(f"radar run {run_id} not found")
            candidates = conn.execute("SELECT * FROM radar_candidates WHERE run_id=? ORDER BY score DESC,code",
                                     (run_id,)).fetchall()
        return {"run": dict(run), "candidates": [dict(row) for row in candidates]}
