"""Hyperliquid WebSocket event archive for real-time crypto analysis."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any, Callable

from .config import CryptoTradingConfig
from .hyperliquid_client import HyperliquidClient


class HyperliquidStreamError(RuntimeError):
    """Raised when the Hyperliquid WebSocket stream cannot start."""


@dataclass(frozen=True)
class HyperliquidStreamEvent:
    received_at: str
    channel: str
    symbols: tuple[str, ...]
    summary: dict[str, Any]
    payload: dict[str, Any]

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


@dataclass(frozen=True)
class HyperliquidStreamSubscription:
    subscription_id: int
    subscription: dict[str, Any]


@dataclass(frozen=True)
class HyperliquidStreamRunSummary:
    subscriptions: int
    events: int
    archive_path: Path
    base_url: str
    duration_seconds: int
    user_events_enabled: bool


class HyperliquidEventArchive:
    def __init__(self, path: Path):
        self.path = path
        self.events_written = 0

    def write(self, event: HyperliquidStreamEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.to_json_line() + "\n")
        self.events_written += 1


class HyperliquidStreamService:
    def __init__(
        self,
        config: CryptoTradingConfig,
        *,
        symbols: tuple[str, ...] | None = None,
        interval: str | None = None,
        archive: HyperliquidEventArchive | None = None,
        info_factory: Callable[[str], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.config = config
        self.symbols = tuple(
            HyperliquidClient.normalize_symbol(symbol)
            for symbol in (symbols or config.symbols)
            if symbol.strip()
        )
        self.interval = interval or config.interval
        self.archive = archive or HyperliquidEventArchive(default_stream_archive_path(config.state_dir))
        self.info_factory = info_factory or _default_info_factory
        self.sleep = sleep
        self.now = now
        self._info: Any | None = None
        self._stop = Event()
        self._subscriptions: list[HyperliquidStreamSubscription] = []

    def subscription_plan(self, *, user_events: bool = False) -> tuple[dict[str, Any], ...]:
        subscriptions: list[dict[str, Any]] = [{"type": "allMids"}]
        for symbol in self.symbols:
            subscriptions.extend(
                (
                    {"type": "l2Book", "coin": symbol},
                    {"type": "trades", "coin": symbol},
                    {"type": "candle", "coin": symbol, "interval": self.interval},
                    {"type": "activeAssetCtx", "coin": symbol},
                )
            )
        if user_events:
            wallet = self.config.hyperliquid_wallet_address.strip()
            if not wallet:
                raise HyperliquidStreamError(
                    "TRADINGAGENTS_CRYPTO_HYPERLIQUID_WALLET_ADDRESS is required for user event streams."
                )
            subscriptions.extend(
                (
                    {"type": "userEvents", "user": wallet},
                    {"type": "userFills", "user": wallet},
                    {"type": "orderUpdates", "user": wallet},
                    {"type": "userFundings", "user": wallet},
                    {"type": "userNonFundingLedgerUpdates", "user": wallet},
                )
            )
        return tuple(subscriptions)

    def start(self, *, user_events: bool = False) -> tuple[HyperliquidStreamSubscription, ...]:
        if self._info is not None:
            return tuple(self._subscriptions)
        self._stop.clear()
        self._subscriptions = []
        self._info = self.info_factory(self.config.resolved_hyperliquid_base_url)
        for subscription in self.subscription_plan(user_events=user_events):
            original = dict(subscription)
            subscription_id = self._info.subscribe(subscription, self._on_message)
            self._subscriptions.append(
                HyperliquidStreamSubscription(
                    subscription_id=subscription_id,
                    subscription=original,
                )
            )
        return tuple(self._subscriptions)

    def stop(self) -> None:
        self._stop.set()
        if self._info is not None and hasattr(self._info, "disconnect_websocket"):
            self._info.disconnect_websocket()
        self._info = None

    def run(self, *, duration_seconds: int, user_events: bool = False) -> HyperliquidStreamRunSummary:
        self.start(user_events=user_events)
        started = time.monotonic()
        try:
            while not self._stop.is_set():
                if duration_seconds > 0 and time.monotonic() - started >= duration_seconds:
                    break
                self.sleep(0.5)
        except KeyboardInterrupt:
            self._stop.set()
        finally:
            self.stop()
        return HyperliquidStreamRunSummary(
            subscriptions=len(self._subscriptions),
            events=self.archive.events_written,
            archive_path=self.archive.path,
            base_url=self.config.resolved_hyperliquid_base_url,
            duration_seconds=duration_seconds,
            user_events_enabled=user_events,
        )

    def _on_message(self, message: dict[str, Any]) -> None:
        event = stream_event_from_message(message, received_at=self.now().isoformat())
        self.archive.write(event)


def default_stream_archive_path(state_dir: Path, now: datetime | None = None) -> Path:
    current = now or datetime.now(UTC)
    return state_dir / "events" / f"hyperliquid-ws-{current:%Y%m%d}.jsonl"


def stream_event_from_message(message: dict[str, Any], *, received_at: str) -> HyperliquidStreamEvent:
    channel = str(message.get("channel", "unknown"))
    data = message.get("data")
    payload = message if isinstance(message, dict) else {"raw": message}
    return HyperliquidStreamEvent(
        received_at=received_at,
        channel=channel,
        symbols=_symbols_from_data(channel, data),
        summary=_summary_from_data(channel, data),
        payload=payload,
    )


def _default_info_factory(base_url: str) -> Any:
    try:
        from hyperliquid.info import Info
    except ImportError as exc:
        raise HyperliquidStreamError(
            "Install the official SDK first: pip install hyperliquid-python-sdk."
        ) from exc
    return Info(base_url=base_url)


def _symbols_from_data(channel: str, data: Any) -> tuple[str, ...]:
    if isinstance(data, dict):
        for key in ("coin", "s"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return (HyperliquidClient.normalize_symbol(value),)
        fills = data.get("fills")
        if isinstance(fills, list):
            return _symbols_from_rows(fills)
    if isinstance(data, list):
        return _symbols_from_rows(data)
    return ()


def _symbols_from_rows(rows: list[Any]) -> tuple[str, ...]:
    symbols: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        coin = row.get("coin")
        if isinstance(coin, str) and coin:
            normalized = HyperliquidClient.normalize_symbol(coin)
            if normalized not in symbols:
                symbols.append(normalized)
    return tuple(symbols)


def _summary_from_data(channel: str, data: Any) -> dict[str, Any]:
    if channel == "allMids" and isinstance(data, dict):
        mids = data.get("mids") if isinstance(data.get("mids"), dict) else data
        return {"mids_count": len(mids)}
    if channel == "l2Book" and isinstance(data, dict):
        return _book_summary(data)
    if channel == "trades" and isinstance(data, list):
        return _trade_summary(data)
    if channel == "candle" and isinstance(data, dict):
        return {
            "interval": data.get("i"),
            "open": _safe_float(data.get("o")),
            "high": _safe_float(data.get("h")),
            "low": _safe_float(data.get("l")),
            "close": _safe_float(data.get("c")),
            "volume": _safe_float(data.get("v")),
        }
    if channel in {"activeAssetCtx", "activeSpotAssetCtx"} and isinstance(data, dict):
        ctx = data.get("ctx") if isinstance(data.get("ctx"), dict) else {}
        return {
            "mark_price": _safe_float(ctx.get("markPx")),
            "funding": _safe_float(ctx.get("funding")),
            "open_interest": _safe_float(ctx.get("openInterest")),
        }
    if channel in {"user", "userFills"} and isinstance(data, dict):
        fills = data.get("fills")
        return {"fills_count": len(fills) if isinstance(fills, list) else 0}
    return {}


def _book_summary(data: dict[str, Any]) -> dict[str, Any]:
    levels = data.get("levels")
    if not isinstance(levels, list) or len(levels) < 2:
        return {"bid_levels": 0, "ask_levels": 0}
    bids = levels[0] if isinstance(levels[0], list) else []
    asks = levels[1] if isinstance(levels[1], list) else []
    best_bid = _safe_float(bids[0].get("px")) if bids and isinstance(bids[0], dict) else 0.0
    best_ask = _safe_float(asks[0].get("px")) if asks and isinstance(asks[0], dict) else 0.0
    mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0.0
    spread_bps = ((best_ask - best_bid) / mid) * 10_000 if mid > 0 else None
    return {
        "time_ms": int(data.get("time", 0) or 0),
        "bid_levels": len(bids),
        "ask_levels": len(asks),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_bps": spread_bps,
    }


def _trade_summary(rows: list[Any]) -> dict[str, Any]:
    notional = 0.0
    for row in rows:
        if not isinstance(row, dict):
            continue
        notional += _safe_float(row.get("px")) * _safe_float(row.get("sz"))
    return {"trade_count": len(rows), "notional_usdc": notional}


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
