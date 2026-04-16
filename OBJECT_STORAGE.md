# Object-storage PoC — evaluating a file-less ifcpipeline

**Branch:** `feature/object-storage` on a *new* clone
(`/home/bimbot-ubuntu/apps/ifcpipeline-objectstorage`).
The original `/home/bimbot-ubuntu/apps/ifcpipeline` is untouched.

## Goal

Evaluate replacing the `/uploads` + `/output` bind-mounted filesystem with a
self-hosted, S3-compatible object store so that:

- Workers can be stateless and scale out without a shared disk
- No NFS/CIFS dependency for multi-host deployments
- Artifacts have a versionable, ACL'd storage backend

## What's in this PoC

### Moving parts

- **MinIO** (`quay.io/minio/minio:latest`) as the self-hosted S3 endpoint.
- **`minio-setup`** one-shot container (`mc`) that creates the bucket on boot.
- **`shared/object_storage.py`**: tiny boto3 wrapper — `download_to_tempfile`,
  `upload_from_path`, `object_exists`, `ensure_bucket`, key builders.
- Two workers converted to S3-first:
  - `ifccsv-worker` (IFC → CSV/XLSX/ODS export + import)
  - `ifctester-worker` (IFC + IDS → validation report)
- `api-gateway /upload/{file_type}` dual-writes (disk + S3) so un-converted
  workers keep working.
- `validate_input_file_exists` accepts files that live only in the bucket.

### Design choices (deliberately minimal)

- Feature flag `USE_OBJECT_STORAGE=true|false`. When false, the converted
  workers fall back to the legacy filesystem behaviour. Same code, two
  modes — good for A/B comparison.
- Single bucket `ifcpipeline` with key prefixes mirroring the original folder
  layout: `uploads/…`, `output/csv/…`, `output/ids/…`, `output/ifc_updated/…`.
  Makes the two stacks trivially diff-able.
- Workers pull objects into a `NamedTemporaryFile`, run the ifcopenshell
  library against that path, then push the result back up. The S3 path is a
  replacement for the bind-mounts — no API or queue shape changes.

## Ports (alternate, so it can coexist with the OG stack)

| Service      | OG port | PoC port |
|--------------|---------|----------|
| api-gateway  | 8000    | **8100** |
| ifc-viewer   | 8001    | **8101** |
| n8n          | 5678    | **5778** |
| rq-dashboard | 9181    | **9281** |
| dozzle       | 9182    | **9282** |
| MinIO API    | –       | **9000** |
| MinIO console| –       | **9001** |

## Running the smoke test

```bash
cd /home/bimbot-ubuntu/apps/ifcpipeline-objectstorage
./smoke-test.sh
```

The script:
1. Boots `minio + minio-setup + redis + postgres + api-gateway + ifccsv-worker + ifctester-worker`.
2. Uploads `shared/examples/Building-Architecture.ifc` and `IDS-example.ids`.
3. Enqueues an ifccsv export and an ifctester validation.
4. Polls for completion.
5. Lists objects in the bucket.

A successful run ends with something like:

```
[2026-04-16 16:36:57 UTC]   525B STANDARD output/csv/arch.csv
[2026-04-16 16:36:57 UTC] 8.0KiB STANDARD output/ids/report.json
[2026-04-16 16:36:56 UTC] 219KiB STANDARD uploads/Building-Architecture.ifc
[2026-04-16 16:36:56 UTC] 2.0KiB STANDARD uploads/IDS-example.ids
```

The worker outputs are **only** in MinIO — `shared/output/csv` and
`shared/output/ids` stay empty on the host.

## Accessing MinIO

Console: <http://localhost:9001>  (user `minioadmin` / pass `minioadmin`).
S3 API: <http://localhost:9000>  (path-style addressing, region `us-east-1`).

## What would the full rollout look like

For each remaining worker, the pattern is almost mechanical:
1. Add `boto3` to its `requirements.txt`.
2. Wrap input reads with `s3.download_to_tempfile(key, suffix=…)`.
3. Wrap output writes with a local temp file + `s3.upload_from_path(…, key)`.
4. Return a dict that includes `storage/bucket/output_key` alongside the
   legacy `output_path` so the n8n nodes can be migrated one at a time.

Endpoints that currently hand out presigned-like tokens
(`/create_download_link`, `/download/{token}`) should be redirected to S3
presigned URLs to avoid streaming files through the gateway. That's the
obvious next step and is out of scope for this minimal PoC.

## Files changed (vs. the clone's `main`)

- `docker-compose.yml` — add `minio`, `minio-setup`, `minio-data` volume, S3
  env vars on api-gateway / ifccsv-worker / ifctester-worker; remap public
  ports; strip the `interaxo` CIFS volume (project-specific, not needed here).
- `docker-compose.test.yml` — minimal overlay for the smoke test.
- `shared/object_storage.py` — new helper.
- `shared/setup.py` — add `boto3` dependency.
- `api-gateway/requirements.txt` — add `boto3`.
- `api-gateway/api-gateway.py` — dual-write on upload, S3-aware validation.
- `ifccsv-worker/requirements.txt`, `ifccsv-worker/tasks.py` — S3 path.
- `ifctester-worker/requirements.txt`, `ifctester-worker/tasks.py` — S3 path.
- `smoke-test.sh` — end-to-end verification.
- `OBJECT_STORAGE.md` — this document.
