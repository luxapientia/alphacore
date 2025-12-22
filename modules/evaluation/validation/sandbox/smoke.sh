#!/usr/bin/env bash
# Firecracker Secure Smoke Test (Strict Mode)
# - Aborts on any host-side error.
# - Captures Guest success via TEST_PASSED marker.
# - Verifies networking + firewall behavior.

set -euo pipefail

# --- Configuration ---
ID="fctest-$(date +%s)"
CHROOT_BASE="/srv/jailer"
FC_BIN="/opt/firecracker/firecracker"
JAILER_BIN="/opt/firecracker/jailer"
KERNEL="/opt/firecracker/acore-sandbox-kernel-v1.bin"
ROOTFS="/opt/firecracker/acore-sandbox-rootfs-v1.ext4"

# Networking (must match setup.sh)
ACORE_TAP="${ACORE_TAP:-acore-tap0}"
# Note: Variables below are for the GUEST inside the VM
GUEST_IP="172.16.0.2"
GUEST_CIDR="172.16.0.2/30"
GUEST_GW="172.16.0.1"

# Resources
MEM_MB="512"
VCPUS="1"

# Logs
LOG_FILE="./firecracker-${ID}.log"

# Cleanup function to run on exit (success or fail)
cleanup() {
  # Kill firecracker if still running
  if [ -n "${FC_PID:-}" ] && kill -0 "$FC_PID" 2>/dev/null; then
    kill "$FC_PID" 2>/dev/null || true
  fi
  # Remove chroot directories
  rm -rf "${CHROOT_BASE}/firecracker/${ID}"
}
trap cleanup EXIT

# --- Pre-flight Checks ---
if [ ! -e /dev/kvm ]; then echo "Error: /dev/kvm missing." >&2; exit 1; fi
if [ ! -x "${FC_BIN}" ]; then echo "Error: Firecracker binary missing." >&2; exit 1; fi
if [ ! -x "${JAILER_BIN}" ]; then echo "Error: Jailer binary missing." >&2; exit 1; fi
if [ ! -f "${KERNEL}" ]; then echo "Error: Kernel image missing." >&2; exit 1; fi
if [ ! -f "${ROOTFS}" ]; then echo "Error: Rootfs image missing." >&2; exit 1; fi
if ! ip link show "${ACORE_TAP}" >/dev/null 2>&1; then
  echo "Error: TAP interface ${ACORE_TAP} not found. Run setup.sh first." >&2
  exit 1
fi

# --- Setup Jailer Chroot ---
CHROOT="${CHROOT_BASE}/firecracker/${ID}/root"
API_SOCK_NAME="fc.sock"
API_SOCK_PATH="${CHROOT}/run/${API_SOCK_NAME}"
ROOTFS_COPY="${CHROOT}/rootfs.ext4"

echo "==> [Host] Preparing chroot: ${CHROOT}"
mkdir -p "${CHROOT_BASE}/firecracker/${ID}"
mkdir -p "${CHROOT}/dev" "${CHROOT}/run"
mkdir -p "${CHROOT}/tmp"

cp "${KERNEL}" "${CHROOT}/vmlinux.bin"
cp "${ROOTFS}" "${ROOTFS_COPY}"
rm -f "${CHROOT}/dev/kvm"

# --- Payload Injection ---
echo "==> [Host] Injecting init script..."
MNT_DIR=$(mktemp -d)
mount -o loop "${ROOTFS_COPY}" "${MNT_DIR}"

cat > "${MNT_DIR}/init-smoke.sh" <<'EOF'
#!/bin/sh
# --- GUEST SCRIPT START ---
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t tmpfs tmpfs /tmp

exec > /dev/ttyS0 2>&1
echo "[Guest] System Booted. User ID: $(id -u)"

# --- CONFIG ---
TARGET_ALLOWED="https://www.googleapis.com/discovery/v1/apis"
TARGET_BLOCKED_DOMAIN="http://example.com"
TARGET_BLOCKED_META="http://169.254.169.254/latest/meta-data"
PROXY_URL="http://172.16.0.1:8888"   # host proxy on TAP gw
DNS_HOST="172.16.0.1"                # host-side dnsmasq on TAP gw

curl_code() {
    # Print a 3-digit HTTP code or "000" on any failure.
    url="$1"
    code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || true)"
    code="$(printf '%s' "$code" | tr -d '\r\n')"
    code="$(printf '%s' "$code" | awk '{ if (length($0) >= 3) print substr($0, length($0)-2); else print "" }')"
    case "$code" in
        [0-9][0-9][0-9]) ;;
        *) code="000" ;;
    esac
    echo "$code"
}

# Ensure terraform bundle path is included.
export PATH="/opt/acore-sandbox-bundle/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

echo "[Guest] terraform --version..."
if command -v terraform >/dev/null 2>&1; then
    terraform --version
else
    echo "[Guest] ERROR: terraform not found in PATH."
    echo "[Guest] TEST_FAILED"
    exit 1
fi

echo "[Guest] python3 --version..."
if command -v python3 >/dev/null 2>&1; then
    python3 --version
else
    echo "[Guest] ERROR: python3 not found in PATH."
    echo "[Guest] TEST_FAILED"
    exit 1
fi

# --- Network bring-up ---
ip link set lo up
ip link set eth0 up
DHCP_TIMEOUT="${DHCP_TIMEOUT:-10}"
if [ ! -x /sbin/udhcpc ]; then
    echo "[Guest] ERROR: /sbin/udhcpc missing; rebuild rootfs via setup.sh." >&2
    echo "[Guest] TEST_FAILED"
    exit 1
fi
cat > /run/udhcpc.sh <<'EOF'
#!/bin/sh
set -eu
iface="${interface:-eth0}"
case "${1:-}" in
  deconfig)
    ip addr flush dev "${iface}" 2>/dev/null || true
    ;;
  bound|renew)
    ip addr flush dev "${iface}" 2>/dev/null || true
    ip addr add "${ip}/${mask}" dev "${iface}"
    gw="$(printf '%s\n' "${router:-}" | awk '{print $1}')"
    if [ -n "${gw}" ]; then
      ip route replace default via "${gw}" dev "${iface}"
    fi
    ;;
esac
exit 0
EOF
chmod +x /run/udhcpc.sh

/sbin/udhcpc -i eth0 -q -t 5 -T 2 -n -s /run/udhcpc.sh
printf 'nameserver %s\n' "${DNS_HOST}" > /etc/resolv.conf

echo ""
echo "[Guest] === CHECK 1: Direct egress WITHOUT proxy should FAIL ==="
unset http_proxy https_proxy

# Try to reach a real HTTPS endpoint directly (no proxy)
HTTP_CODE_DIRECT="$(curl_code "$TARGET_ALLOWED")"

if [ "$HTTP_CODE_DIRECT" = "200" ]; then
    echo "[Guest] ERROR: Direct HTTPS to $TARGET_ALLOWED succeeded without proxy (code $HTTP_CODE_DIRECT)."
    echo "[Guest] TEST_FAILED"
    exit 1
else
    echo "[Guest] OK: Direct egress without proxy failed as expected (code $HTTP_CODE_DIRECT)."
fi

# --- DNS allow/deny via dnsmasq whitelist ---
echo ""
echo "[Guest] === CHECK 2a: DNS whitelist via dnsmasq (googleapis.com should resolve) ==="
DNS_GOOGLE=$(getent ahostsv4 googleapis.com | awk 'NR==1{print $1}')
if [ -z "$DNS_GOOGLE" ] || echo "$DNS_GOOGLE" | grep -Eq '^0\.0\.0\.0'; then
    echo "[Guest] ERROR: googleapis.com did not resolve through dnsmasq (got \"$DNS_GOOGLE\")."
    echo "[Guest] TEST_FAILED"
    exit 1
else
    echo "[Guest] OK: googleapis.com resolved to $DNS_GOOGLE via dnsmasq."
fi

echo ""
echo "[Guest] === CHECK 2b: DNS blacklist via dnsmasq (example.com should be sinkholed) ==="
DNS_EXAMPLE=$(getent ahostsv4 example.com | awk 'NR==1{print $1}')
if [ -z "$DNS_EXAMPLE" ] || echo "$DNS_EXAMPLE" | grep -Eq '^0\.0\.0\.0'; then
    MSG=${DNS_EXAMPLE:-"NXDOMAIN/empty"}
    echo "[Guest] OK: example.com sinkholed by dnsmasq ($MSG)."
else
    echo "[Guest] ERROR: example.com resolved to $DNS_EXAMPLE (expected sinkhole or NXDOMAIN)."
    echo "[Guest] TEST_FAILED"
    exit 1
fi

# --- Configure Proxy for subsequent tests ---
export http_proxy="$PROXY_URL"
export https_proxy="$PROXY_URL"
echo "[Guest] Proxy configured: $PROXY_URL"

echo ""
echo "[Guest] === CHECK 3: Allowed Google traffic via proxy should PASS ==="
HTTP_CODE_ALLOWED="$(curl_code "$TARGET_ALLOWED")"

if [ "$HTTP_CODE_ALLOWED" = "200" ]; then
    echo "[Guest] OK: Reached googleapis via proxy (HTTP 200)."
else
    echo "[Guest] ERROR: Failed to reach googleapis via proxy. Code: $HTTP_CODE_ALLOWED"
    echo "[Guest] TEST_FAILED"
    exit 1
fi

echo ""
echo "[Guest] === CHECK 4: Generic internet via proxy should FAIL (example.com) ==="
CODE_BLOCKED="$(curl_code "$TARGET_BLOCKED_DOMAIN")"

if [ "$CODE_BLOCKED" = "200" ]; then
    echo "[Guest] ERROR: example.com reachable via proxy (code 200). Whitelist is broken."
    echo "[Guest] TEST_FAILED"
    exit 1
else
    echo "[Guest] OK: example.com blocked by proxy (code $CODE_BLOCKED)."
fi

echo ""
echo "[Guest] === CHECK 5: Metadata must be blocked (no proxy) ==="
unset http_proxy https_proxy

curl -sS --max-time 3 "$TARGET_BLOCKED_META" >/dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "[Guest] OK: Metadata endpoint blocked (connection failed)."
else
    echo "[Guest] CRITICAL: Metadata endpoint is reachable!"
    echo "[Guest] TEST_FAILED"
    exit 1
fi

echo "[Guest] TEST_PASSED"
exit 0
# --- GUEST SCRIPT END ---
EOF


chmod +x "${MNT_DIR}/init-smoke.sh"
umount "${MNT_DIR}"
rmdir "${MNT_DIR}"

# --- Execution ---
echo "==> [Host] Starting Firecracker (Logs -> ${LOG_FILE})..."

"${JAILER_BIN}" \
  --id "${ID}" \
  --uid "$(id -u)" \
  --gid "$(id -g)" \
  --chroot-base-dir "${CHROOT_BASE}" \
  --exec-file "${FC_BIN}" \
  -- --api-sock "/run/${API_SOCK_NAME}" >> "${LOG_FILE}" 2>&1 &

FC_PID=$!

# Ensure log file exists before tailing
touch "${LOG_FILE}"
tail -f "${LOG_FILE}" &
TAIL_PID=$!

# Wait for API Socket (Timeout after 5s)
TRIES=0
while [ ! -S "${API_SOCK_PATH}" ]; do
  sleep 0.1
  TRIES=$((TRIES+1))
  if [ $TRIES -gt 50 ]; then
    echo "Error: Socket never appeared. Check ${LOG_FILE}"
    exit 1
  fi
done

# API Call Helper
curl_put() {
  local endpoint=$1
  local data=$2

  if ! curl -fs --unix-socket "${API_SOCK_PATH}" -X PUT "http://localhost/${endpoint}" \
    -H 'Content-Type: application/json' \
    -d "$data" > /dev/null; then
      echo "Error: API call to ${endpoint} failed." >&2
      # Best-effort dump of error body
      curl -s --unix-socket "${API_SOCK_PATH}" -X PUT "http://localhost/${endpoint}" \
        -H 'Content-Type: application/json' \
        -d "$data" || true
      exit 1
  fi
}

echo "==> [Host] Configuring VM..."

# Generate a unique MAC to allow running multiple VMs in parallel on the same bridge.
if command -v hexdump >/dev/null 2>&1; then
  HEX="$(hexdump -n4 -v -e '/1 \"%02X\"' /dev/urandom)"
elif command -v od >/dev/null 2>&1; then
  HEX="$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
elif command -v sha256sum >/dev/null 2>&1; then
  HEX="$(date +%s%N | sha256sum | awk '{print toupper(substr($1,1,8))}')"
else
  HEX="$(date +%s%N | awk '{print toupper(substr($1,1,8))}')"
fi
GUEST_MAC="02:FC:${HEX:0:2}:${HEX:2:2}:${HEX:4:2}:${HEX:6:2}"

# Machine config
curl_put "machine-config" \
  "{\"vcpu_count\": ${VCPUS}, \"mem_size_mib\": ${MEM_MB}}"

# Boot source
curl_put "boot-source" \
  "{\"kernel_image_path\": \"/vmlinux.bin\", \"boot_args\": \"console=ttyS0 reboot=k panic=1 pci=off init=/init-smoke.sh root=/dev/vda rw\"}"

# Rootfs
curl_put "drives/rootfs" \
  "{\"drive_id\": \"rootfs\", \"path_on_host\": \"/rootfs.ext4\", \"is_root_device\": true, \"is_read_only\": false}"

# Network interface (attach TAP)
curl_put "network-interfaces/eth0" \
  "{\"iface_id\":\"eth0\",\"guest_mac\":\"${GUEST_MAC}\",\"host_dev_name\":\"${ACORE_TAP}\"}"

echo "==> [Host] Booting..."
curl_put "actions" "{\"action_type\": \"InstanceStart\"}"

echo "==> [Host] Watching process ${FC_PID}..."
wait $FC_PID || true
kill ${TAIL_PID} 2>/dev/null || true

echo "==> [Host] VM Exited. Verifying results..."

echo ""
echo "=== LOG OUTPUT ==="
grep "\[Guest\]" "${LOG_FILE}" || echo "(No Guest output found)"
echo "=== END LOG ==="
echo ""

if grep -q "\[Guest\] TEST_PASSED" "${LOG_FILE}"; then
  echo "✅ SUCCESS: Terraform + network smoke passed."
  rm -f "${LOG_FILE}"
  exit 0
else
  echo "❌ FAILURE: Success token not found in logs."
  echo "See full log at: ${LOG_FILE}"
  exit 1
fi
