# IfcPipeline — agent guide

IfcPipeline is a **multi-host** IFC processing stack.

- **Primary / control-plane VM** (`PIPELINE_LAN_IP=192.168.101.195`): runs
  `redis`, `postgres`, `seaweedfs`, `api-gateway`, `guid-index-worker`, and
  **local** RQ workers (`ifc5d`, `ifcconvert`, `ifccsv`, `ifcfast`, `ifc2json`,
  `ifcfrag`, `ifccoord`, `topologicpy`).
- **Worker VM `bimbotw1`** (`WORKER_VM_IP=192.168.109.54`,
  repo `/home/bimbot-w1/apps/ifcpipeline`): runs **remote** RQ workers
  (`ifcclash`, `ifcdiff`, `ifcpatch`, `ifctester`) via
  `docker-compose.remote-workers.yml --env-file .env.remote`. They reach
  redis/postgres/seaweedfs over the LAN.

## ⚠️ Critical: control-plane rebuilds need the host-lan overlay

`docker-compose.host-lan.yml` publishes redis/postgres/seaweedfs on
`PIPELINE_LAN_IP`. The base `docker-compose.yml` does **not**. Running
`docker compose up -d` on the primary host **without** the overlay makes Compose
recreate those services back to loopback-only, **strips the LAN bindings**, and
breaks every remote worker (`Error 111 ... Connection refused`) — even when you
only target an unrelated service like `api-gateway`.

**Always** pass both files on the primary host:

```bash
cd /home/bimbot-ubuntu/apps/ifcpipeline
docker compose -f docker-compose.yml -f docker-compose.host-lan.yml up -d <service>
```

Verify after any recreate (must show `192.168.101.195`):

```bash
docker ps --format '{{.Names}}: {{.Ports}}' | grep -E 'ifcpipeline-(redis|seaweedfs)|postgres_objectstorage'
```

For the full rebuild/recovery playbook see the **host-lan-overlay-rebuild**
skill (`.cursor/skills/host-lan-overlay-rebuild/SKILL.md`) and the always-on
rule (`.cursor/rules/host_lan_overlay.mdc`).

## ⚠️ Firewall: scope DOCKER-USER rules to the external NIC

LAN ports (6379/5432/8333) are locked to the worker VM via `DOCKER-USER`. Because
`net.bridge.bridge-nf-call-iptables=1`, those rules MUST be scoped to the
external interface (the NIC holding `PIPELINE_LAN_IP`) — an interface-agnostic
`--dport DROP` also drops internal container-to-container traffic (api-gateway↔
postgres/redis, n8n↔postgres), making internal connections **time out** and
n8n crash-loop. Apply/repair with `sudo ./scripts/apply-host-lan-firewall.sh`.

## Object storage (S3 / SeaweedFS)

Object storage is **SeaweedFS** (S3-compatible, service `seaweedfs:8333`);
MinIO was decommissioned 2026-06. S3 identities live in `seaweedfs/s3.json`
(gitignored; keep in sync with `.env` `S3_ACCESS_KEY`/`S3_SECRET_KEY`).

Workers honor `USE_OBJECT_STORAGE=true`: inputs are staged from S3 and
artifacts uploaded back via `shared.object_storage` (`download_to_path`,
`upload_and_audit`). Worker images bake `shared/` + `tasks.py`, so changes
require syncing to the worker VM repo and rebuilding that worker image.

## External archives (outside this repo)

Research and decommissioned pilot tooling live in sibling directories / fork repos,
not on `main`:

- **MinIO pilot** — `../ifcpipeline-minio-pilot-archive/` (local)
- **IfcOpenShell hunt (May 2026)** —
  [`ifcpipeline-ifcopenshell-hunt-archive`](https://github.com/jonatanjacobsson/ifcpipeline-ifcopenshell-hunt-archive)
  (GitHub; clone to `../ifcpipeline-ifcopenshell-hunt-archive/` on dev hosts)
