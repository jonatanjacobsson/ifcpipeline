# ifcpipeline deployment

How to run the stack on a **primary host** (control plane + optional local workers) and on **worker host(s)** (RQ consumers only). Use generic host names in configuration; set real hostnames and IPs in `.env` / `.env.remote` on each machine.

## Compose layout

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Combined entry (`include` control plane + workers) — **development default** |
| `docker-compose.control-plane.yml` | API, Redis, Postgres, SeaweedFS, n8n, dashboards, viewer, cleanup |
| `docker-compose.workers.yml` | All twelve `*-worker` services (canonical definitions) |
| `docker-compose.remote-workers.yml` | Worker host: `include` workers + `remote` profile + external env |
| `docker-compose.host-lan.yml` | Primary overlay: publish Redis/Postgres/SeaweedFS S3 on `PIPELINE_LAN_IP` |
| `docker-compose.test.yml` | Smoke-test overlay (slim gateway `depends_on`) |

### Worker placement

| Host | Compose | Workers |
|------|---------|---------|
| **Primary** | `docker-compose.yml` or control plane + workers | `ifc5d`, `ifcconvert`, `ifccsv`, `ifcfast`, `ifc2json`, `ifcfrag`, `ifccoord`, `topologicpy` (+ optional duplicate remote workers during migration) |
| **Primary control plane** | `docker-compose.control-plane.yml` | `guid-index-worker` (not in `workers.yml`) |
| **Worker VM** | `docker-compose.remote-workers.yml` | `ifctester`, `ifcpatch`, `ifcclash`, `ifcdiff`, `ifccoord`, `topologicpy` (the `remote` profile) |

All twelve RQ workers in `docker-compose.workers.yml`: `ifc5d`, `ifcpatch`,
`ifcconvert`, `ifcclash`, `ifccsv`, `ifcfast`, `ifctester`, `ifcdiff`,
`ifc2json`, `ifcfrag`, `ifccoord`, `topologicpy`.

Build on primary: `./scripts/build-worker-images.sh` (`WORKER_BUILD_TARGET=all|primary|remote`).

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

Worker-only project (`name: ifcpipeline-w1`, set `REMOTE_COMPOSE_PROJECT` in `.env.remote` per VM). Requires `.env.remote` with `REDIS_URL`, `POSTGRES_HOST`, and `S3_ENDPOINT_URL` pointing at the primary LAN IP — not `redis` / `seaweedfs` Docker service names.

```bash
# On worker host (after images are pushed from primary):
./scripts/start-remote-workers.sh
# equivalent (all six remote workers — keep this list in sync with the script's REMOTE_SERVICES):
COMPOSE_PROFILES=remote docker compose -f docker-compose.remote-workers.yml \
  --env-file .env.remote up -d \
  ifctester-worker ifcpatch-worker ifcclash-worker ifcdiff-worker ifccoord-worker topologicpy-worker
```

> **Prefer `start-remote-workers.sh`.** It deploys the full `REMOTE_SERVICES`
> set (the six workers above + `dozzle-agent`) and applies replica scaling.
> A hand-typed `up -d` that omits a worker is the usual reason a queue ends up
> with no consumer on the worker VM.

Multiple worker VMs use the same recipe; set `REMOTE_SSH` / `REMOTE_REPO` per host in deploy scripts.

---

## Data preservation

**Goal:** Split compose and run workers on a second VM without wiping Postgres, SeaweedFS, Redis, or n8n state on the primary dev machine.

### Where state lives

| Asset | Storage | Notes |
|-------|---------|--------|
| Postgres (pipeline + n8n) | Named volume `postgres-data` | `container_name: ifc_pipeline_postgres_objectstorage` |
| SeaweedFS / S3 | Named volume `seaweedfs-data` | Workers and API use buckets via env; identities in `seaweedfs/s3.json` |
| Redis (RQ) | Named volume `redis-data` | Queue and failed-job registry |
| n8n | Bind mount `./n8n-data` | Workflows, credentials, custom nodes |
| Worker containers | Ephemeral | Safe to recreate |

Worker hosts **must not** run `postgres`, `redis`, or `seaweedfs` services. Use `.env.remote` only. Worker compose project name is `ifcpipeline-w1` (or `REMOTE_COMPOSE_PROJECT`) so it never creates competing `postgres-data` volumes.

### Compose invariants (do not break)

1. **Same project on primary** — Run from repo root; default project name is the directory name (`ifcpipeline`). Do not add a conflicting top-level `name:` on the combined entry.
2. **Same service names** — Unchanged across split files.
3. **Same volume keys** — `postgres-data`, `seaweedfs-data`, `redis-data` declared in control-plane file only.
4. **Same bind mounts** — Paths relative to repo root unchanged (`./n8n-data`, `./ifcpatch-worker/custom_recipes`, etc.).
5. **Postgres container name** — Keep `ifc_pipeline_postgres_objectstorage`.

### Pitfalls

| Pitfall | Symptom | Prevention |
|---------|---------|------------|
| `docker compose down -v` on primary | All named volumes deleted | **Never** use `-v` on primary; scripts and docs forbid it |
| New `name:` / different project on primary | Empty Postgres/SeaweedFS | Keep `COMPOSE_PROJECT_NAME` stable |
| Renamed `postgres` / `redis` / `seaweedfs` | Orphaned volumes, empty DB | Mechanical move only — no renames |
| Full stack on worker with local `.env` | Second Postgres/SeaweedFS on worker | Worker scripts: workers compose + `.env.remote` only |
| `up` from wrong directory | New project, new volumes | Scripts `cd` to repo root |
| Postgres `container_name` change | Second postgres container | Keep fixed name on control-plane |
| Init scripts on empty volume | Fresh DB | Do not recreate `postgres-data` |

### Pre-migration checklist (primary host)

```bash
cd /path/to/ifcpipeline
docker compose ps
docker volume ls | grep -E 'postgres|seaweedfs|redis|ifcpipeline'
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
# Spot-check n8n UI and SeaweedFS bucket contents
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

Run the `remote`-profile workers — **ifctester**, **ifcpatch**, **ifcclash**, **ifcdiff**, **ifccoord**, **topologicpy** — on a worker host while the control plane stays on the primary host. Workers connect over TCP to Redis, Postgres, and SeaweedFS S3 on `PIPELINE_LAN_IP`.

### Files

| File | Purpose |
|------|---------|
| `docker-compose.host-lan.yml` | Primary: publish 6379 / 5432 / 8333 on LAN IP |
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
sudo ufw allow from <worker-lan-ip> to any port 8333 proto tcp
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

From worker: `redis-cli -h <primary-lan-ip> ping`, `nc -z <primary-lan-ip> 8333` (SeaweedFS S3).

### Worker health check (track expected vs actual)

Use this when "the worker VM is missing workers" or workers crash-loop. It
answers three questions: are all expected containers up, are they registered in
the broker, and are they pointing at the **current** primary LAN IP.

```bash
# 1. On the worker VM: every remote worker should be `running` with restarts not climbing.
#    `restarting` (or a RestartCount that keeps growing) means it cannot reach the broker.
for c in ifctester ifcpatch ifcclash ifcdiff ifccoord topologicpy; do
  n=ifcpipeline-w1-$c-worker-1
  docker inspect -f "$c: {{.State.Status}} restarts={{.RestartCount}}" "$n"
done

# 2. On the worker VM: confirm each container's baked-in REDIS_URL matches the CURRENT primary LAN IP.
#    A stale IP here (from a DHCP change) is the classic crash-loop cause — see note below.
for c in ifctester ifcpatch ifcclash ifcdiff ifccoord topologicpy; do
  docker inspect -f "$c: {{range .Config.Env}}{{println .}}{{end}}" ifcpipeline-w1-$c-worker-1 \
    | grep '^REDIS_URL='
done

# 3. On the primary: which queues actually have a registered consumer (across all hosts).
#    Each remote queue should show at least 1; with primary duplicates it shows more.
RID=$(docker ps -qf name=ifcpipeline-redis | head -1)
for k in $(docker exec "$RID" redis-cli smembers rq:workers | grep '^rq:worker:'); do
  docker exec "$RID" redis-cli hget "$k" queues
done | sort | uniq -c
```

Expected remote queues registered: `ifctester`, `ifcpatch`, `ifcclash`,
`ifcdiff`, `ifccoord`, `topologicpy-worker`. `ifccoord` runs **only** on the
worker VM, so if it is missing the queue has zero consumers anywhere.

> **Primary LAN IP changed → recreate, do not restart.** Worker containers bake
> `REDIS_URL` / `POSTGRES_HOST` / `S3_ENDPOINT_URL` in at create time. After the
> primary's DHCP lease changes its IP, `docker restart` reuses the old (dead) IP
> and the worker crash-loops with `redis.exceptions.TimeoutError: Timeout
> connecting to server`. You must **recreate** so containers re-read
> `.env.remote`. Two separate things must both be fixed:
> 1. **Primary** republishes the broker on the new IP: `./scripts/apply-host-lan-access.sh`
>    (a plain `restart` of `redis`/`postgres`/`seaweedfs` drops the `host-lan` port mapping).
> 2. **Worker VM** picks up the new IP: `./scripts/deploy-remote-workers-from-primary.sh`
>    from the primary, or on the worker VM:
>    `COMPOSE_PROFILES=remote docker compose -f docker-compose.remote-workers.yml --env-file .env.remote up -d --force-recreate`.
>
> A reserved/static DHCP lease for the primary avoids this class of breakage entirely.

### Troubleshooting

| Symptom | Check |
|---------|--------|
| Connection refused to Redis/SeaweedFS | Re-run `./scripts/apply-host-lan-access.sh`; update `.env.remote` with current `PIPELINE_LAN_IP` |
| Workers crash-loop with `redis ... Timeout connecting to server` after a primary reboot/IP change | Stale IP baked into containers. **Recreate** (not restart) — see [Worker health check](#worker-health-check-track-expected-vs-actual) |
| Worker VM missing a worker / queue has no consumer | Started via hand-typed `up -d` that omitted a service; use `./scripts/start-remote-workers.sh`. Verify with the broker queue check above |
| Broker port unreachable from worker though host firewall is open | `redis`/`postgres`/`seaweedfs` was recreated without the `host-lan` overlay (shows `6379/tcp`, no `0.0.0.0:6379->`). Re-run `./scripts/apply-host-lan-access.sh` |
| Postgres auth fails | `POSTGRES_PASSWORD` matches primary `.env` |
| S3 errors | `S3_ENDPOINT_URL` uses primary LAN IP `:8333`, not `http://seaweedfs:8333` |
| Deploy SSH fails | Run from your terminal; `ssh -o RemoteCommand=none deploy@worker-host` |
| Worker disk full on build | Use `push-worker-images-to-remote.sh` + `SKIP_BUILD=1` |

SeaweedFS shadow dual-write vars (`S3_SHADOW_*`) are obsolete; leave them empty.

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

`docker-compose.workers.yml` alone references `redis` / `postgres` / `seaweedfs` — validate it merged with control-plane or via the combined root file.
