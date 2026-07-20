# Crypto Autopilot

`crypto-autopilot` is the unattended loop for the crypto extension. It
repeatedly runs the TradingAgents crypto workflow, writes the decision journal,
and optionally executes only the Hermes-selected, risk-approved signal.

2026-07-20 update: OKX is the default provider. The fresh-stream gate is now
provider-aware: OKX expects `okx-ws-*.jsonl`, while the legacy Hyperliquid route
still expects `hyperliquid-ws-*.jsonl`.

The default is intentionally safe:

```powershell
python -m cli.main crypto-autopilot --symbols BTC,ETH,SOL,XRP --mode analysis --no-require-fresh-stream
```

Defaults:

- `--cycles 1`: one cycle only.
- `--interval-seconds 300`: five minutes between cycles when multiple cycles run.
- `--mode analysis`: no order submission.
- `--execute-top false`: no execution unless explicitly requested.
- `--ai-review false`: optional for analysis, but `--execute-top` is rejected
  unless `--ai-review` is also present.
- `--auto-close false`: position exits are inspected but reduce-only close
  orders are not submitted unless explicitly requested.
- `--require-fresh-stream true`: new scans and entries stop when the configured
  exchange WebSocket archive is missing or stale.
- `--fusion true`: high-star strategy fusion is enabled.
- Reports are written to `TRADINGAGENTS_CRYPTO_STATE_DIR`.

For execution, Hermes must return `BUY`, an exact candidate symbol, and
confidence at or above `TRADINGAGENTS_CRYPTO_AI_EXECUTION_MIN_CONFIDENCE`
(default `0.62`). The selected candidate is then evaluated by `RiskManager`;
only both approvals can reach `ExecutionRouter`.

Hostinger Hermes Agent installations that expose the dashboard but not an
OpenAI-compatible API should set
`TRADINGAGENTS_CRYPTO_AI_ROUTER=hermes_cli`. This uses the local `hermes chat`
single-query command without enabling toolsets. Keep `--execute-top` disabled
until at least one analysis cycle records a successful AI review.

For a Hostinger Hermes container, install the version-controlled analysis-only
watchdog into Hermes and schedule it without an agent:

```bash
install -m 700 scripts/hermes/okx_analysis_watchdog.sh \
  /opt/data/scripts/okx_analysis_watchdog.sh
hermes cron create "every 5m" \
  --name "QJ OKX analysis-only" \
  --deliver local \
  --script okx_analysis_watchdog.sh \
  --no-agent \
  --workdir /opt/data/projects/QJ-current
```

The watchdog removes inherited OKX credentials, forces analysis mode, keeps
the demo execution switch off, enforces the 1x leverage policy, prevents
overlapping cycles, and restarts the public OKX stream when it is absent.
Successful ticks are silent; reports and logs are written beneath
`TRADINGAGENTS_CRYPTO_STATE_DIR`.

Hostinger's current Hermes image resolves cron script names beneath
`/opt/data/scripts`, even though some CLI help text describes the directory as
`~/.hermes/scripts`.

Start the real-time stream first for unattended operation:

```powershell
python -m cli.main crypto-okx-stream --symbols BTC,ETH,SOL,XRP --seconds 0 --demo
```

Check local stream freshness:

```powershell
python -m cli.main crypto-okx-stream-status --symbols BTC,ETH,SOL,XRP --max-age-seconds 600
```

Service-style loop:

```powershell
python -m cli.main crypto-autopilot --symbols BTC,ETH,SOL,XRP --mode paper --ai-review --execute-top --cycles 0 --interval-seconds 300
```

Paper loop with automatic reduce-only exits for locally tracked positions:

```powershell
python -m cli.main crypto-autopilot --symbols BTC,ETH,SOL,XRP --mode paper --ai-review --execute-top --auto-close --cycles 0 --interval-seconds 300
```

OKX guarded demo execution is available after the explicit execution switch,
demo credentials, net position mode, 1x leverage, and protective-order checks
all pass:

```powershell
.\scripts\crypto_okx_env_template.ps1
$env:TRADINGAGENTS_CRYPTO_OKX_API_KEY="..."
$env:TRADINGAGENTS_CRYPTO_OKX_API_SECRET="..."
$env:TRADINGAGENTS_CRYPTO_OKX_API_PASSPHRASE="..."
$env:TRADINGAGENTS_CRYPTO_OKX_DEMO_EXECUTION_ENABLED="true"
python -m cli.main crypto-okx-demo-readiness --symbol BTC
python -m cli.main crypto-autopilot --symbols BTC,ETH,SOL,XRP --mode testnet --ai-review --execute-top --auto-close --cycles 12 --interval-seconds 300
```

OKX BUY orders include exchange-side attached mark-price TP/SL protection.
Automatic SELL exits are reduce-only and capped to the actual positive net long
position returned by OKX. Real OKX execution remains hard blocked.

The commands below are for the legacy Hyperliquid route and require an explicit
provider switch.

Hyperliquid testnet execution:

```powershell
$env:TRADINGAGENTS_CRYPTO_EXCHANGE_PROVIDER="hyperliquid"
python -m cli.main crypto-autopilot --symbols BTC,ETH --mode testnet --ai-review --execute-top --auto-close --cycles 12 --interval-seconds 300
```

Hyperliquid live execution requires all live guards:

```powershell
$env:TRADINGAGENTS_CRYPTO_EXCHANGE_PROVIDER="hyperliquid"
python -m cli.main crypto-autopilot --symbols BTC,ETH --mode live --ai-review --execute-top --auto-close --allow-live --live-confirm I_UNDERSTAND_THIS_PLACES_REAL_HYPERLIQUID_ORDERS
```

Live mode still will not submit orders unless:

- `TRADINGAGENTS_CRYPTO_HYPERLIQUID_TESTNET=false`
- `TRADINGAGENTS_CRYPTO_HYPERLIQUID_MAX_LEVERAGE=1`
- `TRADINGAGENTS_CRYPTO_HYPERLIQUID_SDK_EXECUTION_ENABLED=true`
- `TRADINGAGENTS_CRYPTO_PROTECTIVE_OCO_ENABLED=true`
- `TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS=true`
- `--allow-live` is present
- `--live-confirm` matches `TRADINGAGENTS_CRYPTO_LIVE_CONFIRM_PHRASE`
- fresh Hyperliquid WebSocket archive evidence exists
- `RiskManager` approves the order intent
- automatic exits are sent only as reduce-only SELL closes from the position
  guardian; non-reduce-only SELL remains blocked
- no emergency stop file exists
- the official Hyperliquid SDK is installed and signer config passes diagnostics

Position guardian defaults:

```powershell
$env:TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_ENABLED="true"
$env:TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_CLOSE_ON_STOP="true"
$env:TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_CLOSE_ON_TAKE_PROFIT="true"
$env:TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_MAX_HOLDING_MINUTES="0"
$env:TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_STRATEGY_EXIT_ENABLED="false"
$env:TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_SKIP_ENTRIES_AFTER_CLOSE="true"
```

The guardian checks tracked long positions before each new scan. It can close
when a stop-loss or take-profit is touched, when an optional max holding time is
exceeded, or when the optional strategy-exit gate is enabled and the current
scanner no longer supports the long. A close signal is not submitted unless the
CLI includes `--auto-close`.

For a 100 USDT starting account, set risk controls before any live run:

```powershell
$env:TRADINGAGENTS_CRYPTO_ACCOUNT_EQUITY_USDT="100"
$env:TRADINGAGENTS_CRYPTO_RISK_PER_TRADE_PCT="0.005"
$env:TRADINGAGENTS_CRYPTO_MAX_LOSS_PER_TRADE_USDT="0.50"
$env:TRADINGAGENTS_CRYPTO_MAX_POSITION_PCT="0.10"
$env:TRADINGAGENTS_CRYPTO_EMERGENCY_STOP_FILE="C:\tradingagents-stop.txt"
```

The emergency stop file is a simple kill switch. If the file exists, autopilot
stops before the next cycle and the execution router refuses orders.

Important boundary: this runner enables automation, not guaranteed profit. The
3000x target from 100 USDT to 300,000 USDT would require extreme compounding and
can also end in total loss. The code must earn permission through paper trading,
testnet, journaling, and drawdown controls before live capital is used.
