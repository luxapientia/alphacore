#!/bin/bash
# install_pm2.sh — Install Node.js and PM2 for managing the validator process
# Target OS: Ubuntu 22.04+ (adjust if needed)

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

check_prereqs() {
  log_info "Checking prerequisites..."
  command -v curl >/dev/null || { log_error "curl is required"; exit 1; }
  command -v sudo >/dev/null || { log_error "sudo is required"; exit 1; }
}

pkg_installed() {
  dpkg -s "$1" >/dev/null 2>&1
}

node_major_version() {
  if ! command -v node >/dev/null 2>&1; then
    echo ""
    return 0
  fi
  node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/' || true
}

cleanup_node_conflicts() {
  log_info "Checking for conflicting Ubuntu Node packages (e.g., libnode-dev)..."

  local major
  major="$(node_major_version)"

  # On fresh Ubuntu images it's common to have Ubuntu's Node 12 + libnode-dev
  # installed, which conflicts with NodeSource's nodejs (e.g., 20.x) package.
  if pkg_installed libnode-dev; then
    log_warn "Found libnode-dev installed; removing to avoid NodeSource conflicts."
    sudo apt-get remove -y libnode-dev || true
  fi

  # If we have an old Ubuntu node (or a broken install with missing npm), purge
  # the distro Node stack and reinstall cleanly from NodeSource.
  if [[ -n "${major}" ]] && [[ "${major}" =~ ^[0-9]+$ ]] && (( major < 18 )); then
    log_warn "Found Node.js v${major} on PATH; purging distro Node.js/npm packages before installing NodeSource LTS."
    sudo apt-get purge -y nodejs npm nodejs-doc libnode-dev || true
    sudo apt-get autoremove -y || true
    sudo apt-get -f install -y || true
  elif ! command -v npm >/dev/null 2>&1 && (pkg_installed nodejs || pkg_installed npm); then
    log_warn "Node-related packages are installed but npm is missing/broken; purging and reinstalling."
    sudo apt-get purge -y nodejs npm nodejs-doc libnode-dev || true
    sudo apt-get autoremove -y || true
    sudo apt-get -f install -y || true
  fi
}

install_node() {
  log_info "Installing Node.js (LTS)..."
  cleanup_node_conflicts
  # Use NodeSource install script for latest LTS
  curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
  sudo apt-get install -y nodejs

  if ! command -v node >/dev/null 2>&1; then
    log_error "Node.js install failed (node not found on PATH)."
    exit 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    log_error "Node.js install failed (npm not found on PATH). This usually indicates a partial/failed upgrade."
    log_error "Try re-running this script; if it persists, ensure 'libnode-dev' and Ubuntu 'nodejs' are removed."
    exit 1
  fi

  log_success "Node.js installed: $(node -v)"
  log_success "npm installed: $(npm -v)"
}

install_pm2() {
  log_info "Installing PM2 globally..."
  sudo npm install -g pm2
  log_success "PM2 installed: $(pm2 -v)"
}

setup_pm2_startup() {
  log_info "Configuring PM2 startup service..."
  # Generate and run startup script; use current user
  pm2 startup systemd -u "$USER" --hp "$HOME" || log_warn "PM2 startup may already be configured"
}

main() {
  echo ""
  echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║ Install PM2 for AlphaCore Validator        ║${NC}"
  echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
  echo ""
  check_prereqs
  install_node
  install_pm2
  setup_pm2_startup
  
  echo ""
  log_success "PM2 installation complete"
  echo ""
  echo "Next steps:"
  echo "  1. Start validator under PM2:"
  echo "     pm2 start scripts/validator/process/pm2.config.js --env production"
  echo "  2. Save PM2 process list:"
  echo "     pm2 save"
  echo "  3. Enable PM2 at boot:"
  echo "     sudo env PATH=\$PATH pm2 startup systemd -u $USER --hp $HOME"
  echo "  4. View logs:"
  echo "     pm2 logs alphacore-validator"
}

main "$@"
