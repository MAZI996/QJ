"""TradingAgents-style prompt assembly for crypto decisions.

The original project runs a graph of specialist agents. The Hyperliquid
workflow is kept lighter for now, but its LLM contract mirrors the same
hand-off sequence so Hermes can later route each role to a dedicated model if
needed.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base_contract import TRADINGAGENTS_ROLE_CHAIN
from .engine import ReviewedSignal


@dataclass(frozen=True)
class CryptoAgentRole:
    name: str
    responsibility: str


CRYPTO_AGENT_ROLES: tuple[CryptoAgentRole, ...] = (
    CryptoAgentRole(
        "Market Analyst",
        "Read Hyperliquid price action, volume, volatility, trend, and breakout evidence.",
    ),
    CryptoAgentRole(
        "Sentiment Analyst",
        "Treat sentiment as unknown unless external sentiment data is explicitly supplied.",
    ),
    CryptoAgentRole(
        "Bull Researcher",
        "Build the strongest long-only case from approved Hyperliquid candidates.",
    ),
    CryptoAgentRole(
        "Bear Researcher",
        "Challenge weak momentum, overextension, liquidity, and invalidation risks.",
    ),
    CryptoAgentRole(
        "Research Manager",
        "Balance the bull and bear cases into a single trade thesis or no-trade decision.",
    ),
    CryptoAgentRole(
        "Trader",
        "Translate the thesis into BUY/HOLD/REJECT with entry, stop, and take-profit context.",
    ),
    CryptoAgentRole(
        "Risk Analysts",
        "Reject anything that violates deterministic risk, personal-account limits, or the 1x long-only Hyperliquid boundary.",
    ),
    CryptoAgentRole(
        "Portfolio Manager",
        "Make the final decision while preserving capital and respecting the hard risk gate.",
    ),
)


def build_tradingagents_crypto_prompt(reviewed: list[ReviewedSignal]) -> str:
    role_block = "\n".join(
        f"- {role.name}: {role.responsibility}" for role in CRYPTO_AGENT_ROLES
    )
    candidate_block = "\n\n".join(_format_candidate(item) for item in reviewed[:5])
    if not candidate_block:
        candidate_block = "No candidates were produced by the scanner."

    return f"""
You are running a crypto adaptation of the TradingAgents framework for a
personal Hyperliquid account. Follow the same role hand-off pattern, but do not
call external tools in this step.

Canonical TradingAgents role chain:
{" -> ".join(TRADINGAGENTS_ROLE_CHAIN)}

Crypto role responsibilities:
{role_block}

Hard constraints:
- Hyperliquid is the primary trading venue.
- Long-only during the initial stage. No short selling.
- Leverage cap is 1 until the user explicitly approves a higher-risk phase.
- Never promise profit.
- Never bypass deterministic risk controls.
- If deterministic risk rejects a candidate, the final action cannot be BUY.
- If evidence is incomplete, prefer HOLD or REJECT.
- Output Chinese text, but keep the exact English labels below.

Candidate evidence from the scanner and risk gate:
{candidate_block}

Return exactly this structure:
Action: BUY, HOLD, or REJECT
Confidence: a decimal between 0 and 1
Summary: concise final decision and best candidate, or say none
Main risk: the most important risk
Invalidation: market condition that invalidates the setup
Role notes: one compact paragraph summarizing the analyst/research/trader/risk hand-off
"""


def _format_candidate(item: ReviewedSignal) -> str:
    signal = item.signal
    risk = item.risk
    intent = risk.intent
    order_notional = (
        f"Order notional: {intent.notional_usdt:.2f} USDT"
        if intent
        else "Order notional: None"
    )
    metrics = ", ".join(
        f"{key}={value:.4f}" for key, value in sorted(signal.metrics.items())
    )
    rejected = "; ".join(risk.rejected_rules) if risk.rejected_rules else "None"
    return "\n".join(
        [
            f"Symbol: {signal.symbol}",
            f"Side: {signal.side}",
            f"Strategy: {signal.strategy}",
            f"Confidence: {signal.confidence:.2f}",
            f"Timeframe: {signal.timeframe}",
            f"Entry: {signal.entry_price}",
            f"Stop: {signal.stop_loss}",
            f"Take profit: {signal.take_profit}",
            f"Risk reward: {signal.risk_reward}",
            f"Risk approved: {risk.approved}",
            f"Rejected rules: {rejected}",
            order_notional,
            f"Reasons: {'; '.join(signal.reasons)}",
            f"Metrics: {metrics}",
        ]
    )
