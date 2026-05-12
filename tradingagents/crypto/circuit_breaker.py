"""Capital-preservation circuit breakers for crypto autopilot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .config import CryptoTradingConfig
from .positions import PositionStore


@dataclass(frozen=True)
class CircuitBreakerState:
    blocked: bool
    reason: str
    realized_pnl_usdt: float
    daily_loss_limit_usdt: float


class DailyLossCircuitBreaker:
    def __init__(
        self,
        config: CryptoTradingConfig,
        positions: PositionStore | None = None,
    ):
        self.config = config
        self.positions = positions or PositionStore.from_state_dir(config.state_dir)

    def evaluate(self) -> CircuitBreakerState:
        today = datetime.now(timezone.utc).date()
        records = [
            item
            for item in self.positions.load().values()
            if _parse_date(item.updated_at) == today
        ]
        realized = sum(item.realized_pnl_usdt for item in records)
        limit = self._loss_limit()
        if limit > 0 and realized <= -limit:
            return CircuitBreakerState(
                blocked=True,
                reason=f"Daily realized loss {realized:.2f} USDT reached limit {limit:.2f} USDT.",
                realized_pnl_usdt=realized,
                daily_loss_limit_usdt=limit,
            )
        return CircuitBreakerState(
            blocked=False,
            reason="Daily loss circuit breaker clear.",
            realized_pnl_usdt=realized,
            daily_loss_limit_usdt=limit,
        )

    def _loss_limit(self) -> float:
        if self.config.daily_loss_limit_usdt > 0:
            return self.config.daily_loss_limit_usdt
        return self.config.account_equity_usdt * self.config.daily_loss_limit_pct


def _parse_date(value: str):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).date()
    except ValueError:
        return None
