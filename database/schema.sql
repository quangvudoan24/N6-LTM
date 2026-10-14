-- SCHEMA CHÍNH THỨC. Chỉ TV6 (Gia Đạt) được sửa; muốn đổi -> tạo Pull Request.
-- Tên cột khớp 1-1 với trường JSON trong docs/PROTOCOL.md (riêng ip -> ip_address, os giữ nguyên).
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS agents (
    agent_id    TEXT PRIMARY KEY,
    hostname    TEXT NOT NULL,
    ip_address  TEXT,
    os          TEXT,
    status      TEXT NOT NULL DEFAULT 'OFFLINE'
                CHECK (status IN ('ONLINE','WARNING','OFFLINE')),
    last_seen   INTEGER,              -- Unix timestamp (giây)
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    cpu         REAL,
    ram         REAL,
    disk        REAL,
    timestamp   INTEGER NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
);
CREATE INDEX IF NOT EXISTS idx_metrics_agent_time ON metrics(agent_id, timestamp);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    event_type  TEXT NOT NULL,        -- WENT_ONLINE / WENT_WARNING / WENT_OFFLINE / HIGH_CPU ...
    message     TEXT,
    timestamp   INTEGER NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp DESC);
