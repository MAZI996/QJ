# Crypto Market Quality Gate

This layer improves automated-trading selectivity by rejecting fragile market
conditions before risk sizing or execution.

For OKX perpetuals it uses public market data:

- `market/books` for best bid/ask, spread, top-book depth, and imbalance.
- `public/instruments` for `ctVal` and `ctType`, so contract sizes are converted
  to USD depth correctly.
- `public/funding-rate-history` for the latest realized funding rate.
- `public/open-interest` for current contract and USD open interest.

The legacy Hyperliquid route continues to use public `info` data:

- `l2Book` for best bid/ask, spread, top-book depth, and order-book imbalance.
- `metaAndAssetCtxs` for asset context such as funding and open interest.

## Why It Exists

Win rate alone is not the target. The safer target is positive expectancy:

```text
expectancy = win_rate * average_win - loss_rate * average_loss
```

The market-quality gate rejects trades where the strategy may be right but the
execution environment is bad:

- Spread is too wide.
- Bid or ask depth is too thin for clean fills.
- Top-book imbalance is extreme.
- Funding is too expensive or crowded.
- OKX USD open interest is below the configured liquidity floor.
- Required venue market-quality data cannot be loaded.

## Defaults

These defaults are intentionally conservative for a small personal account:

```powershell
$env:TRADINGAGENTS_CRYPTO_MARKET_QUALITY_ENABLED="true"
$env:TRADINGAGENTS_CRYPTO_MARKET_QUALITY_MAX_SPREAD_BPS="8"
$env:TRADINGAGENTS_CRYPTO_MARKET_QUALITY_MIN_DEPTH_USDC="10000"
$env:TRADINGAGENTS_CRYPTO_MARKET_QUALITY_DEPTH_LEVELS="5"
$env:TRADINGAGENTS_CRYPTO_MARKET_QUALITY_MAX_ABS_IMBALANCE="0.80"
$env:TRADINGAGENTS_CRYPTO_MARKET_QUALITY_MAX_ABS_FUNDING_RATE="0.001"
$env:TRADINGAGENTS_CRYPTO_MARKET_QUALITY_MIN_OPEN_INTEREST_USD="1000000"
```

## Inspect Markets

This command only reads public market data:

```powershell
python -m cli.main crypto-market-quality --mainnet --provider okx --symbols BTC,ETH,SOL,XRP
```

Temporary stricter thresholds:

```powershell
python -m cli.main crypto-market-quality --mainnet --provider okx --symbols BTC,XRP --max-spread-bps 5 --min-depth-usdc 25000
```

Legacy Hyperliquid inspection remains available:

```powershell
python -m cli.main crypto-market-quality --mainnet --provider hyperliquid --symbols BTC,ETH,SOL,HYPE
```

## Scan Integration

`crypto-scan` enables the gate by default:

```powershell
python -m cli.main crypto-scan --symbols BTC,ETH,SOL,XRP --mode analysis
```

If a BUY setup fails this gate, it is demoted to HOLD before the risk manager
sees it. The gate never promotes HOLD to BUY.

For debugging only:

```powershell
python -m cli.main crypto-scan --symbols BTC,ETH --mode analysis --no-market-quality
```

Keep the gate enabled for any paper, testnet, or future live workflow.
