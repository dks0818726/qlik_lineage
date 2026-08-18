CREATE TABLE IF NOT EXISTS apps (
    app_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id TEXT,
    stream_id TEXT,
    script_hash TEXT,
    modified_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS streams (
    stream_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS owners (
    owner_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    app_id TEXT,
    name TEXT NOT NULL,
    schedule_id TEXT,
    depends_on_task_id TEXT
);

CREATE TABLE IF NOT EXISTS schedules (
    schedule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cron_expression TEXT
);

CREATE TABLE IF NOT EXISTS connections (
    connection_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    connection_type TEXT
);

CREATE TABLE IF NOT EXISTS qvds (
    qvd_path TEXT PRIMARY KEY,
    name TEXT,
    last_seen TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS source_tables (
    table_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    connection_id TEXT,
    last_seen TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scripts (
    app_id TEXT PRIMARY KEY,
    script_text TEXT NOT NULL,
    script_hash TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lineage_edges (
    id BIGSERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (source_type, source_id, relation, target_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_lineage_source ON lineage_edges (source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_lineage_target ON lineage_edges (target_type, target_id);

CREATE TABLE IF NOT EXISTS scan_runs (
    scan_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP,
    status TEXT NOT NULL,
    stats JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS change_log (
    id BIGSERIAL PRIMARY KEY,
    scan_id TEXT,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    change_type TEXT NOT NULL,  -- created | updated | deleted
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
