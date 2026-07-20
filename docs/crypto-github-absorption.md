# Crypto GitHub Absorption Plan

This project will absorb all seven reviewed GitHub directions as bounded
capabilities. We keep TradingAgents as the base layer and Hyperliquid as the
primary venue; outside projects are references for proven workflows, not
replacement engines.

## Sources And Local Targets

| Source | What We Absorb | Local Target | Status |
| --- | --- | --- | --- |
| Hyperliquid Python SDK | Official REST, WebSocket, account, and signed order flows | `hyperliquid_client.py`, `hyperliquid_execution.py`, diagnostics | Partially adopted |
| Freqtrade | Dry-run discipline, backtesting, optimization, persistence, ops reporting | paper mode, paper status, performance summaries, live-readiness evidence | Pattern adopted |
| Hummingbot | Connector abstraction, controller/executor split, market-making lessons | execution router, position guardian, market-quality gate | Planned |
| Jesse | Strategy lifecycle from research to backtest to paper/live | scanner, strategy fusion, backtest, autopilot | Pattern adopted |
| vectorbt | Fast parameter sweeps and portfolio analytics | optional vectorized research runner feeding candidate configs | Planned optional dependency |
| hftbacktest | L2 replay with latency and queue-position assumptions | order-book archive plus microstructure replay bridge | Planned optional dependency |
| NautilusTrader | Event-driven research/live architecture consistency | event journal, position state, future event bus | Architecture reference |

## Absorption Rules

- Do not replace the TradingAgents role chain.
- Do not route orders directly from an LLM or third-party strategy.
- Do not enable live orders by default.
- Keep Hyperliquid long-only with max leverage 1 during validation.
- Every strategy path must pass scanner -> AI review if enabled -> deterministic
  risk manager -> execution router.
- Optional dependencies must stay optional until the local workflow proves they
  improve replay, paper, or monitoring quality.

## Implementation Phases

### Phase 1: Real-Time Data And Paper Evidence

Absorb Hyperliquid SDK WebSocket examples first:

- Persistent `l2Book` subscription.
- Trade and candle update ingestion.
- `userEvents` and fill recovery for the configured wallet.
- Local event archive under `TRADINGAGENTS_CRYPTO_STATE_DIR`.
- Paper/autopilot reports that prove enough cycles before testnet or live.

Freqtrade and Jesse influence this phase through dry-run evidence, strategy
lifecycle discipline, and paper/live consistency.

### Phase 2: Strategy And Execution Structure

Absorb Hummingbot and Jesse patterns:

- Split autopilot into signal selection and active execution management.
- Keep position guardian as the first executor.
- Add explicit strategy contracts with identical inputs across backtest, paper,
  testnet, and live.
- Add operational status commands for recent cycles, open positions, and rejected
  opportunities.

### Phase 3: Research Acceleration

Absorb vectorbt concepts as an optional research runner:

- Batch parameter sweeps for existing strategies.
- Ranking by return, drawdown, trade count, and robustness.
- Export only candidate configs into our existing backtest and paper queue.

Vectorized winners are not live-ready by themselves.

### Phase 4: L2/Microstructure Validation

Absorb hftbacktest concepts:

- Archive Hyperliquid order-book snapshots.
- Replay spread/depth/imbalance strategies against stored L2 data.
- Model fee, slippage, feed latency, order latency, and queue assumptions before
  any market-making or fast order strategy is allowed.

### Phase 5: Event-Driven Hardening

Absorb NautilusTrader architecture principles:

- Represent market data, signal, risk, order, fill, position, and stop events
  explicitly.
- Keep one journal format across paper, testnet, and live.
- Maintain the same TradingAgents decision workflow while making runtime state
  easier to recover after restarts.

## CLI

Show the full local absorption map:

```powershell
python -m cli.main crypto-github-absorption
```

Write it as JSON:

```powershell
python -m cli.main crypto-github-absorption --json-output reports/github-absorption.json
```

## Current Judgment

The existing analysis stack is enough for paper validation, not enough for
unattended live capital. The next concrete implementation should be the
Hyperliquid WebSocket/event archive layer, then multi-day paper evidence and
Hermes review checks.
