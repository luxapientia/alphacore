#!/bin/bash
# start_miner_pm2.sh - PM2 startup wrapper for AlphaCore miner
# Sources .env, activates venv, and runs miner entry point

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/venv}"
ENV_FILE="${ENV_FILE:-}"
ENTRYPOINT="${ENTRYPOINT:-neurons/miner.py}"

if [[ -z "$ENV_FILE" ]]; then
  echo "[ERROR] ENV_FILE is not set. Pass --env-out to launch_miner.sh or set ENV_FILE before calling this script." >&2
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "[ERROR] Environment file not found at $ENV_FILE." >&2
  exit 1
fi

if [[ -f "$VENV_DIR/bin/activate" ]]; then
  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"
else
  echo "[ERROR] venv not found at $VENV_DIR. Create it (or re-run launch_miner.sh)." >&2
  exit 1
fi

cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

EXTRA_ARGS=()
case "${ALPHACORE_BT_LOGGING_LEVEL:-}" in
  trace) EXTRA_ARGS+=(--logging.trace) ;;
  debug) EXTRA_ARGS+=(--logging.debug) ;;
  info) EXTRA_ARGS+=(--logging.info) ;;
esac

exec python "$ENTRYPOINT" "${EXTRA_ARGS[@]}" "$@"

