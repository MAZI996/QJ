# OKX Trading Center

OKX is now the primary venue for this crypto TradingAgents extension.
Hyperliquid and Binance adapters remain available as legacy optional routes,
but new market-data and execution work should target OKX first.

## Safety Baseline

- Default provider: `okx`
- Default OKX mode: demo header enabled with `TRADINGAGENTS_CRYPTO_OKX_DEMO=true`
- Default execution mode: `analysis`
- Live orders: disabled until a dedicated OKX execution adapter and readiness gate exist
- Strategy path: scanner -> TradingAgents AI review -> risk manager -> execution router
- LLM/Hermes can review signals, but cannot bypass deterministic risk checks

## Environment

Use the safe template first:

```powershell
.\scripts\crypto_okx_env_template.ps1
```

Optional read-only account checks require OKX API credentials:

```powershell
$env:TRADINGAGENTS_CRYPTO_OKX_API_KEY = "..."
$env:TRADINGAGENTS_CRYPTO_OKX_API_SECRET = "..."
$env:TRADINGAGENTS_CRYPTO_OKX_API_PASSPHRASE = "..."
```

Do not paste secrets into chat or commit them to the repository.

## Public Diagnostics

```powershell
.\.venv\Scripts\python.exe -m cli.main crypto-okx-check --symbol BTC --inst-type SWAP --demo
.\.venv\Scripts\python.exe -m cli.main crypto-okx-markets --inst-type SWAP --limit 20
```

The diagnostic command checks public time, ticker, order book, candles, and
instrument rules. Add `--include-balance` only after read-only OKX credentials
are configured.

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

This layer can analyze OKX market data, enforce the OKX market-quality gate, and
archive live public WebSocket events.
It does not yet submit OKX orders or auto-close OKX positions. The next safe
steps are:

1. Add OKX demo order adapter for entry and reduce-only exits.
2. Add OKX live-readiness checks for account mode, permissions, leverage,
   position mode, protective orders, emergency stop, and paper evidence.
3. Promote from analysis/paper to demo execution only after diagnostics pass.
