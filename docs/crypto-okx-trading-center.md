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
.\.venv\Scripts\python.exe -m cli.main crypto-scan --symbols BTC,ETH,SOL,XRP --mode analysis --no-market-quality
```

The current market-quality gate is still Hyperliquid-specific, so use
`--no-market-quality` for early OKX scans until the OKX order-book/funding gate
is promoted.

## Current Boundary

This layer can analyze OKX market data. It does not yet submit OKX orders or
auto-close OKX positions. The next safe steps are:

1. Add OKX WebSocket stream archive and freshness gate.
2. Add OKX demo order adapter for entry and reduce-only exits.
3. Add OKX live-readiness checks for account mode, permissions, leverage,
   position mode, protective orders, emergency stop, and paper evidence.
4. Promote from analysis/paper to demo execution only after diagnostics pass.
