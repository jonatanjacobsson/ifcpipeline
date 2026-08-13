-- Tester / clash GUID rows for the object-storage variant.
--
-- Per-element GUID indexing (object_guids) is unused here — CDE owns that
-- pipeline. The table is still created so existing databases and this init
-- file stay compatible; nothing in ifcpipeline writes to it.
--
-- Tester and clash results have their own tables because (a) they carry
-- additional columns beyond role/entity_type, and (b) a clash pair is a
-- relation between two guids, not a single-guid fact.
--
-- ifctester writes tester_results with a UNIQUE constraint so retries are
-- idempotent. ifcclash does NOT have a UNIQUE constraint because a single
-- clash run may legitimately record the same pair under different
-- clash_sets; the worker dedupes per-run before inserting.

CREATE TABLE IF NOT EXISTS object_guids (
    id BIGSERIAL PRIMARY KEY,
    object_version_id BIGINT NOT NULL REFERENCES object_versions(id) ON DELETE CASCADE,
    ifc_guid TEXT NOT NULL,
    entity_type TEXT,
    role TEXT NOT NULL,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS object_guids_version_guid_role_uidx
    ON object_guids (object_version_id, ifc_guid, role);

CREATE INDEX IF NOT EXISTS idx_object_guids_guid
    ON object_guids (ifc_guid);

CREATE INDEX IF NOT EXISTS idx_object_guids_guid_role_version
    ON object_guids (ifc_guid, role, object_version_id);

CREATE INDEX IF NOT EXISTS idx_object_guids_entity_type
    ON object_guids (entity_type)
    WHERE entity_type IS NOT NULL;


-- Per-(ids_rule, guid) validation outcome from ifctester. Populated directly
-- by the ifctester worker from its JSON report.
CREATE TABLE IF NOT EXISTS tester_results (
    id BIGSERIAL PRIMARY KEY,
    object_version_id BIGINT NOT NULL REFERENCES object_versions(id) ON DELETE CASCADE,
    ifc_guid TEXT NOT NULL,
    ids_rule TEXT NOT NULL,
    passed BOOLEAN NOT NULL,
    reason TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS tester_results_version_guid_rule_uidx
    ON tester_results (object_version_id, ifc_guid, ids_rule);

CREATE INDEX IF NOT EXISTS idx_tester_results_guid
    ON tester_results (ifc_guid);

CREATE INDEX IF NOT EXISTS idx_tester_results_guid_passed
    ON tester_results (ifc_guid, passed);


-- Per-pair clash record, populated directly by the ifcclash worker.
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
