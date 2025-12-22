#!/bin/bash
# setup_validation_api_pm2.sh — Setup venv + deps and run sandbox Validation API under PM2

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# `sandbox/` lives under `modules/evaluation/validation/`.
# We want the repo root.
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv-validation-api}"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env.validation_api}"
CREDS_FILE_DEFAULT_ABS="${CREDS_FILE_DEFAULT_ABS:-$REPO_ROOT/gcp-creds.json}"
CREDS_FILE_DEFAULT_REL="${CREDS_FILE_DEFAULT_REL:-gcp-creds.json}"
PROCESS_NAME="${PROCESS_NAME:-alphacore-validation-api}"

echo "==> Repo: $REPO_ROOT"
echo "==> Venv: $VENV_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 not found." >&2
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"
python -m pip install -U pip

REQ_FILE="$REPO_ROOT/modules/requirements.txt"
if [[ ! -f "$REQ_FILE" ]]; then
  echo "[ERROR] requirements not found at $REQ_FILE" >&2
  exit 1
fi
python -m pip install -r "$REQ_FILE"

if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
# AlphaCore Sandbox Validation API (.env)
ALPHACORE_GCP_CREDS_FILE="$CREDS_FILE_DEFAULT_REL"
ALPHACORE_VALIDATION_HTTP_HOST="127.0.0.1"
ALPHACORE_VALIDATION_HTTP_PORT="8888"
ALPHACORE_SANDBOX_PYTHON="/usr/bin/python3"
ALPHACORE_SANDBOX_USE_SUDO="true"
EOF
  chmod 600 "$ENV_FILE" || true
  echo "==> Wrote $ENV_FILE"
fi

resolve_creds_path() {
  local value="${1:-}"
  if [[ -z "$value" ]]; then
    echo ""
    return 0
  fi
  if [[ "$value" == /* ]]; then
    echo "$value"
    return 0
  fi
  echo "$REPO_ROOT/$value"
}

fix_creds_path() {
  # shellcheck disable=SC1090
  source "$ENV_FILE" || true

  local current="${ALPHACORE_GCP_CREDS_FILE:-}"
  local current_resolved
  current_resolved="$(resolve_creds_path "$current")"
  if [[ -n "$current" && -n "$current_resolved" && -f "$current_resolved" ]]; then
    echo "==> Using creds file: $current_resolved"
    return 0
  fi

  if [[ -n "$current" ]]; then
    echo "==> WARNING: creds file missing at: $current" >&2
  fi

  if [[ -f "$CREDS_FILE_DEFAULT_ABS" ]]; then
    echo "==> Setting ALPHACORE_GCP_CREDS_FILE to default: $CREDS_FILE_DEFAULT_ABS"
    tmp="$(mktemp)"
    awk '!/^ALPHACORE_GCP_CREDS_FILE=/' "$ENV_FILE" > "$tmp"
    printf 'ALPHACORE_GCP_CREDS_FILE="%s"\n' "$CREDS_FILE_DEFAULT_REL" >> "$tmp"
    mv "$tmp" "$ENV_FILE"
    chmod 600 "$ENV_FILE" || true
    return 0
  fi

  echo "==> WARNING: default creds file not found at $CREDS_FILE_DEFAULT_ABS" >&2
  echo "==> Create it or edit $ENV_FILE to point at your service account JSON key." >&2
  return 0
}

fix_creds_path

if ! command -v pm2 >/dev/null 2>&1; then
  if [[ "${AUTO_INSTALL_PM2:-}" == "1" ]]; then
    bash "$REPO_ROOT/scripts/validator/main/install_pm2.sh"
  else
    echo "[ERROR] pm2 not found. Install it first:" >&2
    echo "  bash $REPO_ROOT/scripts/validator/main/install_pm2.sh" >&2
    echo "Or re-run with AUTO_INSTALL_PM2=1." >&2
    exit 1
  fi
fi

mkdir -p "$REPO_ROOT/logs/pm2"

if pm2 describe "$PROCESS_NAME" >/dev/null 2>&1; then
  pm2 delete "$PROCESS_NAME" >/dev/null 2>&1 || true
fi
PROCESS_NAME="$PROCESS_NAME" pm2 start "$REPO_ROOT/modules/evaluation/validation/sandbox/pm2.validation_api.config.js"
pm2 save

echo ""
echo "==> Validation API running under PM2"
echo "    Process: $PROCESS_NAME"
echo "    Health:  curl -sS http://${ALPHACORE_VALIDATION_HTTP_HOST:-127.0.0.1}:${ALPHACORE_VALIDATION_HTTP_PORT:-8888}/health"
echo "    Logs:    pm2 logs $PROCESS_NAME"
echo ""
echo "To enable PM2 at boot for this user:"
echo "  sudo env PATH=\\$PATH pm2 startup systemd -u $USER --hp $HOME"
