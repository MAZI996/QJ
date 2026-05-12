"""Persistent position state for crypto automation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import OrderIntent, OrderResult, Side


@dataclass(frozen=True)
class PositionRecord:
    symbol: str
    quantity: float
    avg_entry_price: float
    stop_loss: float | None
    take_profit: float | None
    opened_at: str
    updated_at: str
    status: str = "OPEN"
    realized_pnl_usdt: float = 0.0
    last_order_id: str = ""
    notes: str = ""

    @property
    def is_open(self) -> bool:
        return self.status == "OPEN" and self.quantity > 0

    def unrealized_pnl(self, mark_price: float) -> float:
        if not self.is_open:
            return 0.0
        return (mark_price - self.avg_entry_price) * self.quantity


class PositionStore:
    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def from_state_dir(cls, state_dir: Path) -> "PositionStore":
        return cls(state_dir / "positions.json")

    def load(self) -> dict[str, PositionRecord]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        rows = payload.get("positions", payload if isinstance(payload, list) else [])
        records: dict[str, PositionRecord] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            record = PositionRecord(
                symbol=str(row.get("symbol", "")).upper(),
                quantity=float(row.get("quantity", 0.0)),
                avg_entry_price=float(row.get("avg_entry_price", 0.0)),
                stop_loss=_optional_float(row.get("stop_loss")),
                take_profit=_optional_float(row.get("take_profit")),
                opened_at=str(row.get("opened_at", _now_iso())),
                updated_at=str(row.get("updated_at", _now_iso())),
                status=str(row.get("status", "OPEN")),
                realized_pnl_usdt=float(row.get("realized_pnl_usdt", 0.0)),
                last_order_id=str(row.get("last_order_id", "")),
                notes=str(row.get("notes", "")),
            )
            if record.symbol:
                records[record.symbol] = record
        return records

    def save(self, records: dict[str, PositionRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": _now_iso(),
            "positions": [
                asdict(item) for item in sorted(records.values(), key=lambda r: r.symbol)
            ],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def active_positions(self) -> list[PositionRecord]:
        return [item for item in self.load().values() if item.is_open]

    def apply_order_result(self, intent: OrderIntent, result: OrderResult) -> PositionRecord | None:
        if not result.accepted:
            return None
        fill_price, quantity = _fill_from_payload(intent, result)
        return self.apply_fill(
            symbol=intent.symbol,
            side=intent.side,
            quantity=quantity,
            price=fill_price,
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            order_id=_order_id(result.exchange_payload),
            notes=f"mode={result.mode}",
        )

    def apply_execution_report(self, payload: dict[str, Any]) -> PositionRecord | None:
        event = payload.get("event", payload)
        if event.get("e") != "executionReport" or event.get("x") not in {"TRADE", "FILLED"}:
            return None
        quantity = float(event.get("l") or event.get("z") or 0.0)
        price = float(event.get("L") or 0.0)
        if quantity <= 0 or price <= 0:
            return None
        return self.apply_fill(
            symbol=str(event.get("s", "")).upper(),
            side=str(event.get("S", "BUY")).upper(),  # type: ignore[arg-type]
            quantity=quantity,
            price=price,
            stop_loss=None,
            take_profit=None,
            order_id=str(event.get("i", "")),
            notes="user_data_stream",
        )

    def apply_fill(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        price: float,
        stop_loss: float | None,
        take_profit: float | None,
        order_id: str = "",
        notes: str = "",
    ) -> PositionRecord | None:
        symbol = symbol.upper()
        if quantity <= 0 or price <= 0:
            return None
        records = self.load()
        now = _now_iso()
        current = records.get(symbol)
        if side == "BUY":
            if current and current.is_open:
                total_quantity = current.quantity + quantity
                avg_price = (
                    (current.avg_entry_price * current.quantity) + (price * quantity)
                ) / total_quantity
                record = replace(
                    current,
                    quantity=total_quantity,
                    avg_entry_price=avg_price,
                    stop_loss=stop_loss if stop_loss is not None else current.stop_loss,
                    take_profit=take_profit if take_profit is not None else current.take_profit,
                    updated_at=now,
                    last_order_id=order_id or current.last_order_id,
                    notes=notes or current.notes,
                )
            else:
                record = PositionRecord(
                    symbol=symbol,
                    quantity=quantity,
                    avg_entry_price=price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    opened_at=now,
                    updated_at=now,
                    last_order_id=order_id,
                    notes=notes,
                )
            records[symbol] = record
            self.save(records)
            return record

        if side == "SELL" and current:
            closed_quantity = min(quantity, current.quantity)
            realized = (price - current.avg_entry_price) * closed_quantity
            remaining = max(0.0, current.quantity - closed_quantity)
            status = "CLOSED" if remaining <= 0 else "OPEN"
            record = replace(
                current,
                quantity=remaining,
                status=status,
                realized_pnl_usdt=current.realized_pnl_usdt + realized,
                updated_at=now,
                last_order_id=order_id or current.last_order_id,
                notes=notes or current.notes,
            )
            records[symbol] = record
            self.save(records)
            return record
        return None


def _fill_from_payload(intent: OrderIntent, result: OrderResult) -> tuple[float, float]:
    payload = result.exchange_payload or {}
    fills = payload.get("fills") if isinstance(payload, dict) else None
    if isinstance(fills, list) and fills:
        total_qty = 0.0
        total_quote = 0.0
        for fill in fills:
            qty = float(fill.get("qty", 0.0))
            price = float(fill.get("price", 0.0))
            total_qty += qty
            total_quote += qty * price
        if total_qty > 0:
            return total_quote / total_qty, total_qty

    executed_qty = (
        float(payload.get("executedQty", 0.0) or 0.0) if isinstance(payload, dict) else 0.0
    )
    quote_qty = (
        float(payload.get("cummulativeQuoteQty", 0.0) or 0.0)
        if isinstance(payload, dict)
        else 0.0
    )
    if executed_qty > 0 and quote_qty > 0:
        return quote_qty / executed_qty, executed_qty
    return intent.entry_price, intent.quantity


def _order_id(payload: dict[str, Any]) -> str:
    value = payload.get("orderId") or payload.get("clientOrderId") or ""
    return str(value)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
