# Object-storage port of ifcpipeline — full coverage

> Object storage is **SeaweedFS** (S3-compatible, `seaweedfs:8333`). MinIO was
> decommissioned 2026-06. The 2026 backend evaluation and pilot runbooks live
> in `../ifcpipeline-minio-pilot-archive/docs/`.

## Goal

Replace the `/uploads` + `/output` bind-mounted filesystem with a self-hosted,
S3-compatible object store so that:

- Workers can be stateless and scale out without a shared disk.
- No NFS/CIFS dependency for multi-host deployments.
- Artifacts have a versionable, ACL'd storage backend.

## What's in this build

### Moving parts

- **SeaweedFS** (`chrislusf/seaweedfs:4.29`) as the self-hosted S3 endpoint (`seaweedfs:8333`).
- **`seaweedfs-setup`** one-shot container (`mc`) that creates the bucket on boot.
- **`seaweedfs/s3.json`** (gitignored) — S3 identity config; copy from `seaweedfs/s3.json.example` and match `.env` keys.
- **`shared/object_storage.py`** — boto3 wrapper with `download_to_tempfile`,
  `upload_from_path`, `object_exists`, key-normalizers, and
  `presigned_get_url_public` for externally reachable URLs.
- **All Linux-side workers converted to S3-first**:

  | Worker | Inputs | Outputs |
  | --- | --- | --- |
  | `ifccsv-worker` | `uploads/<ifc>` | `output/csv/<name>.csv` |
  | `ifctester-worker` | `uploads/<ifc>`, `uploads/<ids>` | `output/ids/<report>` |
  | `ifc-gherkin-worker` | `uploads/<ifc>` | `output/gherkin/<report>.json` |
  | `ifcconvert-worker` | `uploads/<ifc>` | `output/converted/<name>.<ext>`, `.log` |
  | `ifcclash-worker` | `uploads/<ifc>` × N | `output/clash/<name>.json` |
  | `ifcdiff-worker` | `uploads/<old>`, `uploads/<new>` | `output/diff/<report>.json` |
  | `ifc5d-worker` | `uploads/<ifc>` | `output/qto/<name>.ifc` |
  | `ifc2json-worker` | `uploads/<ifc>` | `output/json/<name>.json` |
  | `ifcpatch-worker` | `uploads/<ifc>` | `output/patch/<name>.ifc` |

- **`revit-worker`** is a Windows .NET tray app that runs outside Docker
  Compose and talks to Revit on a Windows host. It is **out of scope** for the
  Linux object-storage port — the on-prem Windows box keeps the RVT files
  local and only pushes resulting IFCs via the gateway's upload endpoint.

- **`api-gateway`**:
  - `/upload/{file_type}` streams the request body straight into S3 —
    no local disk write when `USE_OBJECT_STORAGE=true`.
  - `validate_input_file_exists` accepts files that live only in the bucket.
  - `/ifc2json/{filename}` fetches the JSON body from S3 first, then falls
    back to disk for legacy callers.
  - `/create_download_link` + `/download/{token}` auto-detect S3-backed
    paths and redirect the download to a **presigned URL** (see
    `S3_PUBLIC_ENDPOINT_URL`) so the client streams directly from object storage.

### Design choices

- Feature flag `USE_OBJECT_STORAGE=true|false`. Workers keep the legacy
  filesystem branch so you can A/B compare by flipping the env var.
- Single bucket (default `ifcpipeline`) with keys that mirror the original
  folder layout — `uploads/…`, `output/csv/…`, `output/diff/…`, etc. — so the
  two stacks are trivially diff-able.
- Workers pull objects into a per-job `tempfile.mkdtemp()`, run the ifcopenshell
  library against local paths, and push the result back up. The S3 path is a
  drop-in replacement for the bind-mounts — no API or queue shape changes.
- `S3_PUBLIC_ENDPOINT_URL` lets the gateway mint presigned URLs that point at
  whatever hostname clients can actually reach (e.g. a public HTTPS hostname
  in prod; loopback `:8333` for local smoke tests).

## Ports (default compose)

| Service       | Port |
|---------------|------|
| api-gateway   | 8000 |
| ifc-viewer    | 8001 |
| n8n           | 5678 |
| rq-dashboard  | 9181 |
| dozzle        | 9182 |
| SeaweedFS S3  | **8333** (loopback + LAN via host-lan overlay) |
| SeaweedFS filer UI | **8443** (loopback only) |

## Running the smoke test

```bash
cd /home/bimbot-ubuntu/apps/ifcpipeline
./smoke-test.sh
```

The script:

1. Builds and starts `seaweedfs + seaweedfs-setup + redis + postgres + api-gateway`
   plus every converted worker.
2. Uploads `Building-Architecture.ifc`, `Building-Hvac.ifc`,
   `Building-Structural.ifc` and `IDS-example.ids`.
3. Enqueues one job per worker (csv, tester, convert, diff, qto, 2json,
   patch, clash).
4. Polls each job to completion.
5. Lists every key in the bucket.

A successful run ends with something like:

```
output/converted/Building-Architecture.log
output/converted/Building-Architecture.obj
output/csv/arch.csv
output/diff/diff.json
output/ids/report.json
output/json/arch.json
output/patch/arch_patched.ifc
uploads/Building-Architecture.ifc
uploads/Building-Hvac.ifc
uploads/Building-Structural.ifc
uploads/IDS-example.ids
SMOKE TEST OK (with 2 known library-issue failure(s) — see OBJECT_STORAGE.md)
```

The worker outputs are **only** in S3 — `shared/output/*` stays empty on
the host.

### Known library-level failures (not object-storage issues)

Two smoke-test jobs reliably fail regardless of the storage backend and are
therefore reported as *soft* failures (the script still exits 0):

- **`ifc5d`** — `module 'ifcopenshell.util.shape' has no attribute
  'get_top_area'`. The pinned `ifc5d` expects a helper that was renamed or
  removed in the installed `ifcopenshell==0.8.0`. Fix is a version pin, not a
  storage change.
- **`ifcclash`** — `AssertionError` at `iterator.initialize()` inside
  `ifcclash 0.7.10 / ifcopenshell 0.7.10` on the IFC4X3 demo files shipped in
  `shared/examples`. Reproduces on the original filesystem-based stack too.

Both workers still run through S3 correctly — inputs download, outputs would
upload — the library itself is the blocker.

## Accessing SeaweedFS

S3 API: <http://localhost:8333> (path-style addressing, region `us-east-1`).
Filer UI: <http://localhost:8443> (loopback only).
Credentials: `S3_ACCESS_KEY` / `S3_SECRET_KEY` in `.env`, mirrored in `seaweedfs/s3.json`.

### Exposing the S3 API (e.g. Cloudflare Tunnel)

Compose publishes SeaweedFS on **127.0.0.1** only so the daemon is not reachable
from arbitrary networks. To give browsers a public URL (for presigned
redirects, e.g. `https://s3-api.example.com`):

1. Run **cloudflared** on the **same host** as Docker (outside the compose
   network is fine).
2. Point a tunnel hostname at the **origin** the host can reach:

   ```yaml
   # Example ingress fragment — use your real tunnel name / hostname
   - hostname: s3-api.example.com
     service: http://127.0.0.1:8333
   ```

3. Set **`S3_PUBLIC_ENDPOINT_URL`** to that public URL (`https://s3-api…`).
4. Configure **CORS** on the bucket for your viewer origin
   (`https://ifcpreview…`) so `fetch()` after the 307 can read the object.

### Rotating S3 credentials

`S3_ACCESS_KEY` and `S3_SECRET_KEY` in `.env` must match the identities in
`seaweedfs/s3.json`. All workers and the api-gateway use the same values.

- **Update both together:** edit `.env`, update `seaweedfs/s3.json`, then
  `docker compose -f docker-compose.yml -f docker-compose.host-lan.yml up -d --force-recreate seaweedfs`
  (and recreate services that talk to S3).
- Remote workers: regenerate `.env.remote` via `./scripts/deploy-remote-workers-from-primary.sh`.

## Environment variables

| Variable | Default | Where | Purpose |
| --- | --- | --- | --- |
| `USE_OBJECT_STORAGE` | `true` | gateway + workers | Flip to `false` for legacy mode |
| `S3_ENDPOINT_URL` | `http://seaweedfs:8333` | gateway + workers | Internal SeaweedFS S3 URL |
| `S3_PUBLIC_ENDPOINT_URL` | (set in `.env`) | gateway | Rewritten host for presigned URLs |
| `S3_ACCESS_KEY` | (set in `.env`) | all | SeaweedFS S3 identity (also in `seaweedfs/s3.json`) |
| `S3_SECRET_KEY` | (set in `.env`) | all | SeaweedFS S3 secret |
| `S3_BUCKET` | `ifcpipeline` | all | Single bucket name |
| `S3_REGION` | `us-east-1` | all | SeaweedFS ignores this; boto3 needs it |

## Compose services

- `docker-compose.control-plane.yml` — `seaweedfs`, `seaweedfs-setup`, `seaweedfs-data` volume, S3 env on api-gateway + guid-index-worker.
- `docker-compose.workers.yml` — S3 env on every worker; `depends_on: seaweedfs-setup`.
- `docker-compose.host-lan.yml` — publish Redis/Postgres/SeaweedFS S3 on `PIPELINE_LAN_IP`.

## Audit trail (object lineage)

Every object written to the bucket — root uploads **and** worker-produced
derivatives — is logged to PostgreSQL so operators can reconstruct where a
file came from, what produced it, and what jobs touched it. Audit logging is
tightly coupled to S3 writes via `shared/object_storage.upload_and_audit` and
the `/upload/{file_type}` endpoint; there is no separate write path.

### Schema

`postgres/init/02-audit.sql` creates two append-only tables:

- `object_versions` — one row per `(bucket, object_key, sha256)`. Columns of
  interest:
  - `sha256` (CHAR(64)) content hash — hashed *while* streaming to S3, so
    roots never hit a temp file on the gateway.
  - `size_bytes`, `content_type`.
  - `kind` — `root` (first upload) or `derived` (worker output).
  - `operation` — `upload`, `ifccsv`, `ifctester`, `ifcconvert`, `ifcdiff`,
    `ifc5d`, `ifc2json`, `ifcpatch`, `ifcclash`.
  - `worker` (null for roots), `job_id` (RQ job id; null for roots).
  - `metadata` (JSONB) — per-operation payload, e.g. `{"format":"csv",
    "element_count": 42}` on `ifccsv`, `{"pass": true, "failed_rules": 0}` on
    `ifctester`, `{"diff_count": 7}` on `ifcdiff`, etc.
- `object_lineage` — directed `parent_id -> child_id` edges with a `role`:
  - `input` — the primary input file (IFC, old-file, etc.).
  - `reference` — secondary input (IDS for tester, new-file for diff).
  - `sibling` — output grouped with a primary derivative (e.g. the
    `.log` file produced alongside a converted `.obj`).

Indices are provided on `sha256`, `job_id`, `(operation, created_at)`, and
`(object_key, created_at)` so lookups by any of those is cheap.

Both tables have `ON DELETE CASCADE` on the edge table; the application code
itself is append-only, but operators can prune a version (or entire subtree)
with a single `DELETE FROM object_versions WHERE …`.

### Writing the audit trail

- **Root uploads** — `api-gateway`'s `/upload/{file_type}` wraps the incoming
  `UploadFile` stream in a `HashingReader` and pushes it to S3 with
  `boto3.upload_fileobj`. After the upload completes, `audit_db.record_upload`
  inserts the `object_versions` row and the response includes `sha256`,
  `size_bytes`, and `audit_id`.
- **Derivatives** — workers finish by calling
  `shared.object_storage.upload_and_audit(local_path, key=…, operation=…,
  worker=…, job_id=…, parents=[("input", input_key), …],
  metadata={…})`. The helper hashes the local file, uploads it, records a
  `derived` row, and wires up the parent-child edges. Each worker's result
  dict then carries the `sha256`, `size_bytes`, and `audit_id` for that
  artifact.

If the database is unreachable the audit functions degrade silently (log +
return `None`) so a Postgres outage cannot stall the pipeline — the object
still lands in S3.

### Query endpoints (api-gateway)

All endpoints return JSON, are protected by the same `X-API-Key` header as
the rest of the gateway, and live alongside the existing job endpoints.

- `GET /lineage/{object_key:path}` — returns the full lineage tree for an
  object: the node itself, its ancestors (ultimately back to `root` uploads),
  and descendants, walked with a recursive CTE.

  ```bash
  curl -H "X-API-Key: $API_KEY" \
    "http://localhost:8100/lineage/output/csv/arch.csv"
  ```

  Response shape:

  ```json
  {
    "object_key": "output/csv/arch.csv",
    "node": { "id": 42, "sha256": "…", "operation": "ifccsv",
              "worker": "ifccsv-worker", "job_id": "…", "metadata": {…} },
    "ancestors": [ { "id": 7, "object_key": "uploads/Building-Architecture.ifc",
                     "operation": "upload", "role": "input" } ],
    "descendants": []
  }
  ```

- `GET /lineage/job/{job_id}` — all objects produced by one RQ job plus their
  direct inputs. Useful for tracing "what came out of job X".
- `GET /audit/roots?limit=50&since=<iso8601>` — paginated list of first-time
  uploads (`kind = 'root'`) ordered by `created_at DESC`. `since` is an
  optional ISO-8601 cutoff.
- `GET /audit/dedupe/{sha256}` — every object key (root or derived) currently
  mapped to a given content hash; handy for spotting duplicate uploads and
  cross-pipeline reuse.

### What the smoke test verifies

`smoke-test.sh` calls each query endpoint after the eight jobs complete and
asserts:

- The CSV, tester report, diff, JSON, converted OBJ, and patched IFC each
  have a valid `sha256`, a populated `ancestors` array with the right
  parent keys, and the expected parent count (e.g. 2 for tester/diff, 1 for
  csv/json/patch/convert).
- `/audit/roots` returns the seeded root uploads.

### Operational notes

- **Docker volumes persist.** The `02-audit.sql` migration only runs on a
  brand-new `postgres` volume. On an existing stack apply it manually:

  ```bash
  docker compose exec -T postgres \
    psql -U ifcpipeline -d ifcpipeline < postgres/init/02-audit.sql
  ```

- **Environment variables.** The gateway and every worker that writes
  derivatives need the `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`,
  `POSTGRES_USER`, and `POSTGRES_PASSWORD` env vars — they are already set
  in `docker-compose.yml`, but custom deployments must pass them through.
- **Actor tracking** is intentionally skipped in this iteration. Adding a
  `created_by` TEXT column to `object_versions` (+ a propagated request
  header) is the obvious follow-up.

## Versioned keys (day-2 overwrites)

S3 bucket versioning is enabled at bootstrap (`seaweedfs-setup` runs
`mc version enable local/<bucket>`), so overwriting a key creates a new
object with its own `VersionId` while the previous bytes remain retrievable.
The audit trail matches that model:

- Every successful PUT (root upload or worker derivative) records a fresh
  `object_versions` row carrying the S3 `VersionId`. The uniqueness
  target is the expression index
  `UNIQUE (bucket, object_key, COALESCE(version_id, sha256))`, so two
  uploads to the same key with the same bytes but different `VersionId`s
  are distinct rows.
- `GET /audit/history/{object_key:path}` returns every audited version for
  a key, newest first. Each entry carries `sha256`, `version_id`,
  `audit_id`, and the standard `kind`/`operation`/`worker` metadata.
- `GET /lineage/{object_key:path}` accepts `?audit_id=<id>` or
  `?version_id=<vid>` to anchor the lineage walk at a specific version;
  without either, it uses the newest row for the key.

### Auto-pin at enqueue

The gateway resolves and stamps a `version_id` onto every job payload
before it hits Redis. Clients don't have to think about versions — the job
processes exactly the bytes that existed at enqueue time, even if the key
is overwritten mid-flight. Callers that *do* want an exact pin (e.g. a
replay, or a deterministic CI run) can send:

```jsonc
{
  "input_file": "uploads/Arch.ifc",
  "input_version_id": "a1b2c3..."   // S3 VersionId
  // OR:
  // "input_audit_id": 42            // object_versions.id row
  // Multi-input endpoints (clash, diff) also accept:
  // "input_version_ids": { "uploads/Arch.ifc": "a1b2c3...", "uploads/Struct.ifc": "d4e5f6..." }
}
```

All custom n8n nodes (`CUSTOM.*`) expose the same three fields under an
optional **Version Pinning** collection; leaving it empty preserves the
auto-pin behaviour.

## S3-native checksums

`shared/object_storage.py` controls its hashing strategy with the
`S3_CHECKSUM_MODE` env var:

- **`native`** — asks SeaweedFS to compute SHA-256 server-side via the S3 spec's
  `ChecksumAlgorithm=SHA256`. `TransferConfig(multipart_threshold=5 GiB)`
  keeps the upload single-part so the returned checksum is a whole-object
  hash, not a tree/composite digest. If native is unavailable or returns a
  composite, the helper falls back to app-side hashing automatically.
- **`app`** *(default for compatibility)* — streams the upload through a
  `HashingReader` that accumulates the sha256 client-side.

Toggle with `S3_CHECKSUM_MODE=native` in `.env` when the backend supports
native checksums. The `app` fallback remains for backends that don't.

## GUID-level audit trail

The audit tables above track objects (files). On top of them, an optional
**GUID index** records which IFC `GlobalId`s live in which object
versions, so you can answer "where did this element go?" without a
separate BIM data lake.

### Tables (created by `postgres/init/05-guid-index.sql`)

- `object_guids (object_version_id, ifc_guid, entity_type, role)` with
  UNIQUE `(object_version_id, ifc_guid, role)`. Roles are
  `root` / `patched` / `split` / `exported` / `converted` / `qto_added` and
  `diff_added` / `diff_deleted` / `diff_changed` for ifcdiff reports.
- `tester_results (object_version_id, ifc_guid, ids_rule, passed, reason)` —
  populated directly by `ifctester-worker`, never via the generic index.
- `clash_pairs (object_version_id, guid_a, guid_b, distance, kind)` —
  populated directly by `ifcclash-worker`.

### How it's populated

`shared/object_storage.upload_and_audit` and the gateway's `/upload/{ft}`
enqueue a `tasks.index_object(audit_id, object_key, version_id, role)` job
onto the `guid_index` RQ queue after each successful PUT.
The `guid-index-worker` downloads the pinned `VersionId`, picks the right
streaming extractor from `shared/guid_extract.py` (STEP regex for
`.ifc`/`.ifczip`, `ijson` for JSON, pandas chunks for `.csv`/`.xlsx`,
classified extraction for diff reports), and batches inserts through
`audit_db.record_guids` at 5 000 rows per `execute_values` call with
`ON CONFLICT DO NOTHING`.

### Operator knob: `GUID_INDEX_MODE`

| Value | Behavior |
| --- | --- |
| `off` *(default)* | Never enqueue, no DB writes. Safe default for fresh stacks. |
| `async` | Enqueue on `guid_index`; the dedicated worker does the extraction. |
| `sync` | Extract in-process on the caller — use only for smoke tests and small installs. |

Set it per-service in `.env` / `docker-compose.yml`. Only the api-gateway
and guid-index-worker need the variable for the async path.

### Query endpoints

| Endpoint | Returns |
| --- | --- |
| `GET /guid/{guid}` | Every `object_version` the GUID appears in, newest first. `limit` capped at 1000, default 100, `after_id` cursor. |
| `GET /guid/{guid}/path?depth=N` | Recursive lineage graph around the GUID. Response contains `nodes` (each with `present: bool`), `edges` (with `parent_version_id`), and `dropped_at` edges where `parent.present && !child.present`, annotated with the operation that dropped it. |
| `GET /guid/{guid}/diffs` | Versions where the GUID was flagged with a `diff_*` role. |
| `GET /guid/{guid}/clashes` | Clash pairs referencing the GUID. |
| `GET /guid/{guid}/tester` | ifctester verdicts (`passed`/`reason`) per run. |

All endpoints accept `limit` (≤1000) and `after_id` for pagination and are
protected by the same `X-API-Key` header as the rest of the gateway.

### Backfilling existing objects

`scripts/backfill_guids.py` iterates `object_versions` and enqueues one
indexing job per row at its pinned `version_id`:

```bash
docker compose run --rm api-gateway \
  python /app/scripts/backfill_guids.py --batch-size 500
```

`--dry-run` prints what would be enqueued without touching Redis.
`--role-override <role>` forces a single role on every job. Idempotent by
the UNIQUE `(object_version_id, ifc_guid, role)` index, so re-runs are
no-ops.

## Lifecycle & retention

S3 bucket versioning keeps non-current versions indefinitely by default,
which is great for audit but does grow storage. Set an ILM rule to expire
non-current versions after N days so the audit trail keeps the *row* but
the old bytes get collected:

```bash
docker compose run --rm --entrypoint /bin/sh seaweedfs-setup -c \
  "mc alias set sw http://seaweedfs:8333 \${S3_ACCESS_KEY} \${S3_SECRET_KEY} && \
   mc ilm add --expire-noncurrent-days 90 sw/\${S3_BUCKET:-ifcpipeline}"
```

Tune the window to your compliance needs. The audit rows for expired
versions remain in Postgres (so `GET /audit/history` still shows them),
but downloads of those specific `version_id`s start returning 404.

## Still open / next steps

- **Retention policy**: configure SeaweedFS/S3 lifecycle via `mc ilm add` to expire `output/**` after N days.
- **Bucket authorization**: replace shared admin keys with per-service identities in `seaweedfs/s3.json`.
- **Revit-to-S3 handoff**: teach the Windows `revit-worker` to upload its
  finished IFC/RVT via the gateway's `/upload/ifc` endpoint so RVT files
  never need a shared CIFS mount.
- **Decommission the legacy bind mounts**: once all n8n flows are migrated to
  read `output_key`/`object_url` from the job response, the
  `./shared/uploads:/uploads` + `./shared/output:/output` volumes can be
  removed from the compose file entirely.
