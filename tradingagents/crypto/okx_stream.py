"""OKX WebSocket event archive for real-time crypto analysis."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any, Callable

from .config import CryptoTradingConfig
from .okx_client import OKXClient


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
        self._stop = Event()
        self._sockets: list[Any] = []
        self._subscriptions: tuple[OKXStreamSubscription, ...] = ()

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
        if self._sockets:
            return self._subscriptions
        plan = self.subscription_plan()
        self._stop.clear()
        public_plan = tuple(arg for arg in plan if not arg["channel"].startswith("candle"))
        business_plan = tuple(arg for arg in plan if arg["channel"].startswith("candle"))
        if public_plan:
            self._sockets.append(
                self._open_and_subscribe(
                    self.config.resolved_okx_ws_public_url,
                    public_plan,
                    request_id="okxpublic1",
                )
            )
        if business_plan:
            self._sockets.append(
                self._open_and_subscribe(
                    self.config.resolved_okx_ws_business_url,
                    business_plan,
                    request_id="okxbusiness1",
                )
            )
        self._subscriptions = tuple(OKXStreamSubscription(arg=dict(arg)) for arg in plan)
        return self._subscriptions

    def _open_and_subscribe(
        self,
        url: str,
        plan: tuple[dict[str, str], ...],
        *,
        request_id: str,
    ) -> Any:
        ws = self.ws_factory(url)
        if hasattr(ws, "settimeout"):
            ws.settimeout(1.0)
        ws.send(
            json.dumps(
                {
                    "id": request_id,
                    "op": "subscribe",
                    "args": list(plan),
                },
                separators=(",", ":"),
            )
        )
        return ws

    def stop(self) -> None:
        self._stop.set()
        for ws in self._sockets:
            if hasattr(ws, "close"):
                ws.close()
        self._sockets = []

    def run(self, *, duration_seconds: int) -> OKXStreamRunSummary:
        self.start()
        started = time.monotonic()
        try:
            while not self._stop.is_set():
                if duration_seconds > 0 and time.monotonic() - started >= duration_seconds:
                    break
                for ws in tuple(self._sockets):
                    try:
                        raw = ws.recv()
                    except TimeoutError:
                        continue
                    except Exception as exc:
                        if _is_timeout(exc):
                            continue
                        raise OKXStreamError(f"OKX WebSocket receive failed: {exc}") from exc
                    if raw:
                        self._on_message(raw)
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
        )

    def _on_message(self, raw: str | bytes | dict[str, Any]) -> None:
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
            return
        if message.get("event") == "error":
            raise OKXStreamError(f"OKX WebSocket error: {message}")
        if "data" not in message:
            return
        self.archive.write(
            okx_stream_event_from_message(message, received_at=self.now().isoformat())
        )


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


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
