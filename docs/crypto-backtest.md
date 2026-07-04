# Hyperliquid Historical Replay

This is the first quant-research layer after choosing the Hyperliquid route.
It keeps TradingAgents as the decision framework and replays historical candles
through the existing deterministic chain:

```text
Hyperliquid candles -> scanner -> strategy fusion -> RiskManager -> simulated exit
```

It does not call the execution router, does not use private keys, and does not
place orders.

## Run

```powershell
python -m cli.main crypto-backtest --mainnet --symbols BTC,ETH,SOL,HYPE --interval 15m --bars 500
```

Useful stricter replay:

```powershell
python -m cli.main crypto-backtest --mainnet --symbols BTC,HYPE --interval 5m --bars 1000 --lookback 120 --max-holding-bars 48 --fee-bps 4 --slippage-bps 2
```

Replay the inventory bridge state machine directly:

```powershell
python -m cli.main crypto-backtest --mainnet --symbols BTC --interval 5m --bars 1000 --lookback 120 --inventory-bridge --dead-reserve-ratio 0.20 --inventory-dca-slices 12 --unlock-acceleration-threshold 0.002 --inventory-sell-ratio 1.0 --bridge-beta-threshold 0.05 --bridge-moon-phase-pressure 0.20 --bridge-deadline-force-pct 0.10
```

Batch parameter comparison:

```powershell
python -m cli.main crypto-backtest-sweep --mainnet --symbols BTC,ETH,SOL,HYPE --intervals 5m,15m,1h --lookbacks 60,120 --max-holding-bars 16,32,48 --bars 500
```

Export all cases to CSV and the ranked summary to Markdown:

```powershell
python -m cli.main crypto-backtest-sweep --mainnet --symbols BTC,HYPE --intervals 5m,15m --lookbacks 60,120 --max-holding-bars 16,32 --bars 800 --csv-output reports/hyperliquid-sweep.csv --markdown-output reports/hyperliquid-sweep.md
```

Show only paper-trading candidates after deterministic screening:

```powershell
python -m cli.main crypto-backtest-sweep --mainnet --symbols BTC,ETH,SOL,HYPE --intervals 5m,15m,1h --lookbacks 60,120 --max-holding-bars 16,32,48 --bars 800 --min-trades 5 --min-win-rate 0.40 --min-return-pct 0 --max-drawdown-pct 5 --max-consecutive-losses 3 --candidates-only
```

Build a paper-only queue from those candidates:

```powershell
python -m cli.main crypto-paper-queue --mainnet --symbols BTC,ETH,SOL,HYPE --intervals 5m,15m,1h --lookbacks 60,120 --max-holding-bars 16,32,48 --bars 800 --min-trades 5 --min-win-rate 0.40 --min-return-pct 0 --max-drawdown-pct 5 --max-consecutive-losses 3
```

## Conservative Assumptions

- Signals use only candles available up to the current replay bar.
- The scanner applies the entry-quality gate before risk sizing: weak closes,
  choppy paths, and overextended entries are demoted before they can become
  trades.
- Simulated entry happens on the next candle open.
- If stop-loss and take-profit are both touched in the same candle, stop-loss
  wins.
- Fees and adverse slippage are subtracted from every simulated trade.
- The evolution lab can enable the optional inventory bridge. That bridge
  records macro DCA buys as dead inventory, unlocks them on acceleration spikes,
  and sells the resulting float inventory as realized simulated PnL.
- Inventory bridge macro buys can be gated by beta deviation from an EMA anchor,
  amplified by cyclical pressure, forced by a deadline fallback, and released by
  stale-inventory GC rules.
- Historical order-book quality is not replayed yet; the existing
  `market-quality` gate remains a live/public pre-trade filter.
- Sweep ranking uses return percent minus max drawdown, scaled down for cases
  with fewer than five trades. Treat it as a screening score, not a live-trade
  approval.
- Paper-candidate filters are hard deterministic gates: minimum trades,
  minimum win rate, minimum return, maximum drawdown, and maximum consecutive
  losses. Passing them does not approve live orders.
- `crypto-paper-queue` writes a queue plan only. The generated commands use
  `--mode paper` and still run TradingAgents workflow risk gates before any
  simulated fill is recorded.

## How To Use The Result

Use this to reject weak scanner settings before paper trading. A strategy should
not move toward Hyperliquid SDK execution until historical replay, paper mode,
decision journals, recovery, and loss limits all agree.

For genetic search on top of this replay layer, see
`docs/crypto-evolution-lab.md`. The evolution lab emits challenger packages
only and does not approve live trading.
