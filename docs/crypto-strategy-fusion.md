# Crypto Strategy Fusion

This project will not copy strategy code from external repositories. The
fusion layer adopts useful ideas and keeps all live authority inside our own
Hyperliquid trading-center, Hermes, TradingAgents, and risk-control boundaries.

Snapshot date: 2026-05-12. Stars are a live GitHub API snapshot and will drift.

| Repository | Stars | Useful idea for us |
| --- | ---: | --- |
| [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) | 50222 | Crypto bot lifecycle, dry-run/live split, dynamic whitelist, backtest-first discipline. |
| [microsoft/qlib](https://github.com/microsoft/qlib) | 42664 | AI quant research process, factor mining, production separation. |
| [ccxt/ccxt](https://github.com/ccxt/ccxt) | 42403 | Future multi-exchange abstraction. Hyperliquid is now the primary venue. |
| [mementum/backtrader](https://github.com/mementum/backtrader) | 21511 | Backtesting discipline and analyzers. |
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | 18928 | Algorithm engine structure, brokerage separation, research/live boundaries. |
| [hummingbot/hummingbot](https://github.com/hummingbot/hummingbot) | 18529 | Liquidity, spread, and execution-quality filters. |
| [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL) | 15128 | Reinforcement-learning research only after enough data and backtests. |
| [TA-Lib/ta-lib-python](https://github.com/TA-Lib/ta-lib-python) | 11953 | Technical indicator vocabulary. |
| [ccxt/binance-trade-bot](https://github.com/ccxt/binance-trade-bot) | 8668 | Simple Binance automation and portfolio rotation warning cases. |
| [jesse-ai/jesse](https://github.com/jesse-ai/jesse) | 7867 | Route-based crypto strategy design and strict stop discipline. |
| [freqtrade/freqtrade-strategies](https://github.com/freqtrade/freqtrade-strategies) | 5124 | Community indicator-vote patterns to study, not copy. |
| [bukosabino/ta](https://github.com/bukosabino/ta) | 5032 | Pandas/Numpy technical analysis indicators. |
| [CyberPunkMetalHead/Binance-volatility-trading-bot](https://github.com/CyberPunkMetalHead/Binance-volatility-trading-bot) | 3489 | Binance high-volatility rotation idea, close to the Lana-inspired hot-mover scan. |

The first implementation lives in `tradingagents/crypto/strategy_fusion.py` and
is intentionally conservative:

- It adjusts scanner confidence before `RiskManager`.
- It can demote weak or overextended signals.
- It only gives small positive deltas to signals with volume, trend, liquidity,
  stop-loss, and reward/risk agreement.
- It does not promote a base `HOLD` signal into `BUY`.
- Qlib/FinRL-style AI and reinforcement learning are logged as future research,
  not live execution authority.

CLI control:

```powershell
python -m cli.main crypto-workflow --symbols BTCUSDT,ETHUSDT,SOLUSDT --mode analysis
python -m cli.main crypto-workflow --symbols BTCUSDT,ETHUSDT --mode analysis --no-fusion
python -m cli.main crypto-scan --symbols BTCUSDT,SOLUSDT --mode paper --fusion
```

Important boundary: a small account can compound only if loss control survives.
The fusion layer never promises profit and never bypasses deterministic risk,
execution mode, live order config, or explicit live confirmation.
