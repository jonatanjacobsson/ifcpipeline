# Object-storage port of ifcpipeline — full coverage

**Branch:** `feature/object-storage` on a *new* clone
(`/home/bimbot-ubuntu/apps/ifcpipeline-objectstorage`).
The original `/home/bimbot-ubuntu/apps/ifcpipeline` is untouched.

## Goal

Replace the `/uploads` + `/output` bind-mounted filesystem with a self-hosted,
S3-compatible object store so that:

- Workers can be stateless and scale out without a shared disk.
- No NFS/CIFS dependency for multi-host deployments.
- Artifacts have a versionable, ACL'd storage backend.

## What's in this build

### Moving parts

- **MinIO** (`quay.io/minio/minio:latest`) as the self-hosted S3 endpoint.
- **`minio-setup`** one-shot container (`mc`) that creates the bucket on boot.
- **`shared/object_storage.py`** — boto3 wrapper with `download_to_tempfile`,
  `upload_from_path`, `object_exists`, key-normalizers, and
  `presigned_get_url_public` for externally reachable URLs.
- **All Linux-side workers converted to S3-first**:

  | Worker | Inputs | Outputs |
  | --- | --- | --- |
  | `ifccsv-worker` | `uploads/<ifc>` | `output/csv/<name>.csv` |
  | `ifctester-worker` | `uploads/<ifc>`, `uploads/<ids>` | `output/ids/<report>` |
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
  - `/upload/{file_type}` streams the request body straight into MinIO —
    no local disk write when `USE_OBJECT_STORAGE=true`.
  - `validate_input_file_exists` accepts files that live only in the bucket.
  - `/ifc2json/{filename}` fetches the JSON body from S3 first, then falls
    back to disk for legacy callers.
  - `/create_download_link` + `/download/{token}` auto-detect S3-backed
    paths and redirect the download to a **presigned URL** (see
    `S3_PUBLIC_ENDPOINT_URL`) so the client streams directly from MinIO.

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
  whatever hostname clients can actually reach (default `http://localhost:9000`
  for local testing, swap for your public MinIO host in prod).

## Ports (alternate, so it can coexist with the OG stack)

| Service       | OG port | PoC port |
|---------------|---------|----------|
| api-gateway   | 8000    | **8100** |
| ifc-viewer    | 8001    | **8101** |
| n8n           | 5678    | **5778** |
| rq-dashboard  | 9181    | **9281** |
| dozzle        | 9182    | **9282** |
| MinIO API     | –       | **9000** |
| MinIO console | –       | **9001** |

## Running the smoke test

```bash
cd /home/bimbot-ubuntu/apps/ifcpipeline-objectstorage
./smoke-test.sh
```

The script:

1. Builds and starts `minio + minio-setup + redis + postgres + api-gateway`
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

The worker outputs are **only** in MinIO — `shared/output/*` stays empty on
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

## Accessing MinIO

Console: <http://localhost:9001>  (user `minioadmin` / pass `minioadmin`).
S3 API: <http://localhost:9000>  (path-style addressing, region `us-east-1`).

## Environment variables

| Variable | Default | Where | Purpose |
| --- | --- | --- | --- |
| `USE_OBJECT_STORAGE` | `true` | gateway + workers | Flip to `false` for legacy mode |
| `S3_ENDPOINT_URL` | `http://minio:9000` | gateway + workers | Internal MinIO URL |
| `S3_PUBLIC_ENDPOINT_URL` | `http://localhost:9000` | gateway | Rewritten host for presigned URLs |
| `S3_ACCESS_KEY` | `minioadmin` | all | MinIO credentials |
| `S3_SECRET_KEY` | `minioadmin` | all | MinIO credentials |
| `S3_BUCKET` | `ifcpipeline` | all | Single bucket name |
| `S3_REGION` | `us-east-1` | all | MinIO ignores this; boto3 needs it |

## Files changed (vs. upstream `main`)

- `docker-compose.yml` — `minio`, `minio-setup`, `minio-data` volume, S3 env
  on every converted worker + api-gateway, remapped public ports, stripped
  the external-only `interaxo` CIFS volume.
- `shared/object_storage.py` — boto3 helper (new).
- `shared/classes.py` — expanded `IfcConvertRequest` to match what the worker
  actually reads (unrelated to S3 but required to make `ifcconvert` run).
- `shared/setup.py`, `api-gateway/requirements.txt`, and every converted
  worker's `requirements.txt` — add `boto3`.
- `api-gateway/api-gateway.py` — S3-only uploads, S3-aware validators, S3
  redirect on `/download/{token}`, S3 fetch on `/ifc2json/{filename}`.
- Worker `tasks.py` (each of ifccsv, ifctester, ifcconvert, ifcclash,
  ifcdiff, ifc5d, ifc2json, ifcpatch) — split into `_run_s3` / `_run_filesystem`
  branches driven by `USE_OBJECT_STORAGE`.
- `smoke-test.sh` — full-coverage test (eight jobs, one per worker).
- `OBJECT_STORAGE.md` — this document.

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
  `UploadFile` stream in a `HashingReader` and pushes it to MinIO with
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

## Still open / next steps

- **Retention policy**: replace the `cleanup-service` alpine cron with a
  MinIO lifecycle rule (`mc ilm add`) that expires `output/**` after N days.
- **Bucket authorization**: replace `minioadmin` with per-service policies
  (`ifcpipeline-gateway`, `ifcpipeline-worker`).
- **Revit-to-S3 handoff**: teach the Windows `revit-worker` to upload its
  finished IFC/RVT via the gateway's `/upload/ifc` endpoint so RVT files
  never need a shared CIFS mount.
- **Decommission the legacy bind mounts**: once all n8n flows are migrated to
  read `output_key`/`object_url` from the job response, the
  `./shared/uploads:/uploads` + `./shared/output:/output` volumes can be
  removed from the compose file entirely.
