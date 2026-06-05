# ifcpipeline deployment

How to run the stack on a **primary host** (control plane + optional local workers) and on **worker host(s)** (RQ consumers only). Use generic host names in configuration; set real hostnames and IPs in `.env` / `.env.remote` on each machine.

## Compose layout

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Combined entry (`include` control plane + workers) — **development default** |
| `docker-compose.control-plane.yml` | API, Redis, Postgres, MinIO, n8n, dashboards, viewer, cleanup |
| `docker-compose.workers.yml` | All nine `*-worker` services (canonical definitions) |
| `docker-compose.remote-workers.yml` | Worker host: `include` workers + `remote` profile + external env |
| `docker-compose.host-lan.yml` | Primary overlay: publish Redis/Postgres/MinIO on `PIPELINE_LAN_IP` |
| `docker-compose.test.yml` | Smoke-test overlay (slim gateway `depends_on`) |

Worker services: `ifc5d`, `ifcpatch`, `ifcconvert`, `ifcclash`, `ifccsv`, `ifctester`, `ifcdiff`, `ifc2json`, `guid-index`.

## Three deployment recipes

### 1. Combined (dev / single host) — default

Runs control plane and all workers. Same service names and volume keys as before the monolithic compose file.

```bash
cd /path/to/ifcpipeline
./scripts/compose-up-combined.sh
# equivalent:
docker compose up -d
```

### 2. Control plane only (primary / production control host)

No worker containers on this machine; jobs are enqueued here and consumed elsewhere.

```bash
./scripts/compose-up-control-plane.sh
# equivalent:
docker compose -f docker-compose.control-plane.yml -f docker-compose.host-lan.yml up -d
```

Apply LAN publish and firewall before remote workers connect (see [Remote workers](#remote-workers)).

### 3. Workers on worker host(s)

Worker-only project (`name: ifcpipeline-remote`). Requires `.env.remote` with `REDIS_URL`, `POSTGRES_HOST`, and `S3_ENDPOINT_URL` pointing at the primary LAN IP — not `redis` / `minio` Docker service names.

```bash
# On worker host (after images are pushed from primary):
./scripts/start-remote-workers.sh
# equivalent:
COMPOSE_PROFILES=remote docker compose -f docker-compose.remote-workers.yml \
  --env-file .env.remote up -d \
  ifctester-worker ifcpatch-worker ifcclash-worker ifcdiff-worker
```

Multiple worker VMs use the same recipe; set `REMOTE_SSH` / `REMOTE_REPO` per host in deploy scripts.

---

## Data preservation

**Goal:** Split compose and run workers on a second VM without wiping Postgres, MinIO, Redis, or n8n state on the primary dev machine.

### Where state lives

| Asset | Storage | Notes |
|-------|---------|--------|
| Postgres (pipeline + n8n) | Named volume `postgres-data` | `container_name: ifc_pipeline_postgres_objectstorage` |
| MinIO / S3 | Named volume `minio-data` | Workers and API use buckets via env |
| Redis (RQ) | Named volume `redis-data` | Queue and failed-job registry |
| n8n | Bind mount `./n8n-data` | Workflows, credentials, custom nodes |
| Worker containers | Ephemeral | Safe to recreate |

Worker hosts **must not** run `postgres`, `redis`, or `minio` services. Use `.env.remote` only. Worker compose project name is `ifcpipeline-remote` so it never creates competing `postgres-data` volumes.

### Compose invariants (do not break)

1. **Same project on primary** — Run from repo root; default project name is the directory name (`ifcpipeline`). Do not add a conflicting top-level `name:` on the combined entry.
2. **Same service names** — Unchanged across split files.
3. **Same volume keys** — `postgres-data`, `minio-data`, `redis-data` declared in control-plane file only.
4. **Same bind mounts** — Paths relative to repo root unchanged (`./n8n-data`, `./ifcpatch-worker/custom_recipes`, etc.).
5. **Postgres container name** — Keep `ifc_pipeline_postgres_objectstorage`.

### Pitfalls

| Pitfall | Symptom | Prevention |
|---------|---------|------------|
| `docker compose down -v` on primary | All named volumes deleted | **Never** use `-v` on primary; scripts and docs forbid it |
| New `name:` / different project on primary | Empty Postgres/MinIO | Keep `COMPOSE_PROJECT_NAME` stable |
| Renamed `postgres` / `redis` / `minio` | Orphaned volumes, empty DB | Mechanical move only — no renames |
| Full stack on worker with local `.env` | Second Postgres/MinIO on worker | Worker scripts: workers compose + `.env.remote` only |
| `up` from wrong directory | New project, new volumes | Scripts `cd` to repo root |
| Postgres `container_name` change | Second postgres container | Keep fixed name on control-plane |
| Init scripts on empty volume | Fresh DB | Do not recreate `postgres-data` |

### Pre-migration checklist (primary host)

```bash
cd /path/to/ifcpipeline
docker compose ps
docker volume ls | grep -E 'postgres|minio|redis|ifcpipeline'
# Optional backup:
docker compose exec -T postgres pg_dumpall -U "${POSTGRES_USER:-ifcpipeline}" > backup-pre-split.sql
tar -czf n8n-data-backup-pre-split.tar.gz n8n-data/
docker compose ls   # note COMPOSE_PROJECT_NAME and volume names
```

### Post-migration verification (primary host)

```bash
docker compose ps
docker volume ls | grep postgres    # same volume name as before
docker compose exec -T postgres psql -U ifcpipeline -c '\l'
# Spot-check n8n UI and MinIO bucket contents
```

Expect **container recreate**, not **volume replacement**. If Postgres logs show fresh init on an empty data dir, stop and fix project/volume names.

### Worker VM rollout (no impact on primary data)

1. Build/push images from primary (does not touch volumes).
2. Worker host: `SKIP_BUILD=1 ./scripts/start-remote-workers.sh` with `.env.remote` pointing at primary LAN.
3. Optionally scale down primary worker replicas once remote is healthy.

```bash
docker compose up -d --scale ifctester-worker=0 --scale ifcpatch-worker=0 \
  --scale ifcclash-worker=0 --scale ifcdiff-worker=0
```

---

## Remote workers

Run **ifctester**, **ifcpatch**, **ifcclash**, and **ifcdiff** on a worker host while the control plane stays on the primary host. Workers connect over TCP to Redis, Postgres, and MinIO on `PIPELINE_LAN_IP`.

### Files

| File | Purpose |
|------|---------|
| `docker-compose.host-lan.yml` | Primary: publish 6379 / 5432 / 9000 on LAN IP |
| `docker-compose.remote-workers.yml` | Worker: `include` workers + `remote` profile |
| `.env.remote.example` | Template for worker `.env.remote` |
| `scripts/apply-host-lan-access.sh` | Apply host-lan on primary |
| `scripts/start-remote-workers.sh` | Preflight + start on worker |
| `scripts/deploy-remote-workers-from-primary.sh` | rsync + push images + SSH start |

### One-time setup

**Primary host** — add to `.env`:

```bash
PIPELINE_LAN_IP=<primary-lan-ip>
WORKER_VM_IP=<worker-lan-ip>
PIPELINE_HOST=<primary-hostname>
```

```bash
./scripts/apply-host-lan-access.sh
# Restrict firewall to worker IP (example):
sudo ufw allow from <worker-lan-ip> to any port 6379 proto tcp
sudo ufw allow from <worker-lan-ip> to any port 5432 proto tcp
sudo ufw allow from <worker-lan-ip> to any port 9000 proto tcp
ssh-copy-id deploy@worker-host
```

**Worker host** — Docker Engine + Compose plugin; user in `docker` group; enough RAM (~12G for clash + patch + diff).

### Deploy from primary

```bash
cd /path/to/ifcpipeline
./scripts/setup-remote-workers.sh
```

Requires `.env` on primary. Step-by-step: `./scripts/deploy-remote-workers-from-primary.sh` (needs passwordless `REMOTE_SSH`, default `deploy@worker-host`).

On worker after manual rsync:

```bash
cp .env.remote.example .env.remote   # edit secrets
./scripts/start-remote-workers.sh    # requires .env.remote on worker
```

### Capacity

- **Additive (default):** keep primary workers running; RQ shares load.
- **More replicas on worker:** set `IFCTESTER_REMOTE_REPLICAS`, `IFCPATCH_REMOTE_REPLICAS`, etc. in `.env.remote`, then `./scripts/start-remote-workers.sh`.
- **Migrate load off primary:** scale primary workers to 0 (see above).

### Verification

```bash
./scripts/test-remote-workers.sh
COMPOSE_PROFILES=remote docker compose -f docker-compose.remote-workers.yml --env-file .env.remote ps
```

From worker: `redis-cli -h <primary-lan-ip> ping`, MinIO health on `:9000`.

### Troubleshooting

| Symptom | Check |
|---------|--------|
| Connection refused to Redis/MinIO | Re-run `./scripts/apply-host-lan-access.sh`; update `.env.remote` with current `PIPELINE_LAN_IP` |
| Postgres auth fails | `POSTGRES_PASSWORD` matches primary `.env` |
| S3 errors | `S3_ENDPOINT_URL` uses primary LAN IP, not `http://minio:9000` |
| Deploy SSH fails | Run from your terminal; `ssh -o RemoteCommand=none deploy@worker-host` |
| Worker disk full on build | Use `push-worker-images-to-remote.sh` + `SKIP_BUILD=1` |

SeaweedFS shadow: remote workers do not need `S3_SHADOW_*` unless Seaweed S3 is also published on `PIPELINE_LAN_IP`.

---

## Helper scripts

| Script | Role |
|--------|------|
| `scripts/compose-up-combined.sh` | Primary: full stack (never `down -v`) |
| `scripts/compose-up-control-plane.sh` | Primary: control plane + host-lan |
| `scripts/build-worker-images.sh` | Build worker images on primary |
| `scripts/verify-compose-volumes.sh` | Compare volume names (guardrail) |
| `scripts/start-remote-workers.sh` | Worker: remote profile + `.env.remote` |
| `scripts/deploy-remote-workers-from-primary.sh` | Primary → worker deploy |

## Validate compose locally

```bash
docker compose config
docker compose -f docker-compose.control-plane.yml config
COMPOSE_PROFILES=remote docker compose -f docker-compose.remote-workers.yml --env-file .env.remote.example config
```

`docker-compose.workers.yml` alone references `redis` / `postgres` / `minio` — validate it merged with control-plane or via the combined root file.
