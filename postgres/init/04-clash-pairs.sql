-- Per-pair clash records written by ifcclash-worker after a clash report upload.
CREATE TABLE IF NOT EXISTS clash_pairs (
    id BIGSERIAL PRIMARY KEY,
    object_version_id BIGINT NOT NULL REFERENCES object_versions(id) ON DELETE CASCADE,
    guid_a TEXT NOT NULL,
    guid_b TEXT NOT NULL,
    distance DOUBLE PRECISION,
    kind TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_clash_pairs_guid_a ON clash_pairs (guid_a);
CREATE INDEX IF NOT EXISTS idx_clash_pairs_guid_b ON clash_pairs (guid_b);
CREATE INDEX IF NOT EXISTS idx_clash_pairs_version ON clash_pairs (object_version_id);
