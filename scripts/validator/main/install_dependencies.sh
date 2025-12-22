#!/bin/bash
# install_dependencies.sh - Install ONLY system dependencies for AlphaCore validator
# Based on Autoppia pattern: system deps only, no Python environment setup

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

handle_error() {
  echo -e "${RED}[ERROR]${NC} $1" >&2
  exit 1
}

success_msg() {
  echo -e "${GREEN}[SUCCESS]${NC} $1"
}

info_msg() {
  echo -e "${BLUE}[INFO]${NC} $1"
}

print_header() {
  echo ""
  echo -e "${GREEN}╔════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║ $1${NC}"
  echo -e "${GREEN}╚════════════════════════════════════╝${NC}"
  echo ""
}

check_os() {
  print_header "Step 1: Checking Operating System"
  
  if [[ ! -f /etc/os-release ]]; then
    handle_error "Cannot detect operating system"
  fi
  
  . /etc/os-release
  info_msg "OS: $NAME $VERSION"
  
  if [[ "$ID" != "ubuntu" ]]; then
    echo -e "${YELLOW}[WARN]${NC} This script is optimized for Ubuntu. You may need to adjust package names."
  fi
  
  success_msg "OS check complete"
}

install_core_tools() {
  print_header "Step 2: Installing Core Tools"
  
  info_msg "Updating apt package lists..."
  sudo apt update -y || handle_error "Failed to update apt lists"
  
  info_msg "Installing core tools (sudo, curl, git, build-essential)..."
  sudo apt install -y \
    sudo \
    software-properties-common \
    lsb-release \
    curl \
    wget \
    git \
    build-essential \
    cmake \
    unzip \
    || handle_error "Failed to install core tools"
  
  success_msg "Core tools installed"
}

install_python() {
  print_header "Step 3: Installing Python 3.11+"
  
  if command -v python3.11 &> /dev/null; then
    info_msg "Python 3.11 already installed: $(python3.11 --version)"
    success_msg "Python check complete"
    return 0
  fi
  
  info_msg "Adding Python PPA repository..."
  sudo add-apt-repository ppa:deadsnakes/ppa -y \
    || handle_error "Failed to add Python PPA"
  
  sudo apt update -y || handle_error "Failed to refresh apt lists"
  
  info_msg "Installing Python 3.11 and development files..."
  sudo apt install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    || handle_error "Failed to install Python 3.11"
  
  success_msg "Python 3.11 installed: $(python3.11 --version)"
}

install_docker() {
  print_header "Step 4: Installing Docker"
  
  if command -v docker &> /dev/null; then
    info_msg "Docker already installed: $(docker --version)"
    success_msg "Docker check complete"
    return 0
  fi
  
  info_msg "Installing Docker prerequisites..."
  sudo apt install -y \
    ca-certificates \
    gnupg \
    lsb-release \
    || handle_error "Failed to install Docker prerequisites"
  
  info_msg "Adding Docker GPG key..."
  sudo mkdir -p /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    || handle_error "Failed to add Docker GPG key"
  
  info_msg "Adding Docker repository..."
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    $(lsb_release -cs) stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null \
    || handle_error "Failed to add Docker repository"
  
  info_msg "Installing Docker Engine..."
  sudo apt update -y || handle_error "Failed to update apt after adding Docker repo"
  sudo apt install -y \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin \
    || handle_error "Failed to install Docker"
  
  info_msg "Adding current user to docker group..."
  sudo usermod -aG docker "$USER" || echo "Could not add user to docker group (non-fatal)"
  
  success_msg "Docker installed: $(docker --version)"
  echo -e "${YELLOW}[NOTE]${NC} You may need to log out and back in for docker group permissions to take effect"
}

verify_installation() {
  print_header "Step 5: Verifying Installation"
  
  local all_good=true
  
  # Check Python
  if command -v python3.11 &> /dev/null; then
    success_msg "Python 3.11: $(python3.11 --version)"
  else
    echo -e "${RED}[ERROR]${NC} Python 3.11 not found"
    all_good=false
  fi
  
  # Check Docker
  if command -v docker &> /dev/null; then
    success_msg "Docker: $(docker --version)"
  else
    echo -e "${RED}[ERROR]${NC} Docker not found"
    all_good=false
  fi
  
  # Check Git
  if command -v git &> /dev/null; then
    success_msg "Git: $(git --version)"
  else
    echo -e "${RED}[ERROR]${NC} Git not found"
    all_good=false
  fi
  
  if [ "$all_good" = false ]; then
    handle_error "Some dependencies are missing. Please check the errors above."
  fi
  
  echo ""
  success_msg "All system dependencies verified!"
}

main() {
  echo ""
  echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║ AlphaCore Validator - System Dependencies  ║${NC}"
  echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
  echo ""
  echo "This script installs system-level dependencies only."
  echo "For Python environment setup, run setup.sh after this."
  echo ""
  
  check_os
  install_core_tools
  install_python
  install_docker
  verify_installation
  
  print_header "Installation Complete!"
  echo ""
  echo "Next steps:"
  echo "  1. Log out and back in (for Docker group permissions)"
  echo "  2. Run: bash scripts/validator/main/setup.sh"
  echo ""
}

main "$@"
