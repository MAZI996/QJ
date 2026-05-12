"""Paper/live journal performance summaries."""

from __future__ import annotations

from dataclasses import dataclass

from .config import CryptoTradingConfig
from .positions import PositionStore


@dataclass(frozen=True)
class PerformanceSummary:
    open_positions: int
    closed_positions: int
    realized_pnl_usdt: float
    unrealized_pnl_usdt: float
    wins: int
    losses: int
    win_rate: float


def summarize_performance(
    config: CryptoTradingConfig,
    mark_prices: dict[str, float] | None = None,
) -> PerformanceSummary:
    mark_prices = mark_prices or {}
    records = list(PositionStore.from_state_dir(config.state_dir).load().values())
    open_positions = [item for item in records if item.is_open]
    closed = [item for item in records if not item.is_open]
    realized = sum(item.realized_pnl_usdt for item in records)
    unrealized = sum(
        item.unrealized_pnl(mark_prices.get(item.symbol, item.avg_entry_price))
        for item in open_positions
    )
    wins = sum(1 for item in records if item.realized_pnl_usdt > 0)
    losses = sum(1 for item in records if item.realized_pnl_usdt < 0)
    finished = wins + losses
    win_rate = (wins / finished) if finished else 0.0
    return PerformanceSummary(
        open_positions=len(open_positions),
        closed_positions=len(closed),
        realized_pnl_usdt=realized,
        unrealized_pnl_usdt=unrealized,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
    )
