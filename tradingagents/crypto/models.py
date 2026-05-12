"""Small data models for the crypto trading workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


Side = Literal["BUY", "SELL", "HOLD"]
ExecutionMode = Literal["analysis", "paper", "testnet", "live"]
AIAction = Literal["BUY", "HOLD", "REJECT"]


@dataclass(frozen=True)
class Candle:
    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time_ms: int

    @property
    def close_time(self) -> datetime:
        return datetime.fromtimestamp(self.close_time_ms / 1000, tz=timezone.utc)


@dataclass(frozen=True)
class TickerSnapshot:
    symbol: str
    last_price: float
    price_change_pct_24h: float
    quote_volume_24h: float


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    base_asset: str
    quote_asset: str
    min_qty: float
    step_size: float
    min_notional: float


@dataclass(frozen=True)
class AccountBalance:
    asset: str
    free: float
    locked: float


@dataclass(frozen=True)
class OpportunitySignal:
    symbol: str
    side: Side
    confidence: float
    entry_price: float
    stop_loss: float | None
    take_profit: float | None
    timeframe: str
    strategy: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def risk_reward(self) -> float | None:
        if self.side != "BUY" or self.stop_loss is None or self.take_profit is None:
            return None
        risk = self.entry_price - self.stop_loss
        reward = self.take_profit - self.entry_price
        if risk <= 0:
            return None
        return reward / risk


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: Side
    quantity: float
    notional_usdt: float
    entry_price: float
    stop_loss: float | None
    take_profit: float | None
    reason: str


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    intent: OrderIntent | None = None
    rejected_rules: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AITradeReview:
    action: AIAction
    confidence: float
    summary: str
    main_risk: str
    invalidation: str
    model: str
    router: str
    raw_response: str = ""


@dataclass(frozen=True)
class OrderResult:
    mode: ExecutionMode
    accepted: bool
    symbol: str
    side: Side
    quantity: float
    message: str
    exchange_payload: dict[str, Any] = field(default_factory=dict)
