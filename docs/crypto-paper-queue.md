# Hyperliquid Paper Entry Queue

The paper queue is the bridge between historical replay and paper trading. It
runs the backtest sweep, applies deterministic candidate filters, and writes a
paper-only queue plan.

It does not place orders. It only writes JSON and Markdown files with reviewed
paper commands.

## Run

```powershell
python -m cli.main crypto-paper-queue --mainnet --symbols BTC,ETH,SOL,HYPE --intervals 5m,15m,1h --lookbacks 60,120 --max-holding-bars 16,32,48 --bars 800 --min-trades 5 --min-win-rate 0.40 --min-return-pct 0 --max-drawdown-pct 5 --max-consecutive-losses 3
python -m cli.main crypto-paper-status
```

The default output is:

```text
~/.tradingagents/crypto/paper_queue.json
~/.tradingagents/crypto/paper_queue.md
```

Custom output:

```powershell
python -m cli.main crypto-paper-queue --mainnet --symbols BTC,HYPE --output reports/hyperliquid-paper-queue
```

## Safety Boundary

- Queue items are generated only from backtest candidates.
- Equivalent paper commands are de-duplicated. If two candidates differ only by
  historical holding-window metadata, the higher-ranked one is queued.
- Generated commands use `--mode paper --execute-top --auto-close`, so paper
  validation covers both entries and reduce-only exit handling.
- The TradingAgents workflow, AI review if requested, risk manager, and
  execution router still run before a paper fill can be recorded.
- A queued paper candidate is not a live-trading approval.
