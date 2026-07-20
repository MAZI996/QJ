# OKX Trading Center

OKX is now the primary venue for this crypto TradingAgents extension.
Hyperliquid and Binance adapters remain available as legacy optional routes,
but new market-data and execution work should target OKX first.

## Safety Baseline

- Default provider: `okx`
- Default OKX mode: demo header enabled with `TRADINGAGENTS_CRYPTO_OKX_DEMO=true`
- Default execution mode: `analysis`
- Demo orders: disabled until the explicit demo-execution switch and readiness gate pass
- Live orders: hard blocked; the OKX adapter accepts demo `testnet` mode only
- Strategy path: scanner -> TradingAgents AI review -> risk manager -> execution router
- LLM/Hermes can review signals, but cannot bypass deterministic risk checks

## Environment

Use the safe template first:

```powershell
.\scripts\crypto_okx_env_template.ps1
```

Signed account checks and demo execution require OKX demo API credentials:

```powershell
$env:TRADINGAGENTS_CRYPTO_OKX_API_KEY = "..."
$env:TRADINGAGENTS_CRYPTO_OKX_API_SECRET = "..."
$env:TRADINGAGENTS_CRYPTO_OKX_API_PASSPHRASE = "..."
```

Do not paste secrets into chat or commit them to the repository.

Create a demo-only key with trading permission and without withdrawal
permission. Configure the OKX account in net position mode and set the selected
USDT perpetual contract leverage to 1 before enabling execution.

The default REST endpoint is the current official global API host,
`https://openapi.okx.com`. Override `TRADINGAGENTS_CRYPTO_OKX_BASE_URL` only
when the OKX account belongs to a region with a different official endpoint.

## Public Diagnostics

```powershell
.\.venv\Scripts\python.exe -m cli.main crypto-okx-check --symbol BTC --inst-type SWAP --demo
.\.venv\Scripts\python.exe -m cli.main crypto-okx-markets --inst-type SWAP --limit 20
```

The diagnostic command checks public time, ticker, order book, candles, and
instrument rules. Add `--include-balance` only after read-only OKX credentials
are configured.

## Demo Execution Readiness

The safe environment template leaves demo execution disabled. After demo
credentials and the OKX account settings are ready, enable only the demo switch:

```powershell
$env:TRADINGAGENTS_CRYPTO_OKX_DEMO_EXECUTION_ENABLED = "true"
$env:TRADINGAGENTS_CRYPTO_PROTECTIVE_OCO_ENABLED = "true"
.\.venv\Scripts\python.exe -m cli.main crypto-okx-demo-readiness --symbol BTC
```

If readiness reports `long_short_mode` or leverage above 1, first ensure the
demo account has no positions and no pending orders, then run the guarded
initializer:

```powershell
.\.venv\Scripts\python.exe -m cli.main crypto-okx-demo-prepare --symbol BTC --confirm PREPARE_OKX_DEMO
```

The initializer refuses real API mode, active emergency stops, accounts with
positions, accounts with pending orders, keys without trade permission, and
keys with withdrawal permission. It changes only the demo position mode and
the selected perpetual's leverage.

Readiness verifies the demo header, explicit execution switch, trade permission,
absence of withdrawal permission, linear USDT perpetual metadata, net position
mode, actual account leverage, and exchange-side protective-order
configuration. It also requires a configured, inactive emergency-stop file. It
does not place an order.

When readiness is green, a guarded unattended demo loop is available through
the existing TradingAgents chain:

```powershell
.\.venv\Scripts\python.exe -m cli.main crypto-autopilot --symbols BTC,ETH,SOL,XRP --mode testnet --execute-top --auto-close --cycles 12 --interval-seconds 300
```

BUY intents are converted from base-coin quantity to OKX contract size and are
submitted with attached mark-price TP/SL orders. SELL intents must be
reduce-only; the adapter reads the actual positive net long position and caps
the close size before submission. Local positions are updated only from a
confirmed OKX fill. If a transport timeout makes an acknowledged order
ambiguous, the adapter recovers it by client order ID; an unresolved order
activates the emergency-stop file so another entry cannot be submitted.

## Analysis Scan

```powershell
.\.venv\Scripts\python.exe -m cli.main crypto-market-quality --provider okx --mainnet --symbols BTC,ETH,SOL,XRP
.\.venv\Scripts\python.exe -m cli.main crypto-scan --symbols BTC,ETH,SOL,XRP --mode analysis
```

The OKX market-quality gate is enabled by default. It checks spread, correctly
converted contract depth, order-book imbalance, the latest realized funding
rate, and current USD open interest before risk sizing. A rejected BUY is
demoted to HOLD before it reaches the risk manager.

## Real-Time Stream Archive

Start a short public WebSocket archive run:

```powershell
.\.venv\Scripts\python.exe -m cli.main crypto-okx-stream --symbols BTC,ETH,SOL,XRP --interval 15m --seconds 60 --demo
```

Check freshness:

```powershell
.\.venv\Scripts\python.exe -m cli.main crypto-okx-stream-status --symbols BTC,ETH,SOL,XRP --interval 15m --max-age-seconds 600
```

The OKX freshness gate currently requires these per-symbol channels:

- `tickers`
- `books`
- `candle15m` or the configured candle interval

The stream also subscribes to `trades`, but trade messages are event-triggered
and not required for freshness because quiet/demo markets may not emit them
within every short check window.

Autopilot now uses provider-aware stream evidence. With the default OKX provider,
`crypto-autopilot --require-fresh-stream` expects fresh `okx-ws-*.jsonl` archive
events before entries are allowed.

## Current Boundary

This layer can analyze OKX market data, enforce market-quality and deterministic
risk gates, archive public WebSocket events, submit guarded demo entries, and
send reduce-only demo exits. Real OKX orders remain unavailable even when the
global live switch and confirmation phrase are supplied.

The next promotion gate is operational evidence: configure demo credentials,
pass readiness, run a minimum-size demo entry and protected exit, verify order
recovery, then collect sustained paper/demo evidence before designing a
separate live-readiness path.
