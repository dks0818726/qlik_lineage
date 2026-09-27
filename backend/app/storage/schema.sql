CREATE TABLE IF NOT EXISTS apps (
    app_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id TEXT,
    stream_id TEXT,
    script_hash TEXT,
    modified_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- App status flags (see app/scanner/app_status.py). Added with ALTER so existing
-- databases pick them up on the next start without losing data. All nullable:
-- they stay empty until the next scan or POST /scan/app-status fills them in.
ALTER TABLE apps ADD COLUMN IF NOT EXISTS app_status TEXT;          -- live | dev_copy | stale | unscheduled
ALTER TABLE apps ADD COLUMN IF NOT EXISTS status_reason TEXT;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMPTZ;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS base_name TEXT;           -- name without "(n)" suffixes
ALTER TABLE apps ADD COLUMN IF NOT EXISTS is_copy BOOLEAN;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS original_app_id TEXT;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS last_reload_at TIMESTAMPTZ;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS published BOOLEAN;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS target_app_id TEXT;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS owner_name TEXT;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS owner_user TEXT;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS modified_by TEXT;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS file_size BIGINT;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS tags JSONB;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS task_count INTEGER;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS has_enabled_task BOOLEAN;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS last_task_status TEXT;
ALTER TABLE apps ADD COLUMN IF NOT EXISTS last_task_run_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_apps_status ON apps (app_status);
CREATE INDEX IF NOT EXISTS idx_apps_base_name ON apps (base_name);

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

-- Generated app documentation. Stored alongside the file written to the
-- mounted output folder so downloads are served transactionally rather than
-- by reading a container-relative path back off disk.
CREATE TABLE IF NOT EXISTS app_documentation (
    app_id TEXT PRIMARY KEY REFERENCES apps (app_id) ON DELETE CASCADE,
    markdown TEXT NOT NULL,
    filename TEXT NOT NULL,
    -- Hash of the script the document was generated from. When this no longer
    -- matches apps.script_hash the document describes an older script and the
    -- UI can say "out of date" instead of silently misleading the reader.
    script_hash TEXT,
    model TEXT,
    evidence_level INTEGER NOT NULL DEFAULT 1,
    sections INTEGER NOT NULL DEFAULT 0,
    generated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
