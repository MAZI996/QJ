"""Order recovery helpers for live Binance integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .binance_client import BinanceAPIError, BinanceClient
from .config import CryptoTradingConfig
from .positions import PositionStore


@dataclass(frozen=True)
class OrderRecoveryResult:
    symbol: str
    open_orders: int
    trades_seen: int
    position_updated: bool
    message: str


class OrderRecoveryService:
    def __init__(
        self,
        client: BinanceClient,
        config: CryptoTradingConfig,
        positions: PositionStore | None = None,
    ):
        self.client = client
        self.config = config
        self.positions = positions or PositionStore.from_state_dir(config.state_dir)

    def recover_symbol(self, symbol: str) -> OrderRecoveryResult:
        symbol = symbol.upper()
        try:
            open_orders = self.client.get_open_orders(symbol)
            trades = self.client.get_my_trades(symbol, limit=50)
        except BinanceAPIError as exc:
            return OrderRecoveryResult(symbol, 0, 0, False, str(exc))

        updated = False
        for trade in trades:
            qty = float(trade.get("qty", 0.0))
            price = float(trade.get("price", 0.0))
            is_buyer = bool(trade.get("isBuyer", False))
            if qty <= 0 or price <= 0:
                continue
            record = self.positions.apply_fill(
                symbol=symbol,
                side="BUY" if is_buyer else "SELL",
                quantity=qty,
                price=price,
                stop_loss=None,
                take_profit=None,
                order_id=str(trade.get("orderId", "")),
                notes="order_recovery",
            )
            updated = updated or record is not None

        return OrderRecoveryResult(
            symbol=symbol,
            open_orders=len(open_orders),
            trades_seen=len(trades),
            position_updated=updated,
            message=f"Recovered at {datetime.now(timezone.utc).isoformat()}",
        )

    def normalize_user_data_event(self, event: dict[str, Any]) -> bool:
        record = self.positions.apply_execution_report(event)
        return record is not None
