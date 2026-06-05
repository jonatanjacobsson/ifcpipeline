-- MinIO bucket versioning support for the audit trail.
--
-- Before: UNIQUE (bucket, object_key, sha256) assumed "same key + same bytes
-- = one row". Once bucket versioning is on, MinIO assigns a VersionId to
-- every PUT, and "same key, new bytes" becomes an explicit new version —
-- we want a separate row for each version even if the sha256 matches
-- (ambiguous for dedup, but conservative for audit).
--
-- After: each object_versions row carries the MinIO VersionId. The unique
-- key becomes (bucket, object_key, version_id) when the bucket is versioned,
-- and falls back to (bucket, object_key, sha256) for rows from unversioned
-- buckets or pre-versioning backfill where version_id is NULL.
--
-- Using a unique *expression* index with COALESCE(version_id, sha256) lets
-- both regimes coexist. Postgres treats expression indexes as valid targets
-- for ON CONFLICT(...) so the upsert logic in audit_db._upsert_version can
-- target this index via ON CONFLICT(bucket, object_key, COALESCE(version_id, sha256)).

ALTER TABLE object_versions
    ADD COLUMN IF NOT EXISTS version_id TEXT;

-- Drop the legacy unique constraint. The constraint name matches the
-- auto-generated one from 03-audit.sql's UNIQUE(...) in-table declaration.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'object_versions_bucket_object_key_sha256_key'
    ) THEN
        ALTER TABLE object_versions
            DROP CONSTRAINT object_versions_bucket_object_key_sha256_key;
    END IF;
END
$$;

-- Expression-based unique index so ON CONFLICT (bucket, object_key, COALESCE(version_id, sha256))
-- has a valid target.
CREATE UNIQUE INDEX IF NOT EXISTS object_versions_bucket_key_version_uidx
    ON object_versions (bucket, object_key, COALESCE(version_id, sha256));

-- Speed up /audit/history/<key> and per-key lineage lookups.
CREATE INDEX IF NOT EXISTS idx_object_versions_key_created_desc
    ON object_versions (object_key, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_object_versions_version_id
    ON object_versions (version_id)
    WHERE version_id IS NOT NULL;

-- Record the parent version pin on each lineage edge so ancestor walks can
-- be exact even after the parent key is overwritten.
ALTER TABLE object_lineage
    ADD COLUMN IF NOT EXISTS parent_version_id TEXT;
