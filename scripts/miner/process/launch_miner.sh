#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

NETWORK=""
NETUID=""
WALLET_NAME=""
WALLET_HOTKEY=""
WALLET_PATH="${BT_WALLET_PATH:-$HOME/.bittensor/wallets}"

AXON_PORT=""
AXON_IP=""
EXTERNAL_IP=""
EXTERNAL_PORT=""

CHAIN_ENDPOINT=""

PROCESS_NAME=""
ENV_OUT=""
VENV_DIR=""
ENTRYPOINT="neurons/miner.py"
LOG_DIR=""
AUTO_INSTALL_PM2="0"
BT_LOGGING_LEVEL="info"
ALLOW_NON_REGISTERED="false"
FORCE_VALIDATOR_PERMIT="true"
MIN_STAKE=""

usage() {
  cat <<'EOF'
Launch an AlphaCore miner under PM2.

Example:
  bash scripts/miner/process/launch_miner.sh \
    --network test --netuid <netuid> \
    --wallet-name <wallet_name> --hotkey <hotkey> \
    --wallet-path "$HOME/.bittensor/wallets" \
    --axon-port 8091 --external-ip <ip>

Required:
  --network NAME         local|test|finney
  --netuid NUM
  --wallet-name NAME
  --hotkey NAME
  --axon-port PORT
  --external-ip IP

Optional:
  --external-port PORT   Default: same as --axon-port
  --axon-ip IP           Bind IP (defaults to bittensor/OS default)
  --chain-endpoint URL   Override subtensor endpoint
  --entrypoint PATH      Default: neurons/miner.py (use your own e.g. miney.py)
  --process-name NAME    Default: miner-<hotkey>-<network>
  --env-out PATH         Default: env/<network>/miner-<wallet>-<hotkey>.env
  --venv-dir PATH        Default: <repo>/venv
  --log-dir PATH         Default: <repo>/logs/pm2
  --auto-install-pm2     Install pm2 if missing (uses scripts/validator/main/install_pm2.sh)
  --bt-logging-level LVL  trace|debug|info (default: info)
  --allow-non-registered  Accept queries from non-registered hotkeys (dangerous; dev only)
  --no-force-validator-permit  Do not require validator permit (dev/test only)
  --min-stake N           Minimum stake required for callers (default: chain config)
  --help|-h              Show this help
EOF
}

ensure_venv() {
  if [[ -f "$VENV_DIR/bin/activate" ]]; then
    return 0
  fi
  echo "[launch_miner] venv missing at $VENV_DIR; bootstrapping..."
  if ! command -v python3 >/dev/null 2>&1; then
    echo "[launch_miner] python3 not found; cannot create venv" >&2
    exit 1
  fi
  mkdir -p "$VENV_DIR"
  if ! python3 -m venv "$VENV_DIR"; then
    echo "[launch_miner] Failed to create venv at $VENV_DIR (is python3-venv installed?)" >&2
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
    echo "[launch_miner] Installing Python deps from $req_file..."
    python -m pip install -r "$req_file"
  else
    echo "[launch_miner] WARNING: requirements file not found; venv created but deps not installed." >&2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --network) NETWORK="$2"; shift 2 ;;
    --netuid) NETUID="$2"; shift 2 ;;
    --wallet-name) WALLET_NAME="$2"; shift 2 ;;
    --hotkey) WALLET_HOTKEY="$2"; shift 2 ;;
    --wallet-path) WALLET_PATH="$2"; shift 2 ;;
    --axon-port) AXON_PORT="$2"; shift 2 ;;
    --axon-ip) AXON_IP="$2"; shift 2 ;;
    --external-ip) EXTERNAL_IP="$2"; shift 2 ;;
    --external-port) EXTERNAL_PORT="$2"; shift 2 ;;
    --chain-endpoint) CHAIN_ENDPOINT="$2"; shift 2 ;;
    --entrypoint) ENTRYPOINT="$2"; shift 2 ;;
    --process-name) PROCESS_NAME="$2"; shift 2 ;;
    --env-out) ENV_OUT="$2"; shift 2 ;;
    --venv-dir) VENV_DIR="$2"; shift 2 ;;
    --log-dir) LOG_DIR="$2"; shift 2 ;;
    --auto-install-pm2) AUTO_INSTALL_PM2="1"; shift ;;
    --bt-logging-level) BT_LOGGING_LEVEL="$2"; shift 2 ;;
    --allow-non-registered) ALLOW_NON_REGISTERED="true"; shift ;;
    --no-force-validator-permit) FORCE_VALIDATOR_PERMIT="false"; shift ;;
    --min-stake) MIN_STAKE="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *)
      echo "[launch_miner] Unknown option: $1" >&2
      usage
      exit 1 ;;
  esac
done

if [[ -z "$NETWORK" || -z "$NETUID" || -z "$WALLET_NAME" || -z "$WALLET_HOTKEY" ]]; then
  echo "[launch_miner] Missing required args: --network, --netuid, --wallet-name, --hotkey" >&2
  usage
  exit 1
fi
if [[ -z "$AXON_PORT" || -z "$EXTERNAL_IP" ]]; then
  echo "[launch_miner] Missing required args: --axon-port and --external-ip" >&2
  usage
  exit 1
fi

if [[ -z "$EXTERNAL_PORT" ]]; then
  EXTERNAL_PORT="$AXON_PORT"
fi

if [[ -z "$PROCESS_NAME" ]]; then
  PROCESS_NAME="${WALLET_HOTKEY}-netuid${NETUID}"
fi
if [[ -z "$ENV_OUT" ]]; then
  ENV_OUT="$REPO_ROOT/env/${NETWORK}/miner-${WALLET_NAME}-${WALLET_HOTKEY}.env"
fi
if [[ -z "$VENV_DIR" ]]; then
  VENV_DIR="$REPO_ROOT/venv"
fi
if [[ -z "$LOG_DIR" ]]; then
  LOG_DIR="$REPO_ROOT/logs/pm2"
fi

mkdir -p "$(dirname "$ENV_OUT")"
mkdir -p "$LOG_DIR"

if ! command -v pm2 >/dev/null 2>&1; then
  if [[ "$AUTO_INSTALL_PM2" == "1" ]]; then
    bash "$REPO_ROOT/scripts/validator/main/install_pm2.sh"
  else
    echo "[launch_miner] pm2 not found in PATH. Install via scripts/validator/main/install_pm2.sh (or re-run with --auto-install-pm2)" >&2
    exit 1
  fi
fi

ensure_venv

cat >"$ENV_OUT" <<ENVFILE
# Auto-generated by scripts/miner/process/launch_miner.sh

ALPHACORE_BT_LOGGING_LEVEL="${BT_LOGGING_LEVEL}"

ALPHACORE_NETWORK="${NETWORK}"
BT_NETWORK="${NETWORK}"
ALPHACORE_NETUID="${NETUID}"

ALPHACORE_WALLET_NAME="${WALLET_NAME}"
ALPHACORE_WALLET_HOTKEY="${WALLET_HOTKEY}"
ALPHACORE_WALLET_PATH="${WALLET_PATH}"
BT_WALLET_PATH="${WALLET_PATH}"

ALPHACORE_AXON_PORT="${AXON_PORT}"
ALPHACORE_AXON_EXTERNAL_IP="${EXTERNAL_IP}"
ALPHACORE_AXON_EXTERNAL_PORT="${EXTERNAL_PORT}"

# Blacklist controls (dev/test defaults can be overridden via CLI flags)
ALPHACORE_BLACKLIST_ALLOW_NON_REGISTERED="${ALLOW_NON_REGISTERED}"
ALPHACORE_BLACKLIST_FORCE_VALIDATOR_PERMIT="${FORCE_VALIDATOR_PERMIT}"
ENVFILE

if [[ -n "$AXON_IP" ]]; then
  printf 'ALPHACORE_AXON_IP="%s"\n' "$AXON_IP" >>"$ENV_OUT"
fi
if [[ -n "$CHAIN_ENDPOINT" ]]; then
  printf 'ALPHACORE_CHAIN_ENDPOINT="%s"\n' "$CHAIN_ENDPOINT" >>"$ENV_OUT"
fi
if [[ -n "$MIN_STAKE" ]]; then
  printf 'ALPHACORE_BLACKLIST_MINIMUM_STAKE="%s"\n' "$MIN_STAKE" >>"$ENV_OUT"
fi

chmod 600 "$ENV_OUT" || true

if pm2 describe "$PROCESS_NAME" >/dev/null 2>&1; then
  pm2 delete "$PROCESS_NAME" >/dev/null 2>&1 || true
fi

ENV_FILE="$ENV_OUT" \
PROCESS_NAME="$PROCESS_NAME" \
VENV_DIR="$VENV_DIR" \
ENTRYPOINT="$ENTRYPOINT" \
LOG_DIR="$LOG_DIR" \
pm2 start "$REPO_ROOT/scripts/miner/process/pm2.miner.config.js" --name "$PROCESS_NAME" --update-env

pm2 save >/dev/null 2>&1 || true
echo "[launch_miner] Miner '$PROCESS_NAME' started. View logs with: pm2 logs $PROCESS_NAME"
