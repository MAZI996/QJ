"""GitHub source adoption map for the crypto extension.

The map keeps outside project lessons explicit without turning this repository
into a wrapper around another trading bot. Each source is absorbed as a bounded
capability target behind the existing TradingAgents and Hyperliquid risk gates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SourceAdoption:
    key: str
    name: str
    repository_url: str
    source_strength: str
    adoption_target: str
    local_modules: tuple[str, ...]
    status: str
    next_step: str
    guardrail: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


GITHUB_ADOPTION_SOURCES: tuple[SourceAdoption, ...] = (
    SourceAdoption(
        key="hyperliquid-python-sdk",
        name="Hyperliquid Python SDK",
        repository_url="https://github.com/hyperliquid-dex/hyperliquid-python-sdk",
        source_strength="Official Hyperliquid REST, WebSocket, and signed exchange SDK.",
        adoption_target="Primary venue adapter for market data, account recovery, and execution.",
        local_modules=(
            "tradingagents/crypto/hyperliquid_client.py",
            "tradingagents/crypto/hyperliquid_execution.py",
            "tradingagents/crypto/hyperliquid_diagnostics.py",
        ),
        status="autopilot_stream_gate_added",
        next_step="Summarize WebSocket uptime and fresh-cycle counts in paper evidence reports.",
        guardrail="Use API wallets only; keep live orders behind readiness, confirmation, and protective orders.",
    ),
    SourceAdoption(
        key="freqtrade",
        name="Freqtrade",
        repository_url="https://github.com/freqtrade/freqtrade",
        source_strength="Dry-run, backtesting, optimization, persistence, and operational bot workflows.",
        adoption_target="Paper-trading discipline, strategy qualification, and operational reporting.",
        local_modules=(
            "tradingagents/crypto/paper.py",
            "tradingagents/crypto/paper_status.py",
            "tradingagents/crypto/backtest.py",
            "tradingagents/crypto/performance.py",
        ),
        status="pattern_adopted",
        next_step="Require multi-day paper evidence and performance summaries before any live readiness pass.",
        guardrail="Do not import external strategies directly into live trading without local replay and risk review.",
    ),
    SourceAdoption(
        key="hummingbot",
        name="Hummingbot",
        repository_url="https://github.com/hummingbot/hummingbot",
        source_strength="Exchange connector abstractions, market-making patterns, controllers, and executors.",
        adoption_target="Controller/executor separation for real-time order management and future market-making research.",
        local_modules=(
            "tradingagents/crypto/execution.py",
            "tradingagents/crypto/position_guardian.py",
            "tradingagents/crypto/market_quality.py",
        ),
        status="planned",
        next_step="Split autopilot entry selection from active order/position executors.",
        guardrail="No high-frequency quoting until L2 replay, fee, latency, and inventory limits are proven.",
    ),
    SourceAdoption(
        key="jesse",
        name="Jesse",
        repository_url="https://github.com/jesse-ai/jesse",
        source_strength="Python strategy lifecycle for research, backtesting, optimization, and live/paper trading.",
        adoption_target="A consistent strategy contract from replay to paper to testnet to live.",
        local_modules=(
            "tradingagents/crypto/scanner.py",
            "tradingagents/crypto/strategy_fusion.py",
            "tradingagents/crypto/backtest.py",
            "tradingagents/crypto/autopilot.py",
        ),
        status="pattern_adopted",
        next_step="Formalize a strategy interface with identical inputs across backtest, paper, and live cycles.",
        guardrail="Strategies produce signals only; deterministic risk gates still own sizing and execution permission.",
    ),
    SourceAdoption(
        key="vectorbt",
        name="vectorbt",
        repository_url="https://github.com/polakowo/vectorbt",
        source_strength="Fast portfolio backtesting, parameter sweeps, rankings, analytics, and visualization.",
        adoption_target="Batch research layer for large parameter grids and robustness analysis.",
        local_modules=(
            "tradingagents/crypto/backtest.py",
            "tradingagents/crypto/evolution.py",
        ),
        status="planned_optional_dependency",
        next_step="Add an optional vectorized research runner that exports candidate configurations only.",
        guardrail="Vectorized winners must pass the existing replay, paper queue, and live-readiness gates.",
    ),
    SourceAdoption(
        key="hftbacktest",
        name="hftbacktest",
        repository_url="https://github.com/nkaz001/hftbacktest",
        source_strength="Order-book replay with feed latency, order latency, and queue-position simulation.",
        adoption_target="Microstructure validation for L2/order-book strategies before real order placement.",
        local_modules=(
            "tradingagents/crypto/market_quality.py",
            "tradingagents/crypto/backtest.py",
        ),
        status="planned_optional_dependency",
        next_step="Archive Hyperliquid L2 snapshots and build a replay bridge for spread/depth strategies.",
        guardrail="Do not allow L2-derived strategies into live mode until replay includes latency and fee assumptions.",
    ),
    SourceAdoption(
        key="nautilus-trader",
        name="NautilusTrader",
        repository_url="https://github.com/nautechsystems/nautilus_trader",
        source_strength="Event-driven backtest/live architecture with consistent engine semantics.",
        adoption_target="Long-term event bus, state machine, and research-to-live architecture reference.",
        local_modules=(
            "tradingagents/crypto/autopilot.py",
            "tradingagents/crypto/decision_journal.py",
            "tradingagents/crypto/positions.py",
        ),
        status="architecture_reference",
        next_step="Introduce explicit market-data, signal, risk, order, fill, and position events in the journal.",
        guardrail="Keep the current TradingAgents role chain as the base layer; avoid a broad engine migration.",
    ),
)


def adoption_sources() -> tuple[SourceAdoption, ...]:
    return GITHUB_ADOPTION_SOURCES


def adoption_keys() -> tuple[str, ...]:
    return tuple(source.key for source in GITHUB_ADOPTION_SOURCES)


def adoption_by_key(key: str) -> SourceAdoption:
    normalized = key.strip().lower()
    for source in GITHUB_ADOPTION_SOURCES:
        if source.key == normalized:
            return source
    valid = ", ".join(adoption_keys())
    raise KeyError(f"unknown adoption source: {key}; expected one of: {valid}")
