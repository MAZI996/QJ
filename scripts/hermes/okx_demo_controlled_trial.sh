#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="${TRADINGAGENTS_PROJECT_ROOT:-/opt/data/projects/QJ-current}"
PYTHON_BIN="${TRADINGAGENTS_PYTHON_BIN:-/opt/data/projects/QJ/.venv/bin/python}"
ENV_FILE="${TRADINGAGENTS_OKX_DEMO_ENV_FILE:-/opt/data/projects/QJ/.env.okx.demo.local}"
STATE_DIR="${TRADINGAGENTS_STATE_DIR:-/opt/data/.tradingagents/crypto}"
SYMBOLS="${TRADINGAGENTS_SYMBOLS:-BTC,ETH,SOL,XRP}"
INTERVAL="${TRADINGAGENTS_INTERVAL:-15m}"
STREAM_MAX_AGE_SECONDS="${TRADINGAGENTS_STREAM_MAX_AGE_SECONDS:-180}"
ARM_FILE="${TRADINGAGENTS_DEMO_TRIAL_ARM_FILE:-$STATE_DIR/OKX_DEMO_TRIAL_ARMED}"
EMERGENCY_STOP_FILE="${TRADINGAGENTS_DEMO_EMERGENCY_STOP_FILE:-$STATE_DIR/OKX_DEMO_EMERGENCY_STOP}"
LOCK_FILE="$STATE_DIR/analysis-watchdog.lock"
LOG_DIR="$STATE_DIR/logs"
TRIAL_LOG="$LOG_DIR/okx-demo-controlled-trial.log"
ARM_PHRASE="ARM_OKX_DEMO_TRIAL"

mkdir -p "$LOG_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    printf 'Another QJ analysis or trial cycle currently holds %s.\n' "$LOCK_FILE"
    exit 1
fi

rearm_stop() {
    install -m 600 /dev/null "$EMERGENCY_STOP_FILE"
    rm -f "$ARM_FILE"
}
trap rearm_stop EXIT

if [[ ! -d "$PROJECT_ROOT" || ! -x "$PYTHON_BIN" || ! -r "$ENV_FILE" ]]; then
    printf 'Controlled trial cannot find its release, Python runtime, or demo environment.\n'
    exit 1
fi
if [[ ! -f "$EMERGENCY_STOP_FILE" ]]; then
    printf 'Controlled trial must start with the OKX demo emergency stop active.\n'
    exit 1
fi
if [[ ! -r "$ARM_FILE" ]] || [[ "$(tr -d '\r\n' <"$ARM_FILE")" != "$ARM_PHRASE" ]]; then
    printf 'Controlled trial is not armed. Write %s to %s with mode 600.\n' \
        "$ARM_PHRASE" "$ARM_FILE"
    exit 1
fi
rm -f "$ARM_FILE"

for variable in ${!TRADINGAGENTS_CRYPTO_@}; do
    unset "$variable"
done
unset OKX_API_KEY OKX_API_SECRET OKX_API_PASSPHRASE

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

export TRADINGAGENTS_CRYPTO_AI_ROUTER=hermes_cli
export TRADINGAGENTS_CRYPTO_HERMES_CLI_COMMAND=hermes
export TRADINGAGENTS_CRYPTO_HERMES_CLI_TIMEOUT_SECONDS=90
export TRADINGAGENTS_CRYPTO_EXCHANGE_PROVIDER=okx
export TRADINGAGENTS_CRYPTO_OKX_DEMO=true
export TRADINGAGENTS_CRYPTO_OKX_DEMO_EXECUTION_ENABLED=true
export TRADINGAGENTS_CRYPTO_OKX_MAX_LEVERAGE=1
export TRADINGAGENTS_CRYPTO_OKX_REQUIRE_NET_MODE=true
export TRADINGAGENTS_CRYPTO_OKX_REQUIRE_PROTECTIVE_ORDERS=true
export TRADINGAGENTS_CRYPTO_PROTECTIVE_OCO_ENABLED=true
export TRADINGAGENTS_CRYPTO_EXECUTION_MODE=testnet
export TRADINGAGENTS_CRYPTO_EMERGENCY_STOP_FILE="$EMERGENCY_STOP_FILE"
export TRADINGAGENTS_CRYPTO_STATE_DIR="$STATE_DIR"
export TRADINGAGENTS_CRYPTO_MAX_LOSS_PER_TRADE_USDT=0.50
export TRADINGAGENTS_CRYPTO_MAX_POSITION_PCT=0.005
export TRADINGAGENTS_CRYPTO_DAILY_LOSS_LIMIT_USDT=1.00
export TRADINGAGENTS_CRYPTO_MIN_ORDER_NOTIONAL_USDT=10.00
export TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_ENABLED=true
export TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_CLOSE_ON_STOP=true
export TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_CLOSE_ON_TAKE_PROFIT=true
export TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_MAX_HOLDING_MINUTES=1

account_counts() {
    "$PYTHON_BIN" - <<'PY'
from tradingagents.crypto import CryptoTradingConfig, OKXClient, PositionStore

config = CryptoTradingConfig.from_env()
client = OKXClient(config)
active = [
    row
    for row in client.get_positions()
    if abs(float(row.get("pos") or 0)) > 0
]
pending = client.get_pending_orders()
local = PositionStore.from_state_dir(config.state_dir).active_positions()
print(len(active), len(pending), len(local))
PY
}

cd "$PROJECT_ROOT"
read -r active_positions pending_orders local_positions < <(account_counts)
printf 'Preflight: exchange_positions=%s pending_orders=%s local_positions=%s\n' \
    "$active_positions" "$pending_orders" "$local_positions" | tee -a "$TRIAL_LOG"
if (( active_positions != 0 || pending_orders != 0 || local_positions != 0 )); then
    printf 'Controlled trial requires a flat exchange and local position state.\n' | tee -a "$TRIAL_LOG"
    exit 1
fi

rm -f "$EMERGENCY_STOP_FILE"
"$PYTHON_BIN" - <<'PY' | tee -a "$TRIAL_LOG"
from tradingagents.crypto import CryptoTradingConfig, OKXExecutionAdapter

report = OKXExecutionAdapter(CryptoTradingConfig.from_env()).readiness("BTC")
failed = [check.name for check in report.checks if check.status != "PASS"]
print(f"Machine readiness: ready={report.ready} failed={','.join(failed) or '-'}")
if not report.ready:
    raise SystemExit(1)
PY
"$PYTHON_BIN" -m cli.main crypto-okx-demo-readiness --symbol BTC | tee -a "$TRIAL_LOG"

timeout 240 "$PYTHON_BIN" -m cli.main crypto-autopilot \
    --symbols "$SYMBOLS" \
    --interval "$INTERVAL" \
    --mode testnet \
    --ai-review \
    --execute-top \
    --guard-positions \
    --auto-close \
    --cycles 1 \
    --interval-seconds 300 \
    --no-hotlist \
    --require-fresh-stream \
    --stream-max-age-seconds "$STREAM_MAX_AGE_SECONDS" \
    2>&1 | tee -a "$TRIAL_LOG"

# The one-time entry window ends after exactly one TradingAgents cycle.
rearm_stop
read -r active_positions pending_orders local_positions < <(account_counts)
printf 'After entry cycle: exchange_positions=%s pending_orders=%s local_positions=%s\n' \
    "$active_positions" "$pending_orders" "$local_positions" | tee -a "$TRIAL_LOG"
if (( active_positions == 0 && pending_orders == 0 && local_positions == 0 )); then
    printf 'TRIAL_NO_ENTRY: no AI-reviewed, risk-approved entry was filled.\n' | tee -a "$TRIAL_LOG"
    exit 0
fi
if (( pending_orders != 0 )); then
    printf 'TRIAL_INCOMPLETE: pending orders remain after the entry cycle; new entries are stopped.\n' \
        | tee -a "$TRIAL_LOG"
    exit 1
fi
if (( active_positions > 1 || local_positions > 1 )); then
    printf 'Controlled trial detected more than one position; new entries remain stopped.\n' | tee -a "$TRIAL_LOG"
fi

for attempt in {1..12}; do
    "$PYTHON_BIN" -m cli.main crypto-autopilot \
        --symbols "$SYMBOLS" \
        --interval "$INTERVAL" \
        --mode testnet \
        --guard-positions \
        --auto-close \
        --cycles 1 \
        --interval-seconds 20 \
        --no-hotlist \
        --require-fresh-stream \
        --stream-max-age-seconds "$STREAM_MAX_AGE_SECONDS" \
        2>&1 | tee -a "$TRIAL_LOG"
    read -r active_positions pending_orders local_positions < <(account_counts)
    printf 'Close check %s: exchange_positions=%s pending_orders=%s local_positions=%s\n' \
        "$attempt" "$active_positions" "$pending_orders" "$local_positions" | tee -a "$TRIAL_LOG"
    if (( active_positions == 0 && pending_orders == 0 && local_positions == 0 )); then
        printf 'TRIAL_PASS: controlled demo entry and reduce-only exit completed.\n' | tee -a "$TRIAL_LOG"
        exit 0
    fi
    sleep 20
done

printf 'TRIAL_INCOMPLETE: a demo position remains; emergency stop is active and attached TP/SL remains required.\n' \
    | tee -a "$TRIAL_LOG"
exit 1
