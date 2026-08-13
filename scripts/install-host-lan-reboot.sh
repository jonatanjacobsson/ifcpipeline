#!/usr/bin/env bash
# Install reboot-safe host-LAN systemd units on the primary ifcpipeline host.
#
#   1. ifcpipeline-host-lan.service         (user) — sync IPs, publish redis/postgres/seaweedfs
#   2. ifcpipeline-host-lan-firewall.service (root) — DOCKER-USER allowlist for worker VM
#
# Run once on the primary host:
#   cd /home/bimbot-ubuntu/apps/ifcpipeline
#   sudo ./scripts/install-host-lan-reboot.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ $EUID -ne 0 ]]; then
  echo "error: run as root: sudo $0" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "error: missing .env in $ROOT" >&2
  exit 1
fi

PIPELINE_USER="${SUDO_USER:-bimbot-ubuntu}"
if ! id "$PIPELINE_USER" &>/dev/null; then
  echo "error: user ${PIPELINE_USER} not found (set SUDO_USER or create the account)" >&2
  exit 1
fi

LAN_UNIT="$ROOT/scripts/ifcpipeline-host-lan.service"
FW_UNIT="$ROOT/scripts/ifcpipeline-host-lan-firewall.service"

for f in "$LAN_UNIT" "$FW_UNIT" \
  "$ROOT/scripts/apply-host-lan-access.sh" \
  "$ROOT/scripts/apply-host-lan-firewall.sh"; do
  if [[ ! -f "$f" ]]; then
    echo "error: missing $f" >&2
    exit 1
  fi
done

echo "==> Installing systemd units"
install -m 644 "$LAN_UNIT" /etc/systemd/system/ifcpipeline-host-lan.service
install -m 644 "$FW_UNIT" /etc/systemd/system/ifcpipeline-host-lan-firewall.service

echo "==> Ensuring iptables-persistent (rules survive reboot)"
if ! command -v netfilter-persistent >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
fi

echo "==> Checking WORKER_HOSTNAME resolves (needed for IP sync on reboot)"
set -a
# shellcheck disable=SC1091
source .env
set +a
WORKER_HOST="${WORKER_HOSTNAME:-bimbotw1}"
if ! getent hosts "$WORKER_HOST" >/dev/null 2>&1; then
  echo "warn: ${WORKER_HOST} does not resolve via getent." >&2
  echo "      Add to .env, e.g. WORKER_HOSTNAME=bimbotw1-Virtual-Machine" >&2
fi

echo "==> Enabling units"
systemctl daemon-reload
systemctl enable ifcpipeline-host-lan.service ifcpipeline-host-lan-firewall.service

echo "==> Applying host-LAN bindings now (as ${PIPELINE_USER})"
sudo -u "$PIPELINE_USER" env SKIP_FIREWALL=1 HOME="/home/${PIPELINE_USER}" \
  bash "$ROOT/scripts/apply-host-lan-access.sh"

echo ""
echo "==> Applying firewall now (root)"
bash "$ROOT/scripts/apply-host-lan-firewall.sh"

echo ""
echo "==> Recreating Dozzle (picks up WORKER_VM_IP for remote agent)"
if sudo -u "$PIPELINE_USER" docker compose \
  -f docker-compose.control-plane.yml ps -q dozzle >/dev/null 2>&1; then
  sudo -u "$PIPELINE_USER" docker compose \
    -f docker-compose.control-plane.yml up -d --force-recreate dozzle
else
  echo "note: dozzle service not running; skip recreate"
fi

echo ""
echo "✓ Installed and applied."
echo ""
echo "On reboot, systemd runs:"
echo "  1. ifcpipeline-host-lan.service          — sync PIPELINE_LAN_IP / WORKER_VM_IP, publish ports"
echo "  2. ifcpipeline-host-lan-firewall.service — restrict 6379/5432/8333 to WORKER_VM_IP on eth0"
echo ""
echo "After a DHCP change, refresh remote workers:"
echo "  ./scripts/deploy-remote-workers-from-primary.sh"
echo ""
echo "Status:"
systemctl is-enabled ifcpipeline-host-lan.service ifcpipeline-host-lan-firewall.service
echo ""
echo "Manual test:"
echo "  sudo systemctl start ifcpipeline-host-lan.service ifcpipeline-host-lan-firewall.service"
