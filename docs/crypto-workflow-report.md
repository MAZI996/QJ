# Crypto TradingAgents Workflow Report

This layer keeps the Binance crypto extension tied to the upstream
TradingAgents decision pattern instead of becoming a loose scanner.

The workflow runs:

1. Binance spot opportunity scan.
2. Deterministic personal-account risk gate.
3. Optional Hermes/LLM review.
4. TradingAgents-style role report:
   `Analyst Team -> Bull/Bear Researchers -> Research Manager -> Trader -> Risk Analysts -> Portfolio Manager`.

Use the workflow report when you need an auditable decision trail:

```powershell
python -m cli.main crypto-workflow --symbols BTCUSDT,ETHUSDT,SOLUSDT --mode analysis
python -m cli.main crypto-workflow --symbols BTCUSDT,ETHUSDT --mode analysis --ai-review
```

The command is safe by default:

- `--mode analysis` does not place orders.
- `--ai-review` is advisory only.
- Real Binance execution still requires the execution router, the deterministic
  risk gate, live mode, live config, and the explicit live confirmation phrase.

For later automation, this report becomes the record for why the system did or
did not trade. Hermes can choose the review model, but the model cannot bypass
`RiskManager` or change the execution mode by itself.
