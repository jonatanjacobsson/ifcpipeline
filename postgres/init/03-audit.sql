-- Object-storage audit trail (append-only lineage)
--
-- object_versions  : one row per (bucket, key, sha256) — roots and derivatives.
-- object_lineage   : directed edges parent -> child with a role label.
--
-- The tables are append-only from the application side; cascading delete is
-- retained so operators can prune a version (or whole sub-tree) by hand.

CREATE TABLE IF NOT EXISTS object_versions (
    id           BIGSERIAL PRIMARY KEY,
    bucket       TEXT NOT NULL,
    object_key   TEXT NOT NULL,
    sha256       CHAR(64) NOT NULL,
    size_bytes   BIGINT NOT NULL,
    content_type TEXT,
    kind         TEXT NOT NULL CHECK (kind IN ('root', 'derived')),
    operation    TEXT NOT NULL,
    worker       TEXT,
    job_id       TEXT,
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bucket, object_key, sha256)
);

CREATE INDEX IF NOT EXISTS idx_object_versions_sha256
    ON object_versions (sha256);
CREATE INDEX IF NOT EXISTS idx_object_versions_job_id
    ON object_versions (job_id);
CREATE INDEX IF NOT EXISTS idx_object_versions_operation_created_at
    ON object_versions (operation, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_object_versions_key_created_at
    ON object_versions (object_key, created_at DESC);

CREATE TABLE IF NOT EXISTS object_lineage (
    parent_id BIGINT NOT NULL REFERENCES object_versions(id) ON DELETE CASCADE,
    child_id  BIGINT NOT NULL REFERENCES object_versions(id) ON DELETE CASCADE,
    role      TEXT   NOT NULL CHECK (role IN ('input', 'reference', 'sibling')),
    PRIMARY KEY (parent_id, child_id, role)
);

CREATE INDEX IF NOT EXISTS idx_object_lineage_child
    ON object_lineage (child_id);
