# Hyperliquid Trading Center

Hyperliquid is now the default target venue for this crypto automation project.
The old Binance adapter remains in the repository as a legacy optional adapter,
but new trading-center work should use Hyperliquid first.

For the no-Longxia direct integration runbook, see
`docs/crypto-direct-hyperliquid-integration.md`.

For the seven-source GitHub absorption map, see
`docs/crypto-github-absorption.md`.

For the Hyperliquid WebSocket archive runbook, see
`docs/crypto-hyperliquid-realtime-stream.md`.

## Safe Configuration

Do not paste private keys into chat. Put them only on the server that runs the
trading process.

```powershell
$env:TRADINGAGENTS_CRYPTO_EXCHANGE_PROVIDER="hyperliquid"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_TESTNET="true"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_WALLET_ADDRESS="0xYourMainWallet"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_API_WALLET_ADDRESS="0xYourApiWallet"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_MAX_LEVERAGE="1"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_SDK_EXECUTION_ENABLED="false"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_MARKET_SLIPPAGE="0.01"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_REQUIRE_PROTECTIVE_ORDERS="true"
$env:TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS="false"
$env:TRADINGAGENTS_CRYPTO_LIVE_CONFIRM_PHRASE="I_UNDERSTAND_THIS_PLACES_REAL_HYPERLIQUID_ORDERS"
```

Mainnet public diagnostics:

```powershell
python -m cli.main crypto-hyperliquid-check --mainnet --symbol BTC --wallet-address 0xYourWallet
python -m cli.main crypto-hyperliquid-markets --mainnet --limit 20
python -m cli.main crypto-market-quality --provider hyperliquid --mainnet --symbols BTC,ETH,SOL,HYPE
python -m cli.main crypto-hyperliquid-account --mainnet --wallet-address 0xYourWallet
python -m cli.main crypto-recover-orders --mainnet --wallet-address 0xYourWallet --symbols BTC,ETH,SOL,HYPE
python -m cli.main crypto-live-readiness --target paper
```

TradingAgents workflow scan:

```powershell
python -m cli.main crypto-workflow --symbols BTC,ETH,SOL,HYPE --mode analysis
python -m cli.main crypto-backtest --mainnet --symbols BTC,ETH,SOL,HYPE --interval 15m --bars 500
python -m cli.main crypto-backtest-sweep --mainnet --symbols BTC,ETH,SOL,HYPE --intervals 5m,15m,1h --lookbacks 60,120 --max-holding-bars 16,32,48 --bars 500
python -m cli.main crypto-backtest-sweep --mainnet --symbols BTC,ETH,SOL,HYPE --intervals 5m,15m,1h --lookbacks 60,120 --max-holding-bars 16,32,48 --bars 800 --min-trades 5 --min-win-rate 0.40 --min-return-pct 0 --max-drawdown-pct 5 --max-consecutive-losses 3 --candidates-only
python -m cli.main crypto-paper-queue --mainnet --symbols BTC,ETH,SOL,HYPE --intervals 5m,15m,1h --lookbacks 60,120 --max-holding-bars 16,32,48 --bars 800 --min-trades 5 --min-win-rate 0.40 --min-return-pct 0 --max-drawdown-pct 5 --max-consecutive-losses 3
python -m cli.main crypto-autopilot --symbols BTC,ETH,SOL,HYPE --mode paper --ai-review --execute-top --auto-close --cycles 0 --interval-seconds 300
```

The scan path now includes the Hyperliquid market-quality gate by default:
spread, top-book depth, order-book imbalance, and funding are checked before
risk sizing. See `docs/crypto-market-quality.md`.

The scanner also applies an entry-quality gate by default. It demotes fragile
BUY candidates with weak candle closes, noisy recent paths, or entries stretched
too far above the EMA anchor before AI review, risk sizing, paper mode, or any
execution route.

Historical replay is documented in `docs/crypto-backtest.md`; the paper queue is
documented in `docs/crypto-paper-queue.md`. Both use public candles only and
never submit orders.

## Official SDK Execution Boundary

Hyperliquid order placement uses the official signed `/exchange` flow. We do
not hand-roll live signatures in this repository. The SDK execution adapter is
present but disabled by default:

```bash
pip install hyperliquid-python-sdk
```

Server-only execution env:

```powershell
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_WALLET_ADDRESS="0xYourMainWallet"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_API_WALLET_ADDRESS="0xYourApiWallet"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_PRIVATE_KEY="0xApiWalletPrivateKey"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_SDK_EXECUTION_ENABLED="true"
```

Testnet execution still requires `--execute-top`:

```powershell
python -m cli.main crypto-scan --symbols BTC --mode testnet --ai-review --execute-top
```

Live execution requires all of these at the same time:

- `TRADINGAGENTS_CRYPTO_HYPERLIQUID_TESTNET=false`
- `TRADINGAGENTS_CRYPTO_HYPERLIQUID_SDK_EXECUTION_ENABLED=true`
- `TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS=true`
- `TRADINGAGENTS_CRYPTO_PROTECTIVE_OCO_ENABLED=true`
- `--mode live --execute-top`
- `--live-confirm I_UNDERSTAND_THIS_PLACES_REAL_HYPERLIQUID_ORDERS`

If protective orders are required, the adapter submits a grouped Hyperliquid
entry plus reduce-only take-profit and stop-loss triggers using the official
SDK `bulk_orders(..., grouping="normalTpsl")` path.

The position guardian can also submit active reduce-only SELL closes through
the same official SDK boundary when `crypto-autopilot` is run with
`--auto-close`. This close path is only for existing long positions; ordinary
SELL and short selling remain blocked.

## Server Layout

- Trading server keeps Hyperliquid wallet/private key environment variables.
- Hermes server provides model routing over HTTPS or private network.
- Hermes does not need Hyperliquid private keys.
- The trading server calls Hermes for review, then enforces local deterministic
  risk gates before any order adapter can submit.

## Safety Notes

Hyperliquid is primarily a perpetuals venue. Keep leverage at `1` while the
system is being validated, and do not enable live execution until position
tracking, journal recovery, daily loss circuit breaker, and paper performance
are all verified.
