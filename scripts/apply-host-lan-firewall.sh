#!/usr/bin/env bash
# Restrict the LAN-published control-plane ports (Redis 6379, Postgres 5432,
# MinIO 9000) to the worker VM only — WITHOUT breaking internal
# container-to-container traffic.
#
# Why this script exists:
#   With net.bridge.bridge-nf-call-iptables=1 (the default), container-to-
#   container traffic on the docker bridge ALSO traverses the iptables FORWARD
#   chain, and therefore DOCKER-USER. Interface-agnostic rules like
#       iptables -A DOCKER-USER -p tcp --dport 5432 -j DROP
#   silently drop api-gateway->postgres, n8n->postgres, workers->redis, etc.
#   We scope every rule to the EXTERNAL interface (the NIC holding
#   PIPELINE_LAN_IP) so only off-host clients are filtered; bridge traffic
#   (entering via br-*) is never matched and flows normally.
#
# Run on the primary host as root:
#   sudo ./scripts/apply-host-lan-firewall.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ $EUID -ne 0 ]]; then
  echo "error: must run as root (sudo $0)" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "error: missing .env in $ROOT" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

: "${PIPELINE_LAN_IP:?Set PIPELINE_LAN_IP in .env}"
: "${WORKER_VM_IP:?Set WORKER_VM_IP in .env}"

PORTS=(6379 5432 9000)

# External interface = the NIC that holds PIPELINE_LAN_IP.
EXT_IF="$(ip -o -4 addr show | awk -v ip="${PIPELINE_LAN_IP}/" '$4 ~ ip {print $2; exit}')"
if [[ -z "$EXT_IF" ]]; then
  echo "error: could not find interface holding ${PIPELINE_LAN_IP}" >&2
  exit 1
fi

echo "==> External interface: ${EXT_IF}"
echo "==> Allow worker:       ${WORKER_VM_IP}"
echo "==> Ports:              ${PORTS[*]}"
echo ""

# 1) Remove any prior rules for these ports (both the buggy interface-agnostic
#    form and this script's eth0-scoped form). Idempotent: ignore failures.
for P in "${PORTS[@]}"; do
  # legacy interface-agnostic rules
  iptables -D DOCKER-USER -s "${WORKER_VM_IP}/32" -p tcp --dport "$P" -j RETURN 2>/dev/null || true
  iptables -D DOCKER-USER -p tcp --dport "$P" -j DROP 2>/dev/null || true
  # this script's scoped rules
  iptables -D DOCKER-USER -i "$EXT_IF" -s "${WORKER_VM_IP}/32" -p tcp --dport "$P" -j RETURN 2>/dev/null || true
  iptables -D DOCKER-USER -i "$EXT_IF" -p tcp --dport "$P" -j DROP 2>/dev/null || true
done

# 2) Insert scoped rules. Insert DROP first, then RETURN, so RETURN ends up
#    ABOVE DROP (first match wins in DOCKER-USER).
for P in "${PORTS[@]}"; do
  iptables -I DOCKER-USER -i "$EXT_IF" -p tcp --dport "$P" -j DROP
  iptables -I DOCKER-USER -i "$EXT_IF" -s "${WORKER_VM_IP}/32" -p tcp --dport "$P" -j RETURN
done

echo "==> DOCKER-USER chain now:"
iptables -S DOCKER-USER

# 3) Persist if netfilter-persistent is available.
if command -v netfilter-persistent >/dev/null 2>&1; then
  echo ""
  echo "==> Persisting rules (netfilter-persistent save)"
  netfilter-persistent save
else
  echo ""
  echo "note: netfilter-persistent not found; rules will NOT survive reboot."
  echo "      Install with: apt-get install -y iptables-persistent"
fi

echo ""
echo "✓ Done. Internal container traffic is unaffected; only ${EXT_IF} clients"
echo "  other than ${WORKER_VM_IP} are blocked on ${PORTS[*]}."
