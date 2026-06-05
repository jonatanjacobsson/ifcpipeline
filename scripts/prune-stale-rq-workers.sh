#!/usr/bin/env bash
# Remove dead entries from Redis rq:workers (fixes rq-dashboard "reading workers" 500).
set -euo pipefail

REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"

docker compose -f "$(dirname "$0")/../docker-compose.yml" exec -T redis \
  redis-cli -u redis://127.0.0.1:6379/0 --scan --pattern 'rq:workers' >/dev/null 2>&1 || true

python3 <<'PY'
import os
from redis import Redis

url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
r = Redis.from_url(url)
removed = 0
for raw in list(r.smembers("rq:workers")):
    name = raw.decode() if isinstance(raw, bytes) else raw
    key = name if name.startswith("rq:worker:") else f"rq:worker:{name}"
    if not r.exists(key):
        r.srem("rq:workers", raw)
        print(f"removed stale: {name}")
        removed += 1
print(f"done, removed {removed} stale key(s)")
PY
