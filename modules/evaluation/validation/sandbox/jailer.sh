#!/usr/bin/env bash
# Firecracker + jailer smoke test. Run as root (or with sudo) on a host with KVM.
# Uses the kernel/rootfs built by setup.sh and boots a minimal VM via jailer.

set -euo pipefail

ID="${ID:-fctest}"
CHROOT_BASE="${CHROOT_BASE:-/srv/jailer}"
FC_BIN="${FC_BIN:-/opt/firecracker/firecracker}"
JAILER_BIN="${JAILER_BIN:-/opt/firecracker/jailer}"
KERNEL="${KERNEL:-/opt/firecracker/acore-sandbox-kernel-v1.bin}"   # vmlinux-5.10.*
ROOTFS="${ROOTFS:-/opt/firecracker/acore-sandbox-rootfs-v1.ext4}"
MEM_MB="${MEM_MB:-512}"
VCPUS="${VCPUS:-1}"

if [ ! -x "${FC_BIN}" ]; then
  echo "Firecracker binary not found at ${FC_BIN}" >&2
  exit 1
fi

if [ ! -x "${JAILER_BIN}" ]; then
  echo "Jailer binary not found at ${JAILER_BIN}" >&2
  exit 1
fi

if [ ! -e "${KERNEL}" ] || [ ! -e "${ROOTFS}" ]; then
  echo "Kernel or rootfs missing; run setup.sh first." >&2
  exit 1
fi

if [ ! -e /dev/kvm ]; then
  echo "/dev/kvm missing; ensure virtualization/nesting is enabled." >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required." >&2
  exit 1
fi

CHROOT="${CHROOT_BASE}/firecracker/${ID}/root"
API_SOCK="${CHROOT}/run/fc.sock"
MNT_DIR="${MNT_DIR:-/mnt/acore-smoke}"
SMOKE_FILE="/acore-smoke.txt"   # path inside the guest
ROOTFS_COPY="${CHROOT}/rootfs.ext4"
WAIT_SECS="${WAIT_SECS:-30}"
RUN_SECS="${RUN_SECS:-30}"

cleanup_mount() {
  if mountpoint -q "${MNT_DIR}"; then
    umount "${MNT_DIR}" || true
  fi
}
trap cleanup_mount EXIT

echo "==> Preparing jailer chroot at ${CHROOT}"
rm -rf "${CHROOT_BASE}/firecracker/${ID}"
mkdir -p "${CHROOT_BASE}/firecracker/${ID}"
mkdir -p "${CHROOT}/dev" "${CHROOT}/run"

cp "${KERNEL}" "${CHROOT}/vmlinux.bin"
cp "${ROOTFS}" "${ROOTFS_COPY}"
rm -f "${CHROOT}/dev/kvm"

echo "==> Writing smoke init script into rootfs..."
mkdir -p "${MNT_DIR}"
mount -o loop "${ROOTFS_COPY}" "${MNT_DIR}"

cat > "${MNT_DIR}/init-smoke.sh" <<'EOF'
#!/bin/sh
set -eux

SMOKE_OUT="/acore-smoke.txt"

# Mount basic virtual filesystems
mount -t proc proc /proc || true
mount -t sysfs sysfs /sys || true
mount -t tmpfs tmpfs /tmp || true

# Send stdout/stderr to serial console (so we see logs in host output)
exec > /dev/console 2>&1

log() {
  echo "$@"
  printf '%s\n' "$@" >> "${SMOKE_OUT}"
}

# Start with a clean file
: > "${SMOKE_OUT}"

log "[init-smoke] starting"
log "[init-smoke] pwd: $(pwd)"

log "[init-smoke] listing /:"
ls -al / >> "${SMOKE_OUT}" 2>&1 || log "[init-smoke] ls / failed"

log "[init-smoke] listing /opt:"
if [ -d /opt ]; then
  ls -al /opt >> "${SMOKE_OUT}" 2>&1 || log "[init-smoke] ls /opt failed"
else
  log "[init-smoke] /opt missing"
fi

log "[init-smoke] listing terraform path:"
if [ -d /opt/acore-sandbox-bundle/bin ]; then
  ls -al /opt/acore-sandbox-bundle/bin >> "${SMOKE_OUT}" 2>&1 || log "[init-smoke] ls terraform bin failed"
else
  log "[init-smoke] /opt/acore-sandbox-bundle/bin missing"
fi

# Flush logs before Terraform in case it hangs
sync

if [ -x /opt/acore-sandbox-bundle/bin/terraform ]; then
  log "[init-smoke] terraform exists, running --version"
  /opt/acore-sandbox-bundle/bin/terraform --version >> "${SMOKE_OUT}" 2>&1 || log "[init-smoke] terraform failed"
else
  log "[init-smoke] terraform missing"
fi

log "[init-smoke] smoke ok, shutting down"
sync
sleep 3

if command -v poweroff >/dev/null 2>&1; then
  poweroff -f 2>/dev/null || poweroff >/dev/null 2>&1
elif command -v halt >/dev/null 2>&1; then
  halt -f 2>/dev/null || halt >/dev/null 2>&1
fi

echo o > /proc/sysrq-trigger 2>/dev/null || true
sleep 3
exit 0
EOF

chmod +x "${MNT_DIR}/init-smoke.sh"
umount "${MNT_DIR}"

echo "==> Launching jailer + firecracker (id=${ID})"
"${JAILER_BIN}" \
  --id "${ID}" \
  --uid "$(id -u)" \
  --gid "$(id -g)" \
  --chroot-base-dir "${CHROOT_BASE}" \
  --exec-file "${FC_BIN}" \
  -- --api-sock /run/fc.sock &
PID=$!

echo "==> Waiting for API socket at ${API_SOCK}..."
for _ in $(seq 1 20); do
  [ -S "${API_SOCK}" ] && break
  sleep 0.2
done
if [ ! -S "${API_SOCK}" ]; then
  echo "API socket not created; check jailer/firecracker logs." >&2
  if kill -0 "${PID}" 2>/dev/null; then
    kill "${PID}" || true
  fi
  exit 1
fi

curl --unix-socket "${API_SOCK}" -i -X PUT 'http://localhost/machine-config' \
  -H 'Content-Type: application/json' \
  -d "{\"vcpu_count\":${VCPUS},\"mem_size_mib\":${MEM_MB},\"smt\":false}"

curl --unix-socket "${API_SOCK}" -i -X PUT 'http://localhost/boot-source' \
  -H 'Content-Type: application/json' \
  -d '{
    "kernel_image_path": "/vmlinux.bin",
    "boot_args": "console=ttyS0 reboot=k panic=1 pci=off init=/init-smoke.sh root=/dev/vda rw"
  }'

curl --unix-socket "${API_SOCK}" -i -X PUT 'http://localhost/drives/rootfs' \
  -H 'Content-Type: application/json' \
  -d '{"drive_id":"rootfs","path_on_host":"/rootfs.ext4","is_root_device":true,"is_read_only":false}'

curl --unix-socket "${API_SOCK}" -i -X PUT 'http://localhost/actions' \
  -H 'Content-Type: application/json' \
  -d '{"action_type":"InstanceStart"}'

echo "==> Waiting for VM to exit (timeout ${WAIT_SECS}s)..."
sleep "${RUN_SECS}"
if kill -0 "${PID}" 2>/dev/null; then
  echo "Stopping VM after ${RUN_SECS}s run..."
  kill "${PID}" 2>/dev/null || true
  sleep 1
  kill -9 "${PID}" 2>/dev/null || true
fi
wait "${PID}" 2>/dev/null || true

echo "==> Mounting rootfs to read smoke output..."
mkdir -p "${MNT_DIR}"
mount -o loop "${ROOTFS_COPY}" "${MNT_DIR}"
if [ -f "${MNT_DIR}${SMOKE_FILE}" ]; then
  echo "---- terraform --version (inside VM) ----"
  cat "${MNT_DIR}${SMOKE_FILE}"
  echo "---- end ----"
else
  echo "Smoke output file not found at ${MNT_DIR}${SMOKE_FILE}" >&2
fi
umount "${MNT_DIR}"
echo "Smoke test complete."
