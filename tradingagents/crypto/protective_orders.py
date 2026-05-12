"""Protective stop/take-profit order planning for Binance spot positions."""

from __future__ import annotations

from dataclasses import dataclass

from .config import CryptoTradingConfig
from .models import OrderIntent
from .positions import PositionRecord


@dataclass(frozen=True)
class ProtectiveOrderPlan:
    symbol: str
    quantity: float
    take_profit_price: float
    stop_price: float
    stop_limit_price: float
    order_type: str = "OCO_SELL"

    def to_binance_params(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "side": "SELL",
            "quantity": _format_number(self.quantity),
            "aboveType": "LIMIT_MAKER",
            "abovePrice": _format_number(self.take_profit_price),
            "belowType": "STOP_LOSS_LIMIT",
            "belowStopPrice": _format_number(self.stop_price),
            "belowPrice": _format_number(self.stop_limit_price),
            "belowTimeInForce": "GTC",
        }


def plan_from_intent(
    intent: OrderIntent,
    config: CryptoTradingConfig,
) -> ProtectiveOrderPlan | None:
    if intent.side != "BUY" or intent.stop_loss is None or intent.take_profit is None:
        return None
    return ProtectiveOrderPlan(
        symbol=intent.symbol,
        quantity=intent.quantity,
        take_profit_price=intent.take_profit,
        stop_price=intent.stop_loss,
        stop_limit_price=intent.stop_loss * (1 - config.protective_stop_limit_slippage_pct),
    )


def plan_from_position(
    position: PositionRecord,
    config: CryptoTradingConfig,
) -> ProtectiveOrderPlan | None:
    if not position.is_open or position.stop_loss is None or position.take_profit is None:
        return None
    return ProtectiveOrderPlan(
        symbol=position.symbol,
        quantity=position.quantity,
        take_profit_price=position.take_profit,
        stop_price=position.stop_loss,
        stop_limit_price=position.stop_loss * (1 - config.protective_stop_limit_slippage_pct),
    )


def _format_number(value: float) -> str:
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    return text or "0"
