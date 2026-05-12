# Hyperliquid Market Quality Gate

This layer improves automated-trading selectivity by rejecting fragile market
conditions before risk sizing or execution.

It uses Hyperliquid public `info` data:

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
- Hyperliquid market-quality data cannot be loaded.

## Defaults

These defaults are intentionally conservative for a small personal account:

```powershell
$env:TRADINGAGENTS_CRYPTO_MARKET_QUALITY_ENABLED="true"
$env:TRADINGAGENTS_CRYPTO_MARKET_QUALITY_MAX_SPREAD_BPS="8"
$env:TRADINGAGENTS_CRYPTO_MARKET_QUALITY_MIN_DEPTH_USDC="10000"
$env:TRADINGAGENTS_CRYPTO_MARKET_QUALITY_DEPTH_LEVELS="5"
$env:TRADINGAGENTS_CRYPTO_MARKET_QUALITY_MAX_ABS_IMBALANCE="0.80"
$env:TRADINGAGENTS_CRYPTO_MARKET_QUALITY_MAX_ABS_FUNDING_RATE="0.001"
```

## Inspect Markets

This command only reads public market data:

```powershell
python -m cli.main crypto-market-quality --mainnet --symbols BTC,ETH,SOL,HYPE
```

Temporary stricter thresholds:

```powershell
python -m cli.main crypto-market-quality --mainnet --symbols BTC,HYPE --max-spread-bps 5 --min-depth-usdc 25000
```

## Scan Integration

`crypto-scan` enables the gate by default:

```powershell
python -m cli.main crypto-scan --symbols BTC,ETH,SOL,HYPE --mode analysis
```

If a BUY setup fails this gate, it is demoted to HOLD before the risk manager
sees it. The gate never promotes HOLD to BUY.

For debugging only:

```powershell
python -m cli.main crypto-scan --symbols BTC,ETH --mode analysis --no-market-quality
```

Keep the gate enabled for any paper, testnet, or future live workflow.
