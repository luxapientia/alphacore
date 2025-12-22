  #!/usr/bin/env bash
  set -e pipefail

  pass() { echo "[OK] $*"; }
  warn() { echo "[WARN] $*"; }
  fail() { echo "[FAIL] $*"; }

  arch=$(uname -m)
  echo "Arch: $arch"
  echo "CPU flags (vmx=intel, svm=amd):"
  egrep -m1 'vmx|svm' /proc/cpuinfo || warn "No vmx/svm flag visible (likely nesting disabled)."

  if [ -e /dev/kvm ]; then
    stat /dev/kvm || warn "/dev/kvm exists but not readable (permissions?)"
    pass "/dev/kvm present"
  else
    fail "/dev/kvm missing"
  fi

  if lsmod | grep -q '^kvm'; then
    pass "kvm module loaded: $(lsmod | grep '^kvm')"
  else
    warn "kvm module not loaded"
  fi

  if lsmod | grep -q '^kvm_intel'; then
    nesting=$(cat /sys/module/kvm_intel/parameters/nested 2>/dev/null || echo "unknown")
    echo "kvm_intel nested: $nesting"
  elif lsmod | grep -q '^kvm_amd'; then
    nesting=$(cat /sys/module/kvm_amd/parameters/nested 2>/dev/null || echo "unknown")
    echo "kvm_amd nested: $nesting"
  fi

  echo "dmesg (kvm):"
  dmesg | grep -i kvm | tail -n 5 || true

  if command -v virt-host-validate >/dev/null; then
    echo "virt-host-validate:"
    virt-host-validate qemu || true
  fi