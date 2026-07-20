"""OKX WebSocket event archive for real-time crypto analysis."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any, Callable

from .config import CryptoTradingConfig
from .okx_client import OKXClient


logger = logging.getLogger(__name__)


class OKXStreamError(RuntimeError):
    """Raised when the OKX WebSocket stream cannot start."""


@dataclass(frozen=True)
class OKXStreamEvent:
    received_at: str
    channel: str
    symbols: tuple[str, ...]
    summary: dict[str, Any]
    payload: dict[str, Any]

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


@dataclass(frozen=True)
class OKXStreamSubscription:
    arg: dict[str, str]


@dataclass(frozen=True)
class OKXStreamRunSummary:
    subscriptions: int
    events: int
    archive_path: Path
    websocket_url: str
    websocket_urls: tuple[str, ...]
    duration_seconds: int
    reconnects: int = 0


@dataclass
class _OKXSocketConnection:
    url: str
    plan: tuple[dict[str, str], ...]
    request_id: str
    socket: Any | None = None
    last_received_monotonic: float = 0.0
    last_ping_monotonic: float = 0.0
    subscription_seen_at: dict[tuple[str, str], float] = field(default_factory=dict)
    consecutive_failures: int = 0


class OKXEventArchive:
    def __init__(self, path: Path):
        self.path = path
        self.events_written = 0

    def write(self, event: OKXStreamEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.to_json_line() + "\n")
        self.events_written += 1


class OKXStreamService:
    def __init__(
        self,
        config: CryptoTradingConfig,
        *,
        symbols: tuple[str, ...] | None = None,
        interval: str | None = None,
        archive: OKXEventArchive | None = None,
        ws_factory: Callable[[str], Any] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        ping_interval_seconds: float = 20.0,
        subscription_stale_seconds: float = 120.0,
        reconnect_initial_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
    ):
        self.config = config
        self.client = OKXClient(config)
        self.symbols = tuple(
            self.client.instrument_id(symbol)
            for symbol in (symbols or config.symbols)
            if symbol.strip()
        )
        self.interval = interval or config.interval
        self.archive = archive or OKXEventArchive(default_okx_stream_archive_path(config.state_dir))
        self.ws_factory = ws_factory or _default_ws_factory
        self.now = now
        self.monotonic = monotonic
        self.sleep = sleep
        self.ping_interval_seconds = max(1.0, ping_interval_seconds)
        self.subscription_stale_seconds = max(1.0, subscription_stale_seconds)
        self.reconnect_initial_seconds = max(0.0, reconnect_initial_seconds)
        self.reconnect_max_seconds = max(
            self.reconnect_initial_seconds,
            reconnect_max_seconds,
        )
        self._stop = Event()
        self._sockets: list[Any] = []
        self._connections: list[_OKXSocketConnection] = []
        self._subscriptions: tuple[OKXStreamSubscription, ...] = ()
        self._reconnects = 0

    def subscription_plan(self) -> tuple[dict[str, str], ...]:
        args: list[dict[str, str]] = []
        candle_channel = okx_candle_channel(self.interval)
        for inst_id in self.symbols:
            args.extend(
                (
                    {"channel": "tickers", "instId": inst_id},
                    {"channel": "books", "instId": inst_id},
                    {"channel": "trades", "instId": inst_id},
                    {"channel": candle_channel, "instId": inst_id},
                )
            )
        return tuple(args)

    def start(self) -> tuple[OKXStreamSubscription, ...]:
        if self._connections:
            return self._subscriptions
        plan = self.subscription_plan()
        self._stop.clear()
        public_plan = tuple(arg for arg in plan if not arg["channel"].startswith("candle"))
        business_plan = tuple(arg for arg in plan if arg["channel"].startswith("candle"))
        if public_plan:
            self._connections.append(
                _OKXSocketConnection(
                    self.config.resolved_okx_ws_public_url,
                    public_plan,
                    request_id="okxpublic1",
                )
            )
        if business_plan:
            self._connections.append(
                _OKXSocketConnection(
                    self.config.resolved_okx_ws_business_url,
                    business_plan,
                    request_id="okxbusiness1",
                )
            )
        try:
            for connection in self._connections:
                self._connect(connection)
        except Exception as exc:
            self.stop()
            if isinstance(exc, OKXStreamError):
                raise
            raise OKXStreamError(f"OKX WebSocket start failed: {exc}") from exc
        self._subscriptions = tuple(OKXStreamSubscription(arg=dict(arg)) for arg in plan)
        return self._subscriptions

    def _connect(self, connection: _OKXSocketConnection) -> None:
        ws = self.ws_factory(connection.url)
        try:
            if hasattr(ws, "settimeout"):
                ws.settimeout(1.0)
            ws.send(
                json.dumps(
                    {
                        "id": connection.request_id,
                        "op": "subscribe",
                        "args": list(connection.plan),
                    },
                    separators=(",", ":"),
                )
            )
        except Exception:
            if hasattr(ws, "close"):
                ws.close()
            raise
        current = self.monotonic()
        connection.socket = ws
        connection.last_received_monotonic = current
        connection.last_ping_monotonic = current
        connection.subscription_seen_at = {
            _subscription_key(arg): current
            for arg in connection.plan
            if _requires_subscription_liveness(arg)
        }
        connection.consecutive_failures = 0
        self._sockets = [
            item.socket for item in self._connections if item.socket is not None
        ]

    def stop(self) -> None:
        self._stop.set()
        for connection in self._connections:
            ws = connection.socket
            if hasattr(ws, "close"):
                try:
                    ws.close()
                except Exception:
                    pass
            connection.socket = None
        self._sockets = []
        self._connections = []

    def run(self, *, duration_seconds: int) -> OKXStreamRunSummary:
        self.start()
        started = self.monotonic()
        try:
            while not self._stop.is_set():
                if duration_seconds > 0 and self.monotonic() - started >= duration_seconds:
                    break
                for connection in tuple(self._connections):
                    if self._stop.is_set():
                        break
                    ws = connection.socket
                    if ws is None:
                        self._reconnect(connection, "socket is not connected")
                        continue
                    try:
                        raw = ws.recv()
                    except TimeoutError:
                        self._maintain(connection)
                        continue
                    except Exception as exc:
                        if _is_timeout(exc):
                            self._maintain(connection)
                            continue
                        self._reconnect(connection, str(exc))
                        continue
                    if raw:
                        connection.last_received_monotonic = self.monotonic()
                        if raw == "pong" or raw == b"pong":
                            continue
                        event = self._on_message(raw)
                        if event is not None:
                            self._record_subscription_activity(connection, event)
        except KeyboardInterrupt:
            self._stop.set()
        finally:
            self.stop()
        return OKXStreamRunSummary(
            subscriptions=len(self._subscriptions),
            events=self.archive.events_written,
            archive_path=self.archive.path,
            websocket_url=self.config.resolved_okx_ws_public_url,
            websocket_urls=(
                self.config.resolved_okx_ws_public_url,
                self.config.resolved_okx_ws_business_url,
            ),
            duration_seconds=duration_seconds,
            reconnects=self._reconnects,
        )

    def _maintain(self, connection: _OKXSocketConnection) -> None:
        current = self.monotonic()
        stale = any(
            current - seen_at > self.subscription_stale_seconds
            for seen_at in connection.subscription_seen_at.values()
        )
        if stale:
            self._reconnect(connection, "one or more subscriptions became stale")
            return
        if current - connection.last_received_monotonic < self.ping_interval_seconds:
            return
        if current - connection.last_ping_monotonic < self.ping_interval_seconds:
            return
        try:
            assert connection.socket is not None
            connection.socket.send("ping")
            connection.last_ping_monotonic = current
        except Exception as exc:
            self._reconnect(connection, f"heartbeat failed: {exc}")

    def _reconnect(self, connection: _OKXSocketConnection, reason: str) -> None:
        ws = connection.socket
        if hasattr(ws, "close"):
            try:
                ws.close()
            except Exception:
                pass
        connection.socket = None
        self._sockets = [
            item.socket for item in self._connections if item.socket is not None
        ]
        connection.consecutive_failures += 1
        self._reconnects += 1
        logger.warning("Reconnecting OKX WebSocket %s: %s", connection.url, reason)
        delay = min(
            self.reconnect_initial_seconds
            * (2 ** max(0, connection.consecutive_failures - 1)),
            self.reconnect_max_seconds,
        )
        if delay and not self._stop.is_set():
            self.sleep(delay)
        if self._stop.is_set():
            return
        try:
            self._connect(connection)
        except Exception as exc:
            connection.socket = None
            logger.warning("OKX WebSocket reconnect failed for %s: %s", connection.url, exc)

    def _record_subscription_activity(
        self,
        connection: _OKXSocketConnection,
        event: OKXStreamEvent,
    ) -> None:
        current = connection.last_received_monotonic
        for symbol in event.symbols:
            key = (event.channel, self.client.instrument_id(symbol))
            if key in connection.subscription_seen_at:
                connection.subscription_seen_at[key] = current

    def _on_message(
        self,
        raw: str | bytes | dict[str, Any],
    ) -> OKXStreamEvent | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            try:
                message = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise OKXStreamError(f"OKX WebSocket returned invalid JSON: {raw[:200]}") from exc
        else:
            message = raw
        if not isinstance(message, dict):
            return None
        if message.get("event") == "error":
            raise OKXStreamError(f"OKX WebSocket error: {message}")
        if "data" not in message:
            return None
        event = okx_stream_event_from_message(message, received_at=self.now().isoformat())
        self.archive.write(event)
        return event


def default_okx_stream_archive_path(state_dir: Path, now: datetime | None = None) -> Path:
    current = now or datetime.now(UTC)
    return state_dir / "events" / f"okx-ws-{current:%Y%m%d}.jsonl"


def okx_candle_channel(interval: str) -> str:
    clean = interval.strip() or "15m"
    unit = clean[-1]
    amount = clean[:-1]
    if unit == "h":
        clean = f"{amount}H"
    elif unit == "d":
        clean = f"{amount}D"
    elif unit == "w":
        clean = f"{amount}W"
    return f"candle{clean}"


def okx_stream_event_from_message(message: dict[str, Any], *, received_at: str) -> OKXStreamEvent:
    arg = message.get("arg") if isinstance(message.get("arg"), dict) else {}
    channel = str(arg.get("channel") or message.get("channel") or "unknown")
    payload = message if isinstance(message, dict) else {"raw": message}
    return OKXStreamEvent(
        received_at=received_at,
        channel=channel,
        symbols=_symbols_from_message(arg, message.get("data")),
        summary=_summary_from_data(channel, message.get("data")),
        payload=payload,
    )


def _default_ws_factory(url: str) -> Any:
    try:
        import websocket
    except ImportError as exc:
        raise OKXStreamError(
            "Install websocket-client first: pip install websocket-client."
        ) from exc
    return websocket.create_connection(url, timeout=10)


def _symbols_from_message(arg: dict[str, Any], data: Any) -> tuple[str, ...]:
    symbols: list[str] = []
    inst_id = arg.get("instId")
    if isinstance(inst_id, str) and inst_id:
        symbols.append(OKXClient.normalize_symbol(inst_id))
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            row_inst_id = row.get("instId")
            if isinstance(row_inst_id, str) and row_inst_id:
                symbol = OKXClient.normalize_symbol(row_inst_id)
                if symbol not in symbols:
                    symbols.append(symbol)
    return tuple(symbols)


def _summary_from_data(channel: str, data: Any) -> dict[str, Any]:
    rows = data if isinstance(data, list) else []
    if not rows:
        return {}
    if channel == "tickers" and isinstance(rows[0], dict):
        row = rows[0]
        return {
            "last": _safe_float(row.get("last")),
            "bid": _safe_float(row.get("bidPx")),
            "ask": _safe_float(row.get("askPx")),
            "open24h": _safe_float(row.get("open24h")),
            "quote_volume_24h": _safe_float(row.get("volCcyQuote24h")),
        }
    if channel == "books" and isinstance(rows[0], dict):
        return _book_summary(rows[0])
    if channel == "trades":
        return _trade_summary(rows)
    if channel.startswith("candle"):
        return _candle_summary(channel, rows[0])
    return {"rows": len(rows)}


def _book_summary(row: dict[str, Any]) -> dict[str, Any]:
    bids = row.get("bids") if isinstance(row.get("bids"), list) else []
    asks = row.get("asks") if isinstance(row.get("asks"), list) else []
    best_bid = _safe_float(bids[0][0]) if bids and isinstance(bids[0], list) else 0.0
    best_ask = _safe_float(asks[0][0]) if asks and isinstance(asks[0], list) else 0.0
    mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0.0
    spread_bps = ((best_ask - best_bid) / mid) * 10_000 if mid > 0 else None
    return {
        "time_ms": int(row.get("ts", 0) or 0),
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
    return {"trade_count": len(rows), "notional_usdt": notional}


def _candle_summary(channel: str, row: Any) -> dict[str, Any]:
    if not isinstance(row, list) or len(row) < 6:
        return {"interval": channel.replace("candle", "", 1)}
    return {
        "interval": channel.replace("candle", "", 1),
        "time_ms": int(row[0]),
        "open": _safe_float(row[1]),
        "high": _safe_float(row[2]),
        "low": _safe_float(row[3]),
        "close": _safe_float(row[4]),
        "volume": _safe_float(row[5]),
        "confirmed": str(row[8]) == "1" if len(row) > 8 else False,
    }


def _is_timeout(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    return "timeout" in name or "timed out" in str(exc).lower()


def _subscription_key(arg: dict[str, str]) -> tuple[str, str]:
    return str(arg.get("channel", "")), str(arg.get("instId", ""))


def _requires_subscription_liveness(arg: dict[str, str]) -> bool:
    return arg.get("channel") != "trades"


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
