#!/bin/bash
# start_validator_pm2.sh - PM2 startup wrapper for AlphaCore validator
# Sources .env, activates venv, and runs validator entry point

set -euo pipefail
IFS=$'\n\t'

# Detect repo root and environment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/venv}"
ENV_FILE="${ENV_FILE:-}"
ENTRYPOINT="${ENTRYPOINT:-scripts/start_validator.py}"  # runs validator + task generation API

# Optional: process name used by PM2
PROCESS_NAME="${PROCESS_NAME:-alphacore-validator}"

# Require explicit env file
if [[ -z "$ENV_FILE" ]]; then
  echo "[ERROR] ENV_FILE is not set. Pass --env-file to launch_pm2.sh or set ENV_FILE before calling this script." >&2
  exit 1
fi

# Export environment from env file if present
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "[ERROR] Environment file not found at $ENV_FILE." >&2
  exit 1
fi

# Activate virtual environment
if [[ -f "$VENV_DIR/bin/activate" ]]; then
  source "$VENV_DIR/bin/activate"
else
  echo "[ERROR] venv not found at $VENV_DIR. Run scripts/validator/main/setup.sh first." >&2
  exit 1
fi

cd "$REPO_ROOT"

# Ensure Python can resolve local packages
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

# Run validator entry point
EXTRA_ARGS=()
case "${ALPHACORE_BT_LOGGING_LEVEL:-}" in
  trace) EXTRA_ARGS+=(--logging.trace) ;;
  debug) EXTRA_ARGS+=(--logging.debug) ;;
  info) EXTRA_ARGS+=(--logging.info) ;;
esac

exec python "$ENTRYPOINT" "${EXTRA_ARGS[@]}" "$@"
