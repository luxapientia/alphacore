#!/bin/bash
# register.sh - Register validator on Bittensor subnet
# Standalone registration script (can be run independently)

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# pip install --user puts console scripts in ~/.local/bin, which is not always on PATH
# (especially in non-login shells / PM2 contexts).
if [[ -d "$HOME/.local/bin" && ":${PATH:-}:" != *":$HOME/.local/bin:"* ]]; then
  export PATH="$HOME/.local/bin:${PATH:-}"
fi

# Defaults from environment or fallback
WALLET_NAME="${ALPHACORE_WALLET_NAME:-}"
WALLET_HOTKEY="${ALPHACORE_WALLET_HOTKEY:-}"
NETUID="${ALPHACORE_NETUID:-${AC_NETUID:-1}}"
NETWORK_NAME="${ALPHACORE_NETWORK:-${BT_NETWORK:-local}}"
CHAIN_ENDPOINT="${ALPHACORE_CHAIN_ENDPOINT:-${BT_CHAIN_ENDPOINT:-}}"
BT_WALLET_PATH="${BT_WALLET_PATH:-${ALPHACORE_WALLET_PATH:-$HOME/.bittensor/wallets}}"
ENV_FILE=""
AUTO_CONFIRM="false"
declare -a BTCLI_ARGS=()
BTCLI_OVERVIEW_TIMEOUT_S="${BTCLI_OVERVIEW_TIMEOUT_S:-15}"

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

warn_msg() {
  echo -e "${YELLOW}[WARN]${NC} $1"
}

print_header() {
  echo ""
  echo -e "${GREEN}╔════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║ $1${NC}"
  echo -e "${GREEN}╚════════════════════════════════════╝${NC}"
  echo ""
}

print_usage() {
  cat << EOF
Usage: bash register.sh [OPTIONS]

Register validator on Bittensor subnet.

Options:
  --wallet NAME         Wallet name (coldkey)
  --hotkey NAME         Hotkey name
  --netuid NUM          Subnet ID (default: from env or 1)
  --network NAME        Bittensor network alias (default: local)
  --chain-endpoint URL  Subtensor chain endpoint (wss:// or ws://)
  --env-file PATH       Path to .env file (loads ALPHACORE_* variables)
  --yes                 Auto-confirm prompts
  --help                Show this help message

Environment Variables:
  ALPHACORE_WALLET_NAME     Wallet name (coldkey)
  ALPHACORE_WALLET_HOTKEY   Hotkey name
  ALPHACORE_NETUID          Subnet ID
  ALPHACORE_CHAIN_ENDPOINT  Chain endpoint (ws://...)
  ALPHACORE_NETWORK         Network alias (local|test|finney)

Example:
  bash register.sh --wallet validator-a --hotkey validator-a --netuid 1

EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case $1 in
      --env-file)
        ENV_FILE="$2"
        shift 2
        if [[ ! -f "$ENV_FILE" ]]; then
          handle_error "Env file not found: $ENV_FILE"
        fi
        set -a
        # shellcheck disable=SC1090
        source "$ENV_FILE"
        set +a
        WALLET_NAME="${ALPHACORE_WALLET_NAME:-$WALLET_NAME}"
        WALLET_HOTKEY="${ALPHACORE_WALLET_HOTKEY:-$WALLET_HOTKEY}"
        NETUID="${ALPHACORE_NETUID:-$NETUID}"
        NETWORK_NAME="${ALPHACORE_NETWORK:-$NETWORK_NAME}"
        CHAIN_ENDPOINT="${ALPHACORE_CHAIN_ENDPOINT:-$CHAIN_ENDPOINT}"
        BT_WALLET_PATH="${BT_WALLET_PATH:-${ALPHACORE_WALLET_PATH:-$BT_WALLET_PATH}}"
        ;;
      --wallet)
        WALLET_NAME="$2"
        shift 2
        ;;
      --hotkey)
        WALLET_HOTKEY="$2"
        shift 2
        ;;
      --netuid)
        NETUID="$2"
        shift 2
        ;;
      --network)
        NETWORK_NAME="$2"
        shift 2
        ;;
      --chain-endpoint)
        CHAIN_ENDPOINT="$2"
        shift 2
        ;;
      --yes|-y)
        AUTO_CONFIRM="true"
        shift
        ;;
      --help)
        print_usage
        exit 0
        ;;
      *)
        echo "Unknown option: $1"
        print_usage
        exit 1
        ;;
    esac
  done
}

get_wallet_info() {
  if [[ -z "$WALLET_NAME" ]]; then
    echo -e "${YELLOW}Enter your validator wallet name (coldkey):${NC}"
    read -p "  Wallet name: " WALLET_NAME
  fi
  
  if [[ -z "$WALLET_HOTKEY" ]]; then
    echo -e "${YELLOW}Enter your validator hotkey name:${NC}"
    read -p "  Hotkey name: " WALLET_HOTKEY
  fi
  
  if [[ -z "$WALLET_NAME" || -z "$WALLET_HOTKEY" ]]; then
    handle_error "Wallet name and hotkey are required"
  fi
}

check_already_registered() {
  info_msg "Checking if already registered..."
  
  if command -v btcli &> /dev/null; then
    export BTCLI_DEBUG_FILE="${BTCLI_DEBUG_FILE:-/tmp/btcli-debug.txt}"
    local overview_json
    overview_json=$(
      {
        if command -v timeout >/dev/null 2>&1; then
          timeout "${BTCLI_OVERVIEW_TIMEOUT_S}s" btcli wallet overview \
            --wallet-name "$WALLET_NAME" \
            --hotkey "$WALLET_HOTKEY" \
            --wallet-path "$BT_WALLET_PATH" \
            --netuids "$NETUID" \
            "${BTCLI_ARGS[@]}" \
            --quiet \
            --json-output 2>/dev/null
        else
          btcli wallet overview \
            --wallet-name "$WALLET_NAME" \
            --hotkey "$WALLET_HOTKEY" \
            --wallet-path "$BT_WALLET_PATH" \
            --netuids "$NETUID" \
            "${BTCLI_ARGS[@]}" \
            --quiet \
            --json-output 2>/dev/null
        fi
      } || true
    )

    if [[ -z "${overview_json:-}" ]]; then
      warn_msg "Wallet overview check timed out or failed; proceeding to registration."
      return 1
    fi

    if python3 - "$overview_json" "$NETUID" "$WALLET_HOTKEY" <<'PY' >/dev/null 2>&1; then
import json
import sys

raw, netuid_raw, hotkey_name = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.loads(raw) if raw else {}
target = int(netuid_raw)
subnets = data.get("subnets", []) or []
for sn in subnets:
    if int(sn.get("netuid", -1)) != target:
        continue
    for neuron in (sn.get("neurons", []) or []):
        if neuron.get("hotkey") == hotkey_name:
            raise SystemExit(0)
raise SystemExit(1)
PY
      success_msg "Validator already registered on netuid $NETUID"
      return 0
    fi
  fi

  return 1
}

register_validator() {
  print_header "Validator Registration"
  
  info_msg "Registering validator on netuid $NETUID..."
  info_msg "Wallet: $WALLET_NAME | Hotkey: $WALLET_HOTKEY"
  info_msg "Network: $NETWORK_NAME"
  if [[ -n "$CHAIN_ENDPOINT" ]]; then
    info_msg "Chain endpoint: $CHAIN_ENDPOINT"
  fi
  echo ""
  
  if check_already_registered; then
    warn_msg "Validator appears to be already registered. Skipping."
    return 0
  fi
  
  echo -e "${YELLOW}Registration Details:${NC}"
  echo "  Subnet ID (netuid): $NETUID"
  echo "  Wallet (coldkey):   $WALLET_NAME"
  echo "  Hotkey:             $WALLET_HOTKEY"
  echo "  Network:            $NETWORK_NAME"
  [[ -n "$CHAIN_ENDPOINT" ]] && echo "  Endpoint:           $CHAIN_ENDPOINT"
  echo ""
  echo -e "${YELLOW}⚠️  This will cost approximately 1 TAO (reclaimed after deregistration)${NC}"
  echo ""
  if [[ "$AUTO_CONFIRM" != "true" ]]; then
    read -p "Proceed with registration? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      warn_msg "Registration cancelled"
      exit 0
    fi
  else
    echo "[INFO] Auto-confirm enabled; continuing without prompt."
  fi
  
  info_msg "Running registration command..."
  echo "  btcli subnets register --netuid $NETUID --wallet-name $WALLET_NAME --hotkey $WALLET_HOTKEY --wallet-path $BT_WALLET_PATH ${BTCLI_ARGS[*]} --no-prompt"
  echo ""

  export BTCLI_DEBUG_FILE="${BTCLI_DEBUG_FILE:-/tmp/btcli-debug.txt}"
  register_cmd_args=("${BTCLI_ARGS[@]}" --no-prompt --quiet --json-output)
  register_json=$(
    btcli subnets register \
        --netuid "$NETUID" \
        --wallet-name "$WALLET_NAME" \
        --hotkey "$WALLET_HOTKEY" \
        --wallet-path "$BT_WALLET_PATH" \
        "${register_cmd_args[@]}" 2>/dev/null || true
  )

  register_success=$(python3 - "$register_json" <<'PY' 2>/dev/null || echo "false"
import json
import sys

raw = sys.argv[1] if len(sys.argv) > 1 else ""
data = json.loads(raw) if raw else {}
print("true" if data.get("success") is True else "false")
PY
)

  if [[ "$register_success" != "true" ]]; then
    handle_error "Registration failed (insufficient balance or chain not reachable). Fund the wallet and try again."
  fi

  # Confirm the registration appears in wallet overview (handles inclusion delays).
  for _ in $(seq 1 30); do
    overview_json=$(
      {
        if command -v timeout >/dev/null 2>&1; then
          timeout "${BTCLI_OVERVIEW_TIMEOUT_S}s" btcli wallet overview \
            --wallet-name "$WALLET_NAME" \
            --hotkey "$WALLET_HOTKEY" \
            --wallet-path "$BT_WALLET_PATH" \
            --netuids "$NETUID" \
            "${BTCLI_ARGS[@]}" \
            --quiet \
            --json-output 2>/dev/null
        else
          btcli wallet overview \
            --wallet-name "$WALLET_NAME" \
            --hotkey "$WALLET_HOTKEY" \
            --wallet-path "$BT_WALLET_PATH" \
            --netuids "$NETUID" \
            "${BTCLI_ARGS[@]}" \
            --quiet \
            --json-output 2>/dev/null
        fi
      } || true
    )
    if python3 - "$overview_json" "$NETUID" "$WALLET_HOTKEY" <<'PY' >/dev/null 2>&1; then
import json
import sys

raw, netuid_raw, hotkey_name = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.loads(raw) if raw else {}
target = int(netuid_raw)
subnets = data.get("subnets", []) or []
for sn in subnets:
    if int(sn.get("netuid", -1)) != target:
        continue
    for neuron in (sn.get("neurons", []) or []):
        if neuron.get("hotkey") == hotkey_name:
            raise SystemExit(0)
raise SystemExit(1)
PY
      success_msg "Validator registered successfully!"
      return 0
    fi
    sleep 1
  done

  handle_error "Registration transaction submitted but validator is still not visible on netuid $NETUID."
}

verify_registration() {
  print_header "Verifying Registration"
  
  if ! command -v btcli &> /dev/null; then
    warn_msg "btcli not found, cannot verify registration"
    return
  fi
  
  info_msg "Checking subnet metagraph..."
  export BTCLI_DEBUG_FILE="${BTCLI_DEBUG_FILE:-/tmp/btcli-debug.txt}"
  btcli subnets list "${BTCLI_ARGS[@]}" 2>/dev/null || true
  echo ""
  
  info_msg "Checking wallet overview..."
  btcli wallet overview \
    --wallet-name "$WALLET_NAME" \
    --hotkey "$WALLET_HOTKEY" \
    --wallet-path "$BT_WALLET_PATH" \
    "${BTCLI_ARGS[@]}" 2>/dev/null || true
}

main() {
  parse_args "$@"

  WALLET_NAME="${WALLET_NAME:-${ALPHACORE_WALLET_NAME:-}}"
  WALLET_HOTKEY="${WALLET_HOTKEY:-${ALPHACORE_WALLET_HOTKEY:-}}"
  NETUID="${NETUID:-${ALPHACORE_NETUID:-${AC_NETUID:-1}}}"
  NETWORK_NAME="${NETWORK_NAME:-${ALPHACORE_NETWORK:-${BT_NETWORK:-local}}}"
  CHAIN_ENDPOINT="${CHAIN_ENDPOINT:-${ALPHACORE_CHAIN_ENDPOINT:-${BT_CHAIN_ENDPOINT:-}}}"

  BTCLI_ARGS=()
  # If a chain endpoint is provided, prefer it and avoid --network aliases like "local"
  # which btcli may not recognize.
  if [[ -n "$CHAIN_ENDPOINT" ]]; then
    BTCLI_ARGS+=(--subtensor.chain_endpoint "$CHAIN_ENDPOINT")
  elif [[ -n "$NETWORK_NAME" ]]; then
    BTCLI_ARGS+=(--network "$NETWORK_NAME")
  fi
  
  echo ""
  echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║ AlphaCore Validator - Registration     ║${NC}"
  echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"
  echo ""
  
  get_wallet_info
  register_validator
  verify_registration
  
  print_header "Registration Complete!"
  echo ""
  echo "Next steps:"
  echo "  1. Verify registration:"
  echo "     btcli wallet overview --wallet-name $WALLET_NAME --hotkey $WALLET_HOTKEY --wallet-path $BT_WALLET_PATH ${BTCLI_ARGS[*]}"
  echo "  2. Check validator permits: Look for VPERMIT * in wallet overview"
  echo "  3. Start validator under PM2:"
  echo "     scripts/validator/process/launch_pm2.sh --env-file ${ENV_FILE:-env/validator.env} --process-name ${WALLET_HOTKEY:-alphacore-validator}"
  echo ""
}

main "$@"
