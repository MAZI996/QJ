# Crypto Live Readiness: Items 2-8

This document tracks the live-automation layers added after the initial scanner,
workflow, strategy fusion, and autopilot shell.

## 1. Readiness Gate

Implemented in `tradingagents/crypto/live_readiness.py`.

This command is read-only. It does not place orders and does not print private
key values. Use it before moving from paper to testnet or from testnet to live:

```powershell
python -m cli.main crypto-live-readiness --target paper
python -m cli.main crypto-live-readiness --target testnet --network --symbol BTC
python -m cli.main crypto-live-readiness --target live --mainnet --wallet-address 0xYourWallet --network --symbol BTC
```

The live target fails unless the local gates are aligned: Hyperliquid mainnet,
SDK execution enabled, live switch enabled, API wallet configured, leverage at
1, protective orders enabled, emergency stop configured, circuit breaker clear,
paper-order evidence present, and the position guardian enabled.

## 2. Position Management

Implemented in `tradingagents/crypto/positions.py`.

- Paper and live accepted BUY orders update `positions.json`.
- Average entry, quantity, stop, take-profit, realized PnL, and last order id
  are persisted under `TRADINGAGENTS_CRYPTO_STATE_DIR`.
- User-data execution events can be normalized into the same store.

```powershell
python -m cli.main crypto-positions
```

## 3. Stop/Take-Profit Protection

Implemented in `tradingagents/crypto/protective_orders.py`,
`tradingagents/crypto/hyperliquid_execution.py`,
`tradingagents/crypto/position_guardian.py`, and wired into `ExecutionRouter`
and `crypto-autopilot`.

- Current protective sell plans are legacy Binance Spot OCO-style params.
- Hyperliquid execution can submit grouped entry + reduce-only TP/SL trigger
  orders through the official SDK when protective orders are enabled.
- The position guardian checks locally tracked long positions before every
  autopilot scan and can submit reduce-only SELL closes when `--auto-close` is
  present.
- Non-reduce-only Hyperliquid SELL remains blocked; this is automatic
  long-position closing, not short selling.
- Legacy live OCO submission is gated behind
  `TRADINGAGENTS_CRYPTO_PROTECTIVE_OCO_ENABLED=true`.
- SDK execution remains disabled until server keys, paper behavior, and testnet
  behavior are verified.

```powershell
python -m cli.main crypto-protective-plan
```

## 4. Order Recovery And User-Data Events

Implemented in `tradingagents/crypto/order_recovery.py`.

- Current recovery reads legacy Binance `openOrders` and recent `myTrades` for
  selected symbols.
- Hyperliquid recovery reads clearinghouse account state and open orders, then
  mirrors long-only exchange positions into the local position store.
- Hyperliquid short positions are reported but not synced into local state during
  the initial long-only validation phase.
- Trade fills update local positions.
- User-data `executionReport` payloads can be normalized into local position
  state. A long-running websocket process is still the next deployment step.

```powershell
python -m cli.main crypto-recover-orders --mainnet --wallet-address 0xYourWallet --symbols BTC,ETH
```

## 5. Daily Loss Circuit Breaker

Implemented in `tradingagents/crypto/circuit_breaker.py` and wired into
`crypto-autopilot`.

- Autopilot stops before a cycle when the emergency stop file exists.
- Autopilot also stops when same-day realized loss reaches the configured
  limit.

```powershell
$env:TRADINGAGENTS_CRYPTO_DAILY_LOSS_LIMIT_USDT="3"
```

## 6. Performance Summary

Implemented in `tradingagents/crypto/performance.py`.

```powershell
python -m cli.main crypto-performance
```

The summary currently reads local position state. Deeper backtest analytics and
historical candle replay are still separate future work.

## 7. Hermes Router Hardening

Implemented in `tradingagents/crypto/llm_router.py`.

- Hermes now has a `healthcheck()` path against an OpenAI-compatible `/models`
  endpoint.
- The configured model is verified when Hermes returns model ids.

```powershell
$env:TRADINGAGENTS_CRYPTO_AI_ROUTER="hermes"
$env:TRADINGAGENTS_CRYPTO_AI_MODEL="your-model"
$env:TRADINGAGENTS_CRYPTO_HERMES_BASE_URL="http://127.0.0.1:8000/v1"
python -m cli.main crypto-hermes-check
```

## 8. Automatic Attention Harvesting

Implemented in `tradingagents/crypto/attention_harvester.py`.

Drop UTF-8 `.txt` files from X, Hyperliquid ecosystem posts, forums, Telegram exports, or
news scrapers into `TRADINGAGENTS_CRYPTO_ATTENTION_SOURCE_DIR`; then harvest:

```powershell
python -m cli.main crypto-attention-harvest
```

The harvester writes discovered symbols into the same hotlist used by the
scanner and Lana-inspired strategy.

## 100 USDT Live Guardrail Preset

```powershell
$env:TRADINGAGENTS_CRYPTO_ACCOUNT_EQUITY_USDT="100"
$env:TRADINGAGENTS_CRYPTO_RISK_PER_TRADE_PCT="0.005"
$env:TRADINGAGENTS_CRYPTO_MAX_LOSS_PER_TRADE_USDT="0.50"
$env:TRADINGAGENTS_CRYPTO_DAILY_LOSS_LIMIT_USDT="3"
$env:TRADINGAGENTS_CRYPTO_MAX_POSITION_PCT="0.10"
$env:TRADINGAGENTS_CRYPTO_EXCHANGE_PROVIDER="hyperliquid"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_MAX_LEVERAGE="1"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_SDK_EXECUTION_ENABLED="false"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_REQUIRE_PROTECTIVE_ORDERS="true"
$env:TRADINGAGENTS_CRYPTO_PROTECTIVE_OCO_ENABLED="false"
$env:TRADINGAGENTS_CRYPTO_EMERGENCY_STOP_FILE="C:\tradingagents-stop.txt"
$env:TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_ENABLED="true"
$env:TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_CLOSE_ON_STOP="true"
$env:TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_CLOSE_ON_TAKE_PROFIT="true"
$env:TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_SKIP_ENTRIES_AFTER_CLOSE="true"
```

Do not enable live Hyperliquid market orders until `crypto-account`,
Hyperliquid recovery, paper trading, and testnet behavior are verified on the
actual account.
