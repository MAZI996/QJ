# Direct Hyperliquid Integration

This project does not need Longxia/Lobster as the trading data source. The
preferred integration path is direct Hyperliquid public data plus the official
SDK execution adapter.

## What Is Already Connected

- Public market data: `meta`, `allMids`, `l2Book`, `metaAndAssetCtxs`, and
  `candleSnapshot`.
- Account state: clearinghouse state, balances, open orders, and long-position
  recovery.
- Execution: official `hyperliquid-python-sdk` signed `/exchange` flow.
- Safety: analysis/paper defaults, 1x max leverage, long-only entries,
  reduce-only exits, protective TP/SL, emergency stop, daily loss circuit
  breaker, and position guardian exits.

## Local SDK Install

The dependency is declared in `pyproject.toml`.

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

If only the SDK is missing:

```powershell
.\.venv\Scripts\python.exe -m pip install hyperliquid-python-sdk
```

## Safe Environment Template

Load non-secret defaults into the current PowerShell session:

```powershell
.\scripts\crypto_hyperliquid_env_template.ps1
```

For live-mode defaults, still without printing secrets:

```powershell
.\scripts\crypto_hyperliquid_env_template.ps1 -Live
```

Then set wallet values only on the trading machine:

```powershell
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_WALLET_ADDRESS="0xYourMainWallet"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_API_WALLET_ADDRESS="0xYourApiWallet"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_PRIVATE_KEY="0xYourApiWalletPrivateKey"
```

Never paste private keys into chat or docs.

## Read-Only Checks

```powershell
.\.venv\Scripts\python.exe -m cli.main crypto-hyperliquid-check --mainnet --symbol BTC
.\.venv\Scripts\python.exe -m cli.main crypto-market-quality --provider hyperliquid --mainnet --symbols BTC,ETH,SOL,HYPE
.\.venv\Scripts\python.exe -m cli.main crypto-live-readiness --target paper
```

## Paper Loop

```powershell
.\.venv\Scripts\python.exe -m cli.main crypto-autopilot --symbols BTC,ETH,SOL,HYPE --mode paper --ai-review --execute-top --auto-close --cycles 0 --interval-seconds 300
```

## Testnet And Live Boundary

Testnet execution requires the SDK, wallet, API wallet/private key, and
`TRADINGAGENTS_CRYPTO_HYPERLIQUID_TESTNET=true`.

Live execution additionally requires:

- `TRADINGAGENTS_CRYPTO_HYPERLIQUID_TESTNET=false`
- `TRADINGAGENTS_CRYPTO_HYPERLIQUID_SDK_EXECUTION_ENABLED=true`
- `TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS=true`
- `TRADINGAGENTS_CRYPTO_PROTECTIVE_OCO_ENABLED=true`
- emergency stop file configured
- paper-order evidence present
- CLI `--allow-live`
- CLI `--live-confirm I_UNDERSTAND_THIS_PLACES_REAL_HYPERLIQUID_ORDERS`

The execution adapter still blocks short selling. SELL is allowed only when the
order intent is reduce-only and closes an existing long position.
