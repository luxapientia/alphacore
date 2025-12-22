#!/bin/bash
# start_validation_api_pm2.sh - PM2 startup wrapper for AlphaCore sandbox validation API
# Activates a venv and runs the Firecracker-backed validation API worker pool.

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# `sandbox/` lives under `modules/evaluation/validation/`.
# We want the repo root.
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv-validation-api}"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env.validation_api}"

PRESET_ALPHACORE_GCP_CREDS_FILE="${ALPHACORE_GCP_CREDS_FILE:-}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if [[ -n "$PRESET_ALPHACORE_GCP_CREDS_FILE" ]]; then
  export ALPHACORE_GCP_CREDS_FILE="$PRESET_ALPHACORE_GCP_CREDS_FILE"
fi

if [[ -n "${ALPHACORE_GCP_CREDS_FILE:-}" && "${ALPHACORE_GCP_CREDS_FILE}" != /* ]]; then
  export ALPHACORE_GCP_CREDS_FILE="$REPO_ROOT/${ALPHACORE_GCP_CREDS_FILE}"
fi

DEFAULT_CREDS_FILE="$REPO_ROOT/gcp-creds.json"
if [[ -z "${ALPHACORE_GCP_CREDS_FILE:-}" && -f "$DEFAULT_CREDS_FILE" ]]; then
  export ALPHACORE_GCP_CREDS_FILE="$DEFAULT_CREDS_FILE"
fi
if [[ -n "${ALPHACORE_GCP_CREDS_FILE:-}" && ! -f "${ALPHACORE_GCP_CREDS_FILE}" && -f "$DEFAULT_CREDS_FILE" ]]; then
  export ALPHACORE_GCP_CREDS_FILE="$DEFAULT_CREDS_FILE"
fi

if [[ -f "$VENV_DIR/bin/activate" ]]; then
  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"
else
  echo "[ERROR] venv not found at $VENV_DIR. Create it and install deps first." >&2
  exit 1
fi

cd "$REPO_ROOT"

# Ensure `import modules` works even when invoked from a different CWD.
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:${PYTHONPATH}}"

exec python -m modules.evaluation.validation.sandbox.validation_api
