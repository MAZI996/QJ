"""TradingAgents-style prompt assembly for crypto decisions.

The original project runs a graph of specialist agents. The Hyperliquid
workflow is kept lighter for now, but its LLM contract mirrors the same
hand-off sequence so Hermes can later route each role to a dedicated model if
needed.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base_contract import TRADINGAGENTS_ROLE_CHAIN
from .models import OpportunitySignal


@dataclass(frozen=True)
class CryptoAgentRole:
    name: str
    responsibility: str


CRYPTO_AGENT_ROLES: tuple[CryptoAgentRole, ...] = (
    CryptoAgentRole(
        "Market Analyst",
        "Read venue price action, volume, volatility, trend, and breakout evidence.",
    ),
    CryptoAgentRole(
        "Sentiment Analyst",
        "Treat sentiment as unknown unless external sentiment data is explicitly supplied.",
    ),
    CryptoAgentRole(
        "Bull Researcher",
        "Build the strongest long-only case from the scanner candidates.",
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
        "Flag anything that may violate personal-account limits or the 1x long-only boundary.",
    ),
    CryptoAgentRole(
        "Portfolio Manager",
        "Make the final decision while preserving capital and respecting the hard risk gate.",
    ),
)


def build_tradingagents_crypto_prompt(candidates: list[OpportunitySignal]) -> str:
    role_block = "\n".join(
        f"- {role.name}: {role.responsibility}" for role in CRYPTO_AGENT_ROLES
    )
    candidate_block = "\n\n".join(_format_candidate(item) for item in candidates[:5])
    if not candidate_block:
        candidate_block = "No candidates were produced by the scanner."

    return f"""
You are running a crypto adaptation of the TradingAgents framework for a
personal exchange account. Follow the same role hand-off pattern, but do not
call external tools in this step.

Canonical TradingAgents role chain:
{" -> ".join(TRADINGAGENTS_ROLE_CHAIN)}

Crypto role responsibilities:
{role_block}

Hard constraints:
- OKX is the primary trading venue for this workflow.
- Long-only during the initial stage. No short selling.
- Leverage cap is 1 until the user explicitly approves a higher-risk phase.
- Never promise profit.
- Never bypass deterministic risk controls.
- A BUY is only an AI recommendation. A later deterministic risk gate can reject it.
- If evidence is incomplete, prefer HOLD or REJECT.
- Output Chinese text, but keep the exact English labels below.

Candidate evidence from the scanner. Deterministic risk sizing and permission
are evaluated only after this AI review:
{candidate_block}

Return exactly this structure:
Action: BUY, HOLD, or REJECT
Symbol: exact candidate symbol for BUY, otherwise NONE
Confidence: a decimal between 0 and 1
Summary: concise final decision and best candidate, or say none
Main risk: the most important risk
Invalidation: market condition that invalidates the setup
Role notes: one compact paragraph summarizing the analyst/research/trader/risk hand-off
"""


def _format_candidate(signal: OpportunitySignal) -> str:
    metrics = ", ".join(
        f"{key}={value:.4f}" for key, value in sorted(signal.metrics.items())
    )
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
            f"Reasons: {'; '.join(signal.reasons)}",
            f"Metrics: {metrics}",
        ]
    )
