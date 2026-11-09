-- 本文件定义市场雷达独立SQLite数据库的表、唯一约束与索引，属于逐文件开发清单第八项。
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS security_master (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    exchange TEXT NOT NULL DEFAULT '',
    listing_status TEXT NOT NULL DEFAULT 'active',
    risk_flag INTEGER NOT NULL DEFAULT 0 CHECK (risk_flag IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_security_status_risk
    ON security_master(listing_status, risk_flag);

CREATE TABLE IF NOT EXISTS sector_master (
    sector_code TEXT PRIMARY KEY,
    sector_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sector_membership (
    security_code TEXT NOT NULL,
    sector_code TEXT NOT NULL,
    valid_from TEXT NOT NULL DEFAULT '',
    valid_to TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    PRIMARY KEY (security_code, sector_code, valid_from),
    FOREIGN KEY (security_code) REFERENCES security_master(code),
    FOREIGN KEY (sector_code) REFERENCES sector_master(sector_code)
);
CREATE INDEX IF NOT EXISTS idx_sector_membership_sector
    ON sector_membership(sector_code, valid_to);

CREATE TABLE IF NOT EXISTS cross_market_map (
    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    us_entity TEXT NOT NULL,
    cn_security_code TEXT NOT NULL,
    mapping_type TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'neutral',
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    evidence TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    verified_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(us_entity, cn_security_code, mapping_type, source_url),
    FOREIGN KEY (cn_security_code) REFERENCES security_master(code)
);
CREATE INDEX IF NOT EXISTS idx_cross_map_entity_status
    ON cross_market_map(us_entity, status, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_cross_map_security
    ON cross_market_map(cn_security_code, status);

CREATE TABLE IF NOT EXISTS market_events (
    event_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'unknown',
    direction TEXT NOT NULL DEFAULT 'neutral',
    region TEXT NOT NULL DEFAULT 'US',
    url TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_market_events_published
    ON market_events(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_events_source
    ON market_events(source, published_at DESC);

CREATE TABLE IF NOT EXISTS event_candidates (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    target_code TEXT NOT NULL,
    mapping_type TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    evidence TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT 'neutral',
    verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(event_id, target_code, mapping_type),
    FOREIGN KEY (event_id) REFERENCES market_events(event_id)
);
CREATE INDEX IF NOT EXISTS idx_event_candidate_target
    ON event_candidates(target_code, confidence DESC);

CREATE TABLE IF NOT EXISTS market_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    price REAL,
    change_pct REAL,
    turnover REAL,
    volume_ratio REAL,
    source TEXT NOT NULL DEFAULT '',
    delayed INTEGER NOT NULL DEFAULT 1 CHECK (delayed IN (0, 1)),
    UNIQUE(code, timestamp),
    FOREIGN KEY (code) REFERENCES security_master(code)
);
CREATE INDEX IF NOT EXISTS idx_market_snapshot_time
    ON market_snapshots(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_market_snapshot_code_time
    ON market_snapshots(code, timestamp DESC);

CREATE TABLE IF NOT EXISTS radar_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    pushed_count INTEGER NOT NULL DEFAULT 0,
    source_status TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_radar_runs_time ON radar_runs(run_at DESC);
