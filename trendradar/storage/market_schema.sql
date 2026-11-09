-- 本文件定义短线机会雷达独立SQLite数据库的完整表结构、索引、唯一约束及版本标记，便于后续证券主数据与行情接入。
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO schema_meta(key, value) VALUES ('schema_version', '2')
ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP;

CREATE TABLE IF NOT EXISTS data_sources (
    source_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('security_master','sector','quote','news','macro','mapping')),
    provider_url TEXT NOT NULL DEFAULT '',
    terms_url TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    priority INTEGER NOT NULL DEFAULT 100,
    last_success_at TEXT NOT NULL DEFAULT '',
    last_failure_at TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_data_sources_type_enabled
    ON data_sources(source_type, enabled, priority);

CREATE TABLE IF NOT EXISTS security_master (
    code TEXT PRIMARY KEY CHECK (length(code) = 6 AND code NOT GLOB '*[^0-9]*'),
    name TEXT NOT NULL,
    name_en TEXT NOT NULL DEFAULT '',
    exchange TEXT NOT NULL CHECK (exchange IN ('SH','SZ','BJ','')),
    board TEXT NOT NULL DEFAULT '',
    listing_status TEXT NOT NULL DEFAULT 'active'
        CHECK (listing_status IN ('active','suspended','delisted','pending','unknown')),
    risk_flag INTEGER NOT NULL DEFAULT 0 CHECK (risk_flag IN (0, 1)),
    listing_date TEXT NOT NULL DEFAULT '',
    delisting_date TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    source_updated_at TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_security_status_risk
    ON security_master(listing_status, risk_flag, exchange);
CREATE INDEX IF NOT EXISTS idx_security_name ON security_master(name);
CREATE INDEX IF NOT EXISTS idx_security_board_status ON security_master(board, listing_status);

CREATE TABLE IF NOT EXISTS sector_master (
    sector_code TEXT PRIMARY KEY,
    sector_name TEXT NOT NULL,
    sector_type TEXT NOT NULL DEFAULT 'industry'
        CHECK (sector_type IN ('industry','concept','theme','index','supply_chain','other')),
    provider TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    source_updated_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sector_type_name ON sector_master(sector_type, sector_name);

CREATE TABLE IF NOT EXISTS sector_membership (
    membership_id INTEGER PRIMARY KEY AUTOINCREMENT,
    security_code TEXT NOT NULL,
    sector_code TEXT NOT NULL,
    valid_from TEXT NOT NULL DEFAULT '',
    valid_to TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(security_code, sector_code, valid_from),
    FOREIGN KEY (security_code) REFERENCES security_master(code) ON UPDATE CASCADE,
    FOREIGN KEY (sector_code) REFERENCES sector_master(sector_code) ON UPDATE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sector_membership_sector_period
    ON sector_membership(sector_code, valid_to, valid_from);
CREATE INDEX IF NOT EXISTS idx_sector_membership_security_period
    ON sector_membership(security_code, valid_to, valid_from);

CREATE TABLE IF NOT EXISTS cross_market_map (
    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    us_entity TEXT NOT NULL,
    us_ticker TEXT NOT NULL DEFAULT '',
    cn_security_code TEXT NOT NULL,
    mapping_type TEXT NOT NULL
        CHECK (mapping_type IN ('direct_business','supply_chain','industry_sentiment','macro_transmission','competitor','customer_supplier','other')),
    direction TEXT NOT NULL DEFAULT 'neutral'
        CHECK (direction IN ('positive','negative','mixed','neutral','unknown')),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    evidence TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','verified','rejected','stale')),
    reviewer TEXT NOT NULL DEFAULT '',
    verified_at TEXT NOT NULL DEFAULT '',
    valid_from TEXT NOT NULL DEFAULT '',
    valid_to TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(us_entity, us_ticker, cn_security_code, mapping_type, source_url),
    FOREIGN KEY (cn_security_code) REFERENCES security_master(code) ON UPDATE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cross_map_entity_status_confidence
    ON cross_market_map(us_entity, status, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_cross_map_ticker_status
    ON cross_market_map(us_ticker, status, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_cross_map_security_status
    ON cross_market_map(cn_security_code, status, mapping_type);

CREATE TABLE IF NOT EXISTS market_events (
    event_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_event_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    published_at TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'unknown',
    direction TEXT NOT NULL DEFAULT 'neutral'
        CHECK (direction IN ('positive','negative','mixed','neutral','unknown')),
    region TEXT NOT NULL DEFAULT 'US',
    language TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Source IDs may be unavailable for RSS feeds; only non-empty source_event_id values are unique per source.
CREATE UNIQUE INDEX IF NOT EXISTS idx_market_events_source_external_id
    ON market_events(source, source_event_id) WHERE source_event_id <> '';
CREATE INDEX IF NOT EXISTS idx_market_events_published ON market_events(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_events_source_published ON market_events(source, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_events_type_published ON market_events(event_type, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_events_hash ON market_events(content_hash);

CREATE TABLE IF NOT EXISTS event_candidates (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    target_code TEXT NOT NULL,
    mapping_type TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    evidence TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT 'neutral'
        CHECK (direction IN ('positive','negative','mixed','neutral','unknown')),
    verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    evidence_hash TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(event_id, target_code, mapping_type),
    FOREIGN KEY (event_id) REFERENCES market_events(event_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_event_candidate_target_confidence
    ON event_candidates(target_code, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_event_candidate_event_verified
    ON event_candidates(event_id, verified, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_event_candidate_source ON event_candidates(source, source_url);

CREATE TABLE IF NOT EXISTS market_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    trading_date TEXT NOT NULL DEFAULT '',
    price REAL CHECK (price IS NULL OR price >= 0),
    change_pct REAL,
    turnover REAL CHECK (turnover IS NULL OR turnover >= 0),
    volume_ratio REAL CHECK (volume_ratio IS NULL OR volume_ratio >= 0),
    source TEXT NOT NULL DEFAULT '',
    delayed INTEGER NOT NULL DEFAULT 1 CHECK (delayed IN (0, 1)),
    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(code, timestamp, source),
    FOREIGN KEY (code) REFERENCES security_master(code) ON UPDATE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_market_snapshot_time ON market_snapshots(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_market_snapshot_code_time ON market_snapshots(code, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_market_snapshot_trading_date ON market_snapshots(trading_date, code);

CREATE TABLE IF NOT EXISTS radar_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    run_type TEXT NOT NULL DEFAULT 'snapshot'
        CHECK (run_type IN ('snapshot','intraday','backfill','manual')),
    status TEXT NOT NULL DEFAULT 'started'
        CHECK (status IN ('started','success','partial','failed')),
    candidate_count INTEGER NOT NULL DEFAULT 0,
    pushed_count INTEGER NOT NULL DEFAULT 0,
    source_status TEXT NOT NULL DEFAULT '',
    quote_source TEXT NOT NULL DEFAULT '',
    quote_data_at TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
    notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_radar_runs_time_status ON radar_runs(run_at DESC, status);

CREATE TABLE IF NOT EXISTS radar_candidates (
    run_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('A','B','C')),
    score REAL NOT NULL DEFAULT 0,
    causal_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (causal_status IN ('verified','unverified','conflicting','unknown')),
    reasons TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    quote_timestamp TEXT NOT NULL DEFAULT '',
    pushed INTEGER NOT NULL DEFAULT 0 CHECK (pushed IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(run_id, code),
    FOREIGN KEY (run_id) REFERENCES radar_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (code) REFERENCES security_master(code) ON UPDATE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_radar_candidates_code_time ON radar_candidates(code, run_id DESC);
CREATE INDEX IF NOT EXISTS idx_radar_candidates_run_score ON radar_candidates(run_id, score DESC);

CREATE TABLE IF NOT EXISTS ingestion_state (
    source_id TEXT NOT NULL,
    feed_key TEXT NOT NULL DEFAULT '',
    cursor_value TEXT NOT NULL DEFAULT '',
    last_success_at TEXT NOT NULL DEFAULT '',
    last_attempt_at TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source_id, feed_key)
);
CREATE INDEX IF NOT EXISTS idx_ingestion_state_success ON ingestion_state(last_success_at DESC);

CREATE TABLE IF NOT EXISTS remote_sync_state (
    object_key TEXT PRIMARY KEY,
    remote_version TEXT NOT NULL DEFAULT '',
    remote_etag TEXT NOT NULL DEFAULT '',
    local_sha256 TEXT NOT NULL DEFAULT '',
    last_download_at TEXT NOT NULL DEFAULT '',
    last_upload_at TEXT NOT NULL DEFAULT '',
    last_success_at TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
