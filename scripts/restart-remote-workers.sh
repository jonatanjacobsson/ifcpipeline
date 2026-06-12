#!/usr/bin/env bash
# Restart remote worker containers on the worker host (e.g. after primary Redis was recreated).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

REMOTE_SSH="${REMOTE_SSH:-deploy@worker-host}"
REMOTE_REPO="${REMOTE_REPO:-/home/deploy/apps/ifcpipeline}"
SSH_OPTS=(-o RemoteCommand=none -o RequestTTY=no)
COMPOSE_FILE="docker-compose.remote-workers.yml"

ssh "${SSH_OPTS[@]}" "$REMOTE_SSH" "cd '${REMOTE_REPO}' && \
  COMPOSE_PROFILES=remote docker compose -f ${COMPOSE_FILE} --env-file .env.remote restart && \
  sleep 3 && \
  COMPOSE_PROFILES=remote docker compose -f ${COMPOSE_FILE} --env-file .env.remote ps && \
  COMPOSE_PROFILES=remote docker compose -f ${COMPOSE_FILE} --env-file .env.remote logs --tail=8 \
    ifctester-worker ifcpatch-worker ifcclash-worker ifcdiff-worker ifccoord-worker topologicpy-worker"
