# Hyperliquid Trading Center

Hyperliquid is now the default target venue for this crypto automation project.
The old Binance adapter remains in the repository as a legacy optional adapter,
but new trading-center work should use Hyperliquid first.

## Safe Configuration

Do not paste private keys into chat. Put them only on the server that runs the
trading process.

```powershell
$env:TRADINGAGENTS_CRYPTO_EXCHANGE_PROVIDER="hyperliquid"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_TESTNET="true"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_WALLET_ADDRESS="0xYourMainWallet"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_API_WALLET_ADDRESS="0xYourApiWallet"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_MAX_LEVERAGE="1"
$env:TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS="false"
$env:TRADINGAGENTS_CRYPTO_LIVE_CONFIRM_PHRASE="I_UNDERSTAND_THIS_PLACES_REAL_HYPERLIQUID_ORDERS"
```

Mainnet public diagnostics:

```powershell
python -m cli.main crypto-hyperliquid-check --mainnet --symbol BTC --wallet-address 0xYourWallet
python -m cli.main crypto-hyperliquid-markets --mainnet --limit 20
python -m cli.main crypto-market-quality --mainnet --symbols BTC,ETH,SOL,HYPE
python -m cli.main crypto-hyperliquid-account --mainnet --wallet-address 0xYourWallet
```

TradingAgents workflow scan:

```powershell
python -m cli.main crypto-workflow --symbols BTC,ETH,SOL,HYPE --mode analysis
python -m cli.main crypto-autopilot --symbols BTC,ETH,SOL,HYPE --mode paper --execute-top --cycles 0 --interval-seconds 300
```

The scan path now includes the Hyperliquid market-quality gate by default:
spread, top-book depth, order-book imbalance, and funding are checked before
risk sizing. See `docs/crypto-market-quality.md`.

## Live Signing Boundary

Hyperliquid order placement uses the official signed `/exchange` flow. We do
not hand-roll live signatures in this repository. Live trading should use the
official `hyperliquid-python-sdk` adapter once keys and paper/testnet behavior
are verified:

```bash
pip install hyperliquid-python-sdk
```

Until that adapter is enabled, the execution router blocks Hyperliquid
`testnet/live` execution and allows only `analysis` and `paper` modes.

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
