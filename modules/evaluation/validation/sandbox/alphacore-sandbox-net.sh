#!/usr/bin/env bash
set -euo pipefail

log() { echo "[alphacore-sandbox-net] $*"; }

ensure_rule() {
  local table="$1"; shift
  if ! iptables -t "${table}" -C "$@" 2>/dev/null; then
    iptables -t "${table}" -A "$@"
  fi
}

ensure_rule_first() {
  local table="$1"; shift
  if ! iptables -t "${table}" -C "$@" 2>/dev/null; then
    iptables -t "${table}" -I "$@"
  fi
}

ensure_chain_reset() {
  local chain="$1"
  if iptables -nL "${chain}" >/dev/null 2>&1; then
    iptables -F "${chain}"
  else
    iptables -N "${chain}"
  fi
}

ENV_FILE="/etc/default/alphacore-sandbox-net"
if [ -f "${ENV_FILE}" ]; then
  # shellcheck disable=SC1091
  . "${ENV_FILE}"
fi

BR_NAME="${ACORE_BR_NAME:-acore-br0}"
BR_CIDR="${ACORE_BR_CIDR:-172.16.0.1/24}"
SUBNET_CIDR="${ACORE_SUBNET_CIDR:-172.16.0.0/24}"
TAP_PREFIX="${ACORE_TAP_PREFIX:-acore-tap}"
TAP_POOL_SIZE="${ACORE_TAP_POOL_SIZE:-32}"
DHCP_START="${ACORE_DHCP_START:-172.16.0.100}"
DHCP_END="${ACORE_DHCP_END:-172.16.0.199}"
DHCP_LEASE="${ACORE_DHCP_LEASE:-12h}"
PROXY_PORT="${ACORE_PROXY_PORT:-8888}"
TAP_OWNER_UID="${TAP_OWNER_UID:-${SUDO_UID:-terraformrunner}}"
TAP_OWNER_GID="${TAP_OWNER_GID:-${SUDO_GID:-terraformrunner}}"

EGRESS_IFACE="${ACORE_EGRESS_IFACE:-}"
if [ -z "${EGRESS_IFACE}" ]; then
  EGRESS_IFACE="$(ip route get 8.8.8.8 2>/dev/null \
    | awk '/dev/ {for (i=1; i<=NF; i++) if ($i == "dev") {print $(i+1); exit}}')"
fi
[ -z "${EGRESS_IFACE}" ] && EGRESS_IFACE="ens4"

log "egress_iface=${EGRESS_IFACE} bridge=${BR_NAME} tap_prefix=${TAP_PREFIX} tap_pool_size=${TAP_POOL_SIZE}"
log "tap_owner=${TAP_OWNER_UID}:${TAP_OWNER_GID} proxy_port=${PROXY_PORT}"

log "enabling net.ipv4.ip_forward=1"
sysctl -w net.ipv4.ip_forward=1 >/dev/null

log "ensuring bridge ${BR_NAME} (${BR_CIDR})"
if ! ip link show "${BR_NAME}" >/dev/null 2>&1; then
  ip link add name "${BR_NAME}" type bridge
fi
ip addr flush dev "${BR_NAME}" || true
ip addr add "${BR_CIDR}" dev "${BR_NAME}" 2>/dev/null || true
ip link set "${BR_NAME}" up

sysctl -w "net.ipv4.conf.${BR_NAME}.arp_ignore=1" >/dev/null 2>&1 || true
sysctl -w "net.ipv4.conf.${BR_NAME}.arp_announce=2" >/dev/null 2>&1 || true
sysctl -w "net.ipv6.conf.${BR_NAME}.disable_ipv6=1" >/dev/null 2>&1 || true

log "detaching non-sandbox interfaces from ${BR_NAME}"
while read -r iface; do
  [ -z "${iface}" ] && continue
  if [[ "${iface}" != ${TAP_PREFIX}* ]]; then
    ip link set dev "${iface}" nomaster 2>/dev/null || true
  fi
done < <(ip -o link show master "${BR_NAME}" 2>/dev/null | awk -F': ' '{print $2}' | awk '{print $1}')

log "ensuring TAP pool (${TAP_POOL_SIZE} devices)"
for i in $(seq 0 $((TAP_POOL_SIZE - 1))); do
  TAP_NAME="${TAP_PREFIX}${i}"
  if ! ip link show "${TAP_NAME}" >/dev/null 2>&1; then
    ip tuntap add dev "${TAP_NAME}" mode tap user "${TAP_OWNER_UID}" group "${TAP_OWNER_GID}"
  fi
  ip addr flush dev "${TAP_NAME}" 2>/dev/null || true
  ip link set "${TAP_NAME}" master "${BR_NAME}" 2>/dev/null || true
  ip link set "${TAP_NAME}" up 2>/dev/null || true
  bridge link set dev "${TAP_NAME}" isolated on 2>/dev/null || true
  sysctl -w "net.ipv6.conf.${TAP_NAME}.disable_ipv6=1" >/dev/null 2>&1 || true
done

log "writing dnsmasq config"
cat > /etc/dnsmasq.d/acore-sandbox.conf <<EOF
interface=${BR_NAME}
bind-interfaces
bogus-priv
no-resolv
no-poll
dhcp-authoritative
dhcp-range=${DHCP_START},${DHCP_END},${DHCP_LEASE}
dhcp-option=option:router,172.16.0.1
dhcp-option=option:dns-server,172.16.0.1
server=/googleapis.com/8.8.8.8
server=/gcr.io/8.8.8.8
server=/pkg.dev/8.8.8.8
address=/#/
EOF
if ! dnsmasq --test -C /etc/dnsmasq.d/acore-sandbox.conf >/dev/null 2>&1; then
  dnsmasq --test -C /etc/dnsmasq.d/acore-sandbox.conf || true
  log "ERROR: dnsmasq config test failed"
  exit 1
fi
systemctl restart dnsmasq

log "configuring firewall (iptables)"
iptables -D FORWARD -i "${BR_NAME}" -j ACORE_BR 2>/dev/null || true
iptables -D INPUT -i "${BR_NAME}" -j ACORE_INPUT 2>/dev/null || true
ensure_chain_reset ACORE_BR
ensure_chain_reset ACORE_INPUT

iptables -A ACORE_BR -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A ACORE_BR -d 172.16.0.1 -p udp --dport 67 -j ACCEPT
iptables -A ACORE_BR -d 172.16.0.1 -p udp --dport 53 -j ACCEPT
iptables -A ACORE_BR -d 172.16.0.1 -p tcp --dport 53 -j ACCEPT
iptables -A ACORE_BR -d 172.16.0.1 -p tcp --dport "${PROXY_PORT}" -j ACCEPT
iptables -A ACORE_BR -p tcp -d 169.254.169.254 -j REJECT --reject-with tcp-reset
iptables -A ACORE_BR -j DROP
ensure_rule_first filter FORWARD -i "${BR_NAME}" -j ACORE_BR

iptables -A ACORE_INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A ACORE_INPUT -p udp --dport 67 -j ACCEPT
iptables -A ACORE_INPUT -s "${SUBNET_CIDR}" -d 172.16.0.1 -p udp --dport 53 -j ACCEPT
iptables -A ACORE_INPUT -s "${SUBNET_CIDR}" -d 172.16.0.1 -p tcp --dport 53 -j ACCEPT
iptables -A ACORE_INPUT -s "${SUBNET_CIDR}" -d 172.16.0.1 -p tcp --dport "${PROXY_PORT}" -j ACCEPT
iptables -A ACORE_INPUT -d 169.254.169.254 -j DROP
iptables -A ACORE_INPUT -j DROP
ensure_rule_first filter INPUT -i "${BR_NAME}" -j ACORE_INPUT

ensure_rule nat POSTROUTING -s "${SUBNET_CIDR}" -o "${EGRESS_IFACE}" -j MASQUERADE

log "writing tinyproxy config"
TINYCONF="/etc/tinyproxy/tinyproxy.conf"
TINYFILTER="/etc/tinyproxy/filter"

cat > "${TINYCONF}" <<EOF
User nobody
Group nogroup
Port ${PROXY_PORT}
Listen 172.16.0.1
Bind 0.0.0.0

Timeout 600
LogLevel Info
MaxClients 100
StartServers 5
MinSpareServers 5
MaxSpareServers 20

PidFile "/run/tinyproxy/tinyproxy.pid"
LogFile "/var/log/tinyproxy/tinyproxy.log"

Allow 172.16.0.0/24

Filter "${TINYFILTER}"
FilterURLs On
FilterDefaultDeny Yes
FilterExtended On
EOF

cat > "${TINYFILTER}" <<'EOF'
^([^.]+\.)*googleapis\.com(:[0-9]+)?$
EOF

mkdir -p /run/tinyproxy
chown nobody:nogroup /run/tinyproxy
systemctl enable tinyproxy >/dev/null 2>&1 || true
systemctl restart tinyproxy || log "WARNING: tinyproxy failed to restart (check /var/log/tinyproxy/tinyproxy.log)"

log "done"

