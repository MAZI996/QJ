"""Order and position recovery helpers for crypto execution venues."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .binance_client import BinanceAPIError, BinanceClient
from .config import CryptoTradingConfig
from .hyperliquid_client import HyperliquidAPIError, HyperliquidClient
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
        client: BinanceClient | HyperliquidClient | Any,
        config: CryptoTradingConfig,
        positions: PositionStore | None = None,
    ):
        self.client = client
        self.config = config
        self.positions = positions or PositionStore.from_state_dir(config.state_dir)

    def recover_symbol(self, symbol: str) -> OrderRecoveryResult:
        if self.config.exchange_provider.strip().lower() == "hyperliquid":
            return self._recover_hyperliquid_symbol(symbol)
        return self._recover_binance_symbol(symbol)

    def _recover_binance_symbol(self, symbol: str) -> OrderRecoveryResult:
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
                notes="binance_order_recovery",
            )
            updated = updated or record is not None

        return OrderRecoveryResult(
            symbol=symbol,
            open_orders=len(open_orders),
            trades_seen=len(trades),
            position_updated=updated,
            message=f"Recovered Binance trades at {_now_iso()}",
        )

    def _recover_hyperliquid_symbol(self, symbol: str) -> OrderRecoveryResult:
        coin = HyperliquidClient.normalize_symbol(symbol)
        try:
            state = self.client.get_user_state()
            open_orders = self.client.get_open_orders()
        except HyperliquidAPIError as exc:
            return OrderRecoveryResult(coin, 0, 0, False, str(exc))

        matching_orders = [
            row for row in open_orders if str(row.get("coin", "")).upper() == coin
        ]
        position = _hyperliquid_position(state, coin)
        if not position:
            record = self.positions.sync_position(
                symbol=coin,
                quantity=0.0,
                avg_entry_price=0.0,
                notes="hyperliquid_recovery:no_exchange_position",
            )
            return OrderRecoveryResult(
                symbol=coin,
                open_orders=len(matching_orders),
                trades_seen=0,
                position_updated=record is not None,
                message=(
                    "No Hyperliquid clearinghouse position; local open position "
                    "closed if one existed."
                ),
            )

        size = _safe_float(position.get("szi"))
        entry_price = _safe_float(position.get("entryPx"))
        if size < 0:
            return OrderRecoveryResult(
                symbol=coin,
                open_orders=len(matching_orders),
                trades_seen=1,
                position_updated=False,
                message=(
                    "Hyperliquid short position detected; local long-only recovery "
                    "left state unchanged."
                ),
            )

        record = self.positions.sync_position(
            symbol=coin,
            quantity=size,
            avg_entry_price=entry_price,
            notes="hyperliquid_clearinghouse_recovery",
        )
        return OrderRecoveryResult(
            symbol=coin,
            open_orders=len(matching_orders),
            trades_seen=1 if size > 0 else 0,
            position_updated=record is not None,
            message=(
                f"Synced Hyperliquid clearinghouse position at {_now_iso()}: "
                f"size={size:.8f}, entry={entry_price:.4f}"
            ),
        )

    def normalize_user_data_event(self, event: dict[str, Any]) -> bool:
        record = self.positions.apply_execution_report(event)
        return record is not None


def _hyperliquid_position(state: dict[str, Any], coin: str) -> dict[str, Any]:
    for item in state.get("assetPositions", []):
        if not isinstance(item, dict):
            continue
        position = item.get("position", {})
        if not isinstance(position, dict):
            continue
        if str(position.get("coin", "")).upper() == coin:
            return position
    return {}


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
