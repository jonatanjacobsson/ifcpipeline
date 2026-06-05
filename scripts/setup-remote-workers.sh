#!/usr/bin/env bash
# One-shot remote worker rollout: primary host → worker host.
# Run from your terminal on the primary host (SSH may be blocked in some agent environments):
#
#   cd /path/to/ifcpipeline && ./scripts/setup-remote-workers.sh
#
# Requires .env on primary. First time: configure REMOTE_SSH (e.g. deploy@worker-host).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PIPELINE_HOST="${PIPELINE_HOST:-primary-host}"
REMOTE_SSH="${REMOTE_SSH:-deploy@worker-host}"
REMOTE_REPO="${REMOTE_REPO:-/home/deploy/apps/ifcpipeline}"
WORKER_HOSTNAME="${WORKER_HOSTNAME:-worker-host}"

detect_lan_ip() {
  hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^172\.17\.' | head -1 || hostname -I | awk '{print $1}'
}

detect_worker_ip() {
  getent hosts "$WORKER_HOSTNAME" 2>/dev/null | awk '{print $1}' | head -1
}

ensure_env_vars() {
  local lan_ip worker_ip
  lan_ip="${PIPELINE_LAN_IP:-$(detect_lan_ip)}"
  worker_ip="${WORKER_VM_IP:-$(detect_worker_ip)}"

  if [[ -z "$lan_ip" || -z "$worker_ip" ]]; then
    echo "error: could not detect PIPELINE_LAN_IP or WORKER_VM_IP; set them in .env" >&2
    exit 1
  fi

  if [[ ! -f .env ]]; then
    echo "error: missing .env in $ROOT" >&2
    exit 1
  fi

  append_if_missing() {
    local key="$1" val="$2"
    if ! grep -q "^${key}=" .env 2>/dev/null; then
      echo "${key}=${val}" >>.env
      echo "  appended ${key}=${val} to .env"
    fi
  }

  append_if_missing PIPELINE_LAN_IP "$lan_ip"
  append_if_missing WORKER_VM_IP "$worker_ip"
  append_if_missing PIPELINE_HOST "$PIPELINE_HOST"

  export PIPELINE_LAN_IP="$lan_ip" WORKER_VM_IP="$worker_ip" PIPELINE_HOST
}

apply_ufw() {
  if ! command -v ufw >/dev/null 2>&1; then
    echo "==> ufw not installed; skip firewall (ensure worker can reach 6379, 5432, 9000)"
    return 0
  fi
  if ! sudo -n true 2>/dev/null; then
    echo "==> sudo required for ufw; run manually:"
    echo "sudo ufw allow from ${WORKER_VM_IP} to any port 6379 proto tcp"
    echo "sudo ufw allow from ${WORKER_VM_IP} to any port 5432 proto tcp"
    echo "sudo ufw allow from ${WORKER_VM_IP} to any port 9000 proto tcp"
    return 0
  fi
  for port in 6379 5432 9000; do
    if ! sudo ufw status 2>/dev/null | grep -q "${WORKER_VM_IP}.*${port}/tcp"; then
      sudo ufw allow from "${WORKER_VM_IP}" to any port "${port}" proto tcp
    fi
  done
  echo "==> ufw rules for worker ${WORKER_VM_IP} OK"
}

check_ssh() {
  local ssh_opts=(-o RemoteCommand=none -o RequestTTY=no -o BatchMode=yes -o ConnectTimeout=8)
  if ssh "${ssh_opts[@]}" "$REMOTE_SSH" 'echo ok' >/dev/null 2>&1; then
    echo "==> SSH to ${REMOTE_SSH} OK"
    return 0
  fi
  echo ""
  echo "SSH to ${REMOTE_SSH} failed (need key-based login)."
  echo "If you see 'Cannot execute command-line and remote command', fix ~/.ssh/config"
  echo "(add Host worker-host with RemoteCommand none above Host *) or use -o RemoteCommand=none."
  echo ""
  echo "On worker host: openssh-server + docker must be running."
  echo "On THIS host, run once:"
  echo "  ssh-copy-id -o RemoteCommand=none -o RequestTTY=no ${REMOTE_SSH}"
  echo "  ssh -o RemoteCommand=none ${REMOTE_SSH} 'docker compose version'"
  echo ""
  echo "Then re-run: $ROOT/scripts/setup-remote-workers.sh"
  exit 1
}

main() {
  echo "=== ifcpipeline remote workers setup ==="
  echo "Primary: ${PIPELINE_HOST}  Worker: ${REMOTE_SSH}"
  echo ""

  ensure_env_vars
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a

  echo "==> Step 1/4: LAN publish redis, postgres, minio on ${PIPELINE_LAN_IP}"
  docker compose \
    -f docker-compose.control-plane.yml \
    -f docker-compose.host-lan.yml \
    up -d redis postgres minio

  echo ""
  echo "==> Step 2/4: Firewall (worker ${WORKER_VM_IP} only)"
  apply_ufw

  echo ""
  echo "==> Step 3/4: Preflight from primary to LAN services"
  if command -v redis-cli >/dev/null 2>&1; then
    redis-cli -h "${PIPELINE_LAN_IP}" ping
  fi
  curl -sf "http://${PIPELINE_LAN_IP}:9000/minio/health/live" >/dev/null && echo "MinIO health OK"

  echo ""
  echo "==> Step 4/4: Deploy to worker over SSH"
  check_ssh
  export PIPELINE_HOST REMOTE_SSH REMOTE_REPO
  "$ROOT/scripts/deploy-remote-workers-from-primary.sh"

  echo ""
  echo "=== Done ==="
  echo "Workers should be running on ${REMOTE_SSH}."
  echo "rq-dashboard: http://127.0.0.1:9181  (on primary host)"
}

main "$@"
