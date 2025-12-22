#!/bin/bash
# setup_systemd.sh — Install and manage systemd service for AlphaCore validator
# This will install a templated service using your current username

set -euo pipefail
IFS=$'\n\t'

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SERVICE_SRC="$REPO_ROOT/scripts/validator/process/alphacore-validator.service"
SERVICE_NAME="alphacore-validator@${USER}.service"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}"

require_root() {
  if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root (sudo)"
    exit 1
  fi
}

install_service() {
  log_info "Installing systemd service to $SERVICE_DST"
  cp "$SERVICE_SRC" "$SERVICE_DST"
  chmod 644 "$SERVICE_DST"
  systemctl daemon-reload
  log_success "Service installed"
}

enable_service() {
  log_info "Enabling service $SERVICE_NAME"
  systemctl enable "$SERVICE_NAME"
  log_success "Service enabled"
}

start_service() {
  log_info "Starting service $SERVICE_NAME"
  systemctl start "$SERVICE_NAME"
  systemctl status "$SERVICE_NAME" --no-pager || true
}

remove_service() {
  log_info "Stopping and removing service $SERVICE_NAME"
  systemctl stop "$SERVICE_NAME" || true
  systemctl disable "$SERVICE_NAME" || true
  rm -f "$SERVICE_DST"
  systemctl daemon-reload
  log_success "Service removed"
}

print_usage() {
  cat <<EOF
Usage: sudo bash setup_systemd.sh [command]

Commands:
  install   Copy service file and reload daemon
  enable    Enable service at boot
  start     Start service now
  remove    Stop, disable, and remove service

Examples:
  sudo bash setup_systemd.sh install
  sudo bash setup_systemd.sh enable
  sudo bash setup_systemd.sh start
EOF
}

main() {
  if [[ $# -lt 1 ]]; then
    print_usage
    exit 1
  fi
  
  case "$1" in
    install)
      require_root
      install_service
      ;;
    enable)
      require_root
      enable_service
      ;;
    start)
      require_root
      start_service
      ;;
    remove)
      require_root
      remove_service
      ;;
    *)
      print_usage
      exit 1
      ;;
  esac
}

main "$@"
