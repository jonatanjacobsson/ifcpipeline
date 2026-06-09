---
name: host-lan-overlay-rebuild
description: Safely rebuild or recreate IfcPipeline control-plane containers (redis, postgres, seaweedfs, api-gateway, workers) on the primary VM without breaking remote workers. Use when rebuilding, recreating, or running docker compose up/build for the ifcpipeline stack, or after a recreate left remote workers with Connection refused.
---

# IfcPipeline control-plane rebuilds with the host-lan overlay

IfcPipeline is a multi-host stack. The control plane (redis, postgres, seaweedfs,
api-gateway) runs on the **primary VM** at `PIPELINE_LAN_IP=192.168.101.195`.
Remote workers run on **bimbotw1** (`192.168.109.54`) and connect to
redis/postgres/seaweedfs over the LAN. (Object storage is **SeaweedFS** on
port 8333; MinIO was decommissioned 2026-06.)

`docker-compose.host-lan.yml` adds the `PIPELINE_LAN_IP` port bindings;
`docker-compose.yml` does not. Running `docker compose up -d` **without** the
overlay makes Compose recreate redis/seaweedfs/postgres back to loopback-only,
**stripping the LAN bindings** and breaking every remote worker with
`Error 111 ... Connection refused`. This happens even when you only target an
unrelated service like `api-gateway`, because Compose reconciles dependencies.

## Golden rule

On the primary host, ALWAYS pass both compose files:

```bash
cd /home/bimbot-ubuntu/apps/ifcpipeline
docker compose -f docker-compose.yml -f docker-compose.host-lan.yml <build|up -d> <service>
```

## Rebuild workflow

```
- [ ] 1. cd /home/bimbot-ubuntu/apps/ifcpipeline
- [ ] 2. build (if image/code changed) WITH overlay
- [ ] 3. up -d WITH overlay
- [ ] 4. verify LAN bindings present
- [ ] 5. verify remote workers still connected
```

**Step 2 + 3 — build and recreate (example: api-gateway):**

```bash
docker compose -f docker-compose.yml -f docker-compose.host-lan.yml build api-gateway
docker compose -f docker-compose.yml -f docker-compose.host-lan.yml up -d api-gateway
```

**Step 4 — verify LAN bindings (must show 192.168.101.195):**

```bash
docker ps --format '{{.Names}}: {{.Ports}}' | grep -E 'ifcpipeline-(redis|seaweedfs)|postgres_objectstorage'
```

**Step 5 — verify remote worker queues are registered:**

```bash
for q in ifcclash ifcdiff ifcpatch ifctester; do
  echo -n "$q: "; docker exec ifcpipeline-redis-1 redis-cli --raw SCARD "rq:workers:$q"; done
```

## Recovery: LAN bindings were stripped

```bash
cd /home/bimbot-ubuntu/apps/ifcpipeline
docker compose -f docker-compose.yml -f docker-compose.host-lan.yml up -d redis postgres seaweedfs
```

Then on **bimbotw1**, restart any worker stuck in reconnect backoff (it will not
self-heal from deep backoff):

```bash
cd /home/bimbot-w1/apps/ifcpipeline
docker compose -f docker-compose.remote-workers.yml --env-file .env.remote restart <worker>
```

`DOCKER-USER` iptables allow-rules (worker VM allowlist on 6379/5432/8333)
persist across recreates — only the published ports vanish, so re-applying the
overlay restores access.

## Firewall: restrict LAN ports to the worker VM (don't break internal traffic)

The control-plane LAN ports must be locked to the worker VM, but the rules must
be **scoped to the external interface**. With `net.bridge.bridge-nf-call-iptables=1`
(default), container-to-container traffic on the docker bridge ALSO traverses
`DOCKER-USER`, so an interface-agnostic rule like
`iptables -A DOCKER-USER -p tcp --dport 5432 -j DROP` silently drops
`api-gateway->postgres`, `n8n->postgres`, `workers->redis`, etc. Symptom:
internal connections **time out** (not "refused"); n8n crash-loops with
"Could not establish database connection".

Apply / repair with the idempotent script (scopes every rule to the NIC holding
`PIPELINE_LAN_IP`, e.g. `eth0`):

```bash
cd /home/bimbot-ubuntu/apps/ifcpipeline
sudo ./scripts/apply-host-lan-firewall.sh
```

Diagnose a suspected drop (timeout = dropped, refused = path OK):

```bash
docker exec ifcpipeline-api-gateway-1 python3 -c \
 'import socket;s=socket.socket();s.settimeout(4);s.connect(("postgres",5432));print("OK")'
```

## Worker-VM rebuilds (bimbotw1)

Remote-worker images bake `shared/` + `tasks.py`. After changing them, sync to
the worker repo and rebuild WITH the env file:

```bash
cd /home/bimbot-w1/apps/ifcpipeline
docker compose -f docker-compose.remote-workers.yml --env-file .env.remote build <worker>
docker compose -f docker-compose.remote-workers.yml --env-file .env.remote up -d <worker>
```
