# Crypto Live Readiness: Items 2-8

This document tracks the live-automation layers added after the initial scanner,
workflow, strategy fusion, and autopilot shell.

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

Implemented in `tradingagents/crypto/protective_orders.py` and wired into
`ExecutionRouter`.

- Protective sell plans use Binance Spot OCO-style params.
- Live OCO submission is gated behind
  `TRADINGAGENTS_CRYPTO_PROTECTIVE_OCO_ENABLED=true`.
- The default remains disabled until live order behavior is verified.

```powershell
python -m cli.main crypto-protective-plan
```

## 4. Order Recovery And User-Data Events

Implemented in `tradingagents/crypto/order_recovery.py`.

- Recovery reads `openOrders` and recent `myTrades` for selected symbols.
- Trade fills update local positions.
- User-data `executionReport` payloads can be normalized into local position
  state. A long-running websocket process is still the next deployment step.

```powershell
python -m cli.main crypto-recover-orders --symbols BTCUSDT,ETHUSDT
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

Drop UTF-8 `.txt` files from X, Binance Square, forums, Telegram exports, or
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
$env:TRADINGAGENTS_CRYPTO_PROTECTIVE_OCO_ENABLED="false"
$env:TRADINGAGENTS_CRYPTO_EMERGENCY_STOP_FILE="C:\tradingagents-stop.txt"
```

Do not enable live OCO or live market orders until `crypto-account`,
`crypto-recover-orders`, paper trading, and testnet behavior are verified on the
actual Binance account.
