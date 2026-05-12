"""Contract between the crypto extension and upstream TradingAgents.

This module keeps the architectural dependency explicit: crypto automation is
an extension of TradingAgents, not a separate bot with unrelated control flow.
"""

from __future__ import annotations


UPSTREAM_TRADINGAGENTS_REPO = "https://github.com/TauricResearch/TradingAgents.git"

TRADINGAGENTS_ROLE_CHAIN: tuple[str, ...] = (
    "Analyst Team",
    "Bull/Bear Researchers",
    "Research Manager",
    "Trader",
    "Risk Analysts",
    "Portfolio Manager",
)

CRYPTO_EXTENSION_BOUNDARIES: tuple[str, ...] = (
    "Hyperliquid is the primary crypto trading venue; Binance remains a legacy optional adapter.",
    "Hermes and model selection are routed through the crypto LLM router.",
    "LLM output is advisory until deterministic risk approves an order intent.",
    "Execution stays behind analysis/paper/testnet/live modes and explicit live confirmation.",
)


def describe_base_contract() -> str:
    roles = " -> ".join(TRADINGAGENTS_ROLE_CHAIN)
    boundaries = "\n".join(f"- {item}" for item in CRYPTO_EXTENSION_BOUNDARIES)
    return (
        "Crypto trading automation must remain a TradingAgents extension.\n\n"
        f"Upstream: {UPSTREAM_TRADINGAGENTS_REPO}\n"
        f"Role chain: {roles}\n\n"
        f"Boundaries:\n{boundaries}"
    )
