# Project Rules

## TradingAgents Is The Base Layer

This project is built on top of `TauricResearch/TradingAgents`.

- Keep `upstream` pointed at `https://github.com/TauricResearch/TradingAgents.git`.
- Treat the upstream graph, agent roles, LLM client factory, structured-output helpers, CLI style, and report/memory conventions as the base framework.
- Add Hyperliquid, Hermes, hotlist, and execution work as an extension layer, primarily under `tradingagents/crypto/`.
- Do not turn this repository into a standalone trading bot that bypasses TradingAgents concepts.
- Prefer adapting the TradingAgents role chain: Analyst Team -> Bull/Bear Researchers -> Research Manager -> Trader -> Risk Analysts -> Portfolio Manager.
- Keep hard deterministic risk gates outside the LLM. The model can review or rank signals, but cannot bypass risk checks or submit orders directly.
- When upstream changes are needed, preserve compatibility with `upstream/main` and avoid broad edits to stock-analysis modules unless the change is intentionally shared with both stock and crypto workflows.

## Hyperliquid Trading Center Safety

- Hyperliquid is the primary trading venue for new crypto work.
- Keep Binance code as a legacy optional adapter, not the main route.
- Default to analysis or paper modes.
- Hyperliquid is primarily a perpetuals venue; start long-only with leverage capped at 1.
- Do not enable short selling, leverage above 1, withdrawals, or force live trading by default.
- Live orders require explicit configuration and CLI confirmation.
- Any new strategy must pass through scanner -> AI review -> risk manager -> execution router.
