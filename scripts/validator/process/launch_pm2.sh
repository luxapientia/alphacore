#!/bin/bash
# launch_pm2.sh - Helper to register and start an AlphaCore validator via PM2.

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

ENV_FILE="${ENV_FILE:-}"
PROCESS_NAME="${PROCESS_NAME:-}"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/venv}"
ENTRYPOINT="${ENTRYPOINT:-scripts/start_validator.py}"
SKIP_REGISTER="false"
AUTO_CONFIRM="false"

ensure_validator_venv() {
  if [[ -f "$VENV_DIR/bin/activate" ]]; then
    return 0
  fi

  echo "[launch_pm2] venv missing at $VENV_DIR; bootstrapping..."

  if ! command -v python3 >/dev/null 2>&1; then
    echo "[launch_pm2] python3 not found; cannot create venv at $VENV_DIR" >&2
    exit 1
  fi

  mkdir -p "$VENV_DIR"
  if ! python3 -m venv "$VENV_DIR"; then
    echo "[launch_pm2] Failed to create venv at $VENV_DIR (is python3-venv installed?)" >&2
    exit 1
  fi

  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"

  python -m pip install -U pip setuptools wheel >/dev/null 2>&1 || true

  local req_file="$REPO_ROOT/modules/requirements.txt"
  if [[ -f "$REPO_ROOT/requirements.txt" ]]; then
    req_file="$REPO_ROOT/requirements.txt"
  fi

  if [[ -f "$req_file" ]]; then
    echo "[launch_pm2] Installing Python deps from $req_file..."
    if ! python -m pip install -r "$req_file"; then
      echo "[launch_pm2] Failed to install dependencies. Fix pip/network access then re-run." >&2
      deactivate >/dev/null 2>&1 || true
      exit 1
    fi
  else
    echo "[launch_pm2] WARNING: requirements file not found; venv created but deps not installed." >&2
  fi
}

usage() {
  cat <<'EOF'
Usage: launch_pm2.sh --env-file PATH [--process-name NAME] [--venv-dir PATH]
                     [--entrypoint REL_PATH] [--skip-register] [--yes]

Registers the validator (via scripts/validator/main/register.sh) and then
starts it under PM2 using scripts/validator/process/pm2.config.js.

Examples:
  ./launch_pm2.sh --env-file env/validator-a.env --process-name validator-a --yes
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --process-name)
      PROCESS_NAME="$2"
      shift 2
      ;;
    --venv-dir)
      VENV_DIR="$2"
      shift 2
      ;;
    --entrypoint)
      ENTRYPOINT="$2"
      shift 2
      ;;
    --skip-register)
      SKIP_REGISTER="true"
      shift
      ;;
    --yes|-y)
      AUTO_CONFIRM="true"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "[launch_pm2] Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$ENV_FILE" ]]; then
  echo "[launch_pm2] --env-file is required (no default). Create per-validator env files under env/ and pass the path explicitly." >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[launch_pm2] Environment file not found: $ENV_FILE" >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

BT_WALLET_PATH="${BT_WALLET_PATH:-$REPO_ROOT/bt_wallets}"
export BT_WALLET_PATH

PROCESS_NAME="${PROCESS_NAME:-${ALPHACORE_WALLET_HOTKEY:-alphacore-validator}}"
LOG_DIR="${LOG_DIR:-$REPO_ROOT/logs/pm2}"

mkdir -p "$LOG_DIR"

if ! command -v pm2 >/dev/null 2>&1; then
  echo "[launch_pm2] pm2 not found in PATH. Install via scripts/validator/main/install_pm2.sh" >&2
  exit 1
fi

PM2_ENV="${PM2_ENV:-${ALPHACORE_ENV:-local}}"

ensure_validator_venv

if [[ "$SKIP_REGISTER" != "true" ]]; then
  if [[ -f "$VENV_DIR/bin/activate" ]]; then
    # shellcheck disable=SC1090
    source "$VENV_DIR/bin/activate"
  fi
  REGISTER_ARGS=(--env-file "$ENV_FILE")
  if [[ "$AUTO_CONFIRM" == "true" ]]; then
    REGISTER_ARGS+=(--yes)
  fi
  bash "$REPO_ROOT/scripts/validator/main/register.sh" "${REGISTER_ARGS[@]}"
fi

# Remove existing PM2 process with the same name if present.
if pm2 describe "$PROCESS_NAME" >/dev/null 2>&1; then
  pm2 delete "$PROCESS_NAME" >/dev/null 2>&1 || true
fi

ENV_FILE="$ENV_FILE" \
PROCESS_NAME="$PROCESS_NAME" \
VENV_DIR="$VENV_DIR" \
ENTRYPOINT="$ENTRYPOINT" \
LOG_DIR="$LOG_DIR" \
pm2 start "$SCRIPT_DIR/pm2.config.js" --name "$PROCESS_NAME" --update-env

pm2 save >/dev/null 2>&1 || true

echo "[launch_pm2] Validator '$PROCESS_NAME' started. View logs with: pm2 logs $PROCESS_NAME"
