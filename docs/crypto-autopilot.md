# Crypto Autopilot

`crypto-autopilot` is the unattended loop for the Hyperliquid crypto extension. It
repeatedly runs the TradingAgents crypto workflow, writes the decision journal,
and optionally executes only the top risk-approved signal in the selected mode.

The default is intentionally safe:

```powershell
python -m cli.main crypto-autopilot --symbols BTC,ETH,SOL,HYPE --mode analysis
```

Defaults:

- `--cycles 1`: one cycle only.
- `--interval-seconds 300`: five minutes between cycles when multiple cycles run.
- `--mode analysis`: no order submission.
- `--execute-top false`: no execution unless explicitly requested.
- `--fusion true`: high-star strategy fusion is enabled.
- Reports are written to `TRADINGAGENTS_CRYPTO_STATE_DIR`.

Service-style loop:

```powershell
python -m cli.main crypto-autopilot --symbols BTC,ETH,SOL,HYPE --mode paper --execute-top --cycles 0 --interval-seconds 300
```

Testnet execution:

```powershell
python -m cli.main crypto-autopilot --symbols BTC,ETH --mode testnet --execute-top --cycles 12 --interval-seconds 300
```

Live execution requires all live guards:

```powershell
python -m cli.main crypto-autopilot --symbols BTC,ETH --mode live --execute-top --allow-live --live-confirm I_UNDERSTAND_THIS_PLACES_REAL_HYPERLIQUID_ORDERS
```

Live mode still will not submit orders unless:

- `TRADINGAGENTS_CRYPTO_HYPERLIQUID_TESTNET=false`
- `TRADINGAGENTS_CRYPTO_HYPERLIQUID_MAX_LEVERAGE=1`
- `TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS=true`
- `--allow-live` is present
- `--live-confirm` matches `TRADINGAGENTS_CRYPTO_LIVE_CONFIRM_PHRASE`
- `RiskManager` approves the order intent
- no emergency stop file exists
- the official Hyperliquid signing adapter is installed and enabled

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
