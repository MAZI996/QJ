#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="${TRADINGAGENTS_PROJECT_ROOT:-/opt/data/projects/QJ-current}"
PYTHON_BIN="${TRADINGAGENTS_PYTHON_BIN:-/opt/data/projects/QJ/.venv/bin/python}"
STATE_DIR="${TRADINGAGENTS_STATE_DIR:-/opt/data/.tradingagents/crypto}"
SYMBOLS="${TRADINGAGENTS_SYMBOLS:-BTC,ETH,SOL,XRP}"
INTERVAL="${TRADINGAGENTS_INTERVAL:-15m}"
STREAM_MAX_AGE_SECONDS="${TRADINGAGENTS_STREAM_MAX_AGE_SECONDS:-180}"
STREAM_WARMUP_SECONDS="${TRADINGAGENTS_STREAM_WARMUP_SECONDS:-15}"
DEMO_EMERGENCY_STOP_FILE="${TRADINGAGENTS_DEMO_EMERGENCY_STOP_FILE:-$STATE_DIR/OKX_DEMO_EMERGENCY_STOP}"

LOG_DIR="$STATE_DIR/logs"
STREAM_LOG="$LOG_DIR/okx-stream-watchdog.log"
ANALYSIS_LOG="$LOG_DIR/autopilot-analysis-watchdog.log"
LOCK_FILE="$STATE_DIR/analysis-watchdog.lock"

mkdir -p "$LOG_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    exit 0
fi

# Keep credential-bearing demo executors blocked while this deployment is in
# analysis-only mode. Do not export this path to the analysis process itself.
install -m 600 /dev/null "$DEMO_EMERGENCY_STOP_FILE"

if [[ ! -d "$PROJECT_ROOT" || ! -x "$PYTHON_BIN" ]]; then
    printf 'QJ analysis watchdog cannot find its release or Python runtime.\n'
    exit 1
fi

# Cron may inherit credentials from the Hermes container. This job only needs
# public market data, so remove every trading variable before setting its
# explicit analysis-only policy.
for variable in ${!TRADINGAGENTS_CRYPTO_@}; do
    unset "$variable"
done
unset OKX_API_KEY OKX_API_SECRET OKX_API_PASSPHRASE

export TRADINGAGENTS_CRYPTO_AI_ROUTER=hermes_cli
export TRADINGAGENTS_CRYPTO_HERMES_CLI_COMMAND=hermes
export TRADINGAGENTS_CRYPTO_HERMES_CLI_TIMEOUT_SECONDS=90
export TRADINGAGENTS_CRYPTO_EXCHANGE_PROVIDER=okx
export TRADINGAGENTS_CRYPTO_OKX_DEMO=true
export TRADINGAGENTS_CRYPTO_OKX_DEMO_EXECUTION_ENABLED=false
export TRADINGAGENTS_CRYPTO_OKX_MAX_LEVERAGE=1
export TRADINGAGENTS_CRYPTO_EXECUTION_MODE=analysis
export TRADINGAGENTS_CRYPTO_STATE_DIR="$STATE_DIR"

stream_pattern="crypto-okx-stream --symbols $SYMBOLS"
if ! pgrep -f "$stream_pattern" >/dev/null 2>&1; then
    (
        cd "$PROJECT_ROOT"
        nohup "$PYTHON_BIN" -m cli.main crypto-okx-stream \
            --symbols "$SYMBOLS" \
            --seconds 0 \
            --interval "$INTERVAL" \
            --demo \
            >>"$STREAM_LOG" 2>&1 </dev/null &
    )
    sleep "$STREAM_WARMUP_SECONDS"
fi

cd "$PROJECT_ROOT"
if ! "$PYTHON_BIN" -m cli.main crypto-autopilot \
    --symbols "$SYMBOLS" \
    --mode analysis \
    --ai-review \
    --cycles 1 \
    --interval-seconds 300 \
    --no-guard-positions \
    --require-fresh-stream \
    --stream-max-age-seconds "$STREAM_MAX_AGE_SECONDS" \
    >>"$ANALYSIS_LOG" 2>&1; then
    printf 'QJ analysis watchdog failed; inspect %s\n' "$ANALYSIS_LOG"
    exit 1
fi
