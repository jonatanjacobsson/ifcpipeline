#!/usr/bin/env bash
# Primary-host LAN detection shared by the host-LAN scripts.
#
# The worker VM is reached over the LAN, so routing toward WORKER_VM_IP tells us the
# primary's LAN interface and address. That breaks when WORKER_VM_IP is stale (DHCP
# moved the worker) and the old address falls inside a Docker bridge subnet: the
# route then goes via docker0 / br-<id> and yields the bridge gateway (e.g.
# 172.17.0.1), which no remote host can reach. On 2026-10-20 that wrote
# PIPELINE_LAN_IP=172.17.0.1 into .env at boot.
#
# These helpers never return a Docker bridge interface or address; they fall back
# to the default-route interface instead.

# is_docker_bridge_dev <dev>  ->  0 for docker0, a compose network (br-<12 hex>) or a veth
is_docker_bridge_dev() {
  [[ "$1" == docker0 || "$1" =~ ^br-[0-9a-f]{12}$ || "$1" == veth* ]]
}

# is_docker_bridge_ip <ip>  ->  0 if <ip> is assigned to a Docker bridge on this host
is_docker_bridge_ip() {
  local ip="$1" dev addr
  while read -r dev addr; do
    if is_docker_bridge_dev "$dev" && [[ "${addr%/*}" == "$ip" ]]; then
      return 0
    fi
  done < <(ip -4 -o addr show 2>/dev/null | awk '{print $2, $4}')
  return 1
}

# route_field <ip> <field>  ->  `dev` or `src` of the route the kernel picks for <ip>
route_field() {
  ip route get "$1" 2>/dev/null | awk -v f="$2" '{for (i = 1; i <= NF; i++) if ($i == f) { print $(i + 1); exit }}'
}

# default_route_dev  ->  interface of the IPv4 default route
default_route_dev() {
  ip -4 route show default 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "dev") { print $(i + 1); exit }}'
}

# dev_ipv4 <dev>  ->  first global IPv4 address on <dev>
dev_ipv4() {
  ip -4 -o addr show dev "$1" scope global 2>/dev/null | awk '{split($4, a, "/"); print a[1]; exit}'
}

# lan_dev_toward <worker-ip>  ->  LAN interface used to reach the worker, never a Docker bridge
lan_dev_toward() {
  local dev
  dev="$(route_field "$1" dev)"
  if [[ -z "$dev" ]] || is_docker_bridge_dev "$dev"; then
    [[ -n "$dev" ]] && echo "warn: route to $1 goes via Docker bridge $dev (stale WORKER_VM_IP?); using the default-route interface" >&2
    dev="$(default_route_dev)"
    if [[ -z "$dev" ]] || is_docker_bridge_dev "$dev"; then
      return 1
    fi
  fi
  echo "$dev"
}

# lan_ip_toward <worker-ip>  ->  primary address the worker should use, never a Docker bridge
lan_ip_toward() {
  local dev src
  src="$(route_field "$1" src)"
  dev="$(route_field "$1" dev)"
  if [[ -n "$src" && -n "$dev" ]] && ! is_docker_bridge_dev "$dev" && ! is_docker_bridge_ip "$src"; then
    echo "$src"
    return 0
  fi
  dev="$(lan_dev_toward "$1")" || return 1
  src="$(dev_ipv4 "$dev")"
  if [[ -z "$src" ]] || is_docker_bridge_ip "$src"; then
    return 1
  fi
  echo "$src"
}
