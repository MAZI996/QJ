"""Read exchange WebSocket archives and report data freshness."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import CryptoTradingConfig
from .hyperliquid_client import HyperliquidClient
from .okx_client import OKXClient
from .okx_stream import okx_candle_channel


HYPERLIQUID_REQUIRED_SYMBOL_CHANNELS: tuple[str, ...] = (
    "l2Book",
    "trades",
    "candle",
    "activeAssetCtx",
)
HYPERLIQUID_GLOBAL_CHANNELS: tuple[str, ...] = ("allMids",)
OKX_BASE_REQUIRED_SYMBOL_CHANNELS: tuple[str, ...] = ("tickers", "books")
OKX_GLOBAL_CHANNELS: tuple[str, ...] = ()

REQUIRED_SYMBOL_CHANNELS = HYPERLIQUID_REQUIRED_SYMBOL_CHANNELS
GLOBAL_CHANNELS = HYPERLIQUID_GLOBAL_CHANNELS


@dataclass(frozen=True)
class StreamFreshnessRow:
    symbol: str
    channel: str
    count: int
    last_received_at: str
    age_seconds: float | None
    fresh: bool
    message: str


@dataclass(frozen=True)
class StreamStatusSummary:
    provider: str
    archive_paths: tuple[Path, ...]
    symbols: tuple[str, ...]
    max_age_seconds: int
    events_read: int
    rows: tuple[StreamFreshnessRow, ...]

    @property
    def fresh(self) -> bool:
        return all(row.fresh for row in self.rows)

    @property
    def missing_or_stale(self) -> tuple[StreamFreshnessRow, ...]:
        return tuple(row for row in self.rows if not row.fresh)

    @property
    def latest_event_at(self) -> str:
        latest = ""
        for row in self.rows:
            if row.last_received_at and row.last_received_at > latest:
                latest = row.last_received_at
        return latest


def summarize_stream_status(
    config: CryptoTradingConfig,
    *,
    symbols: tuple[str, ...] | None = None,
    archive_path: Path | None = None,
    max_age_seconds: int = 600,
    max_lines_per_file: int = 5000,
    now: datetime | None = None,
) -> StreamStatusSummary:
    provider = config.exchange_provider.strip().lower() or "okx"
    normalizer = _symbol_normalizer(provider)
    selected = tuple(normalizer(symbol) for symbol in (symbols or config.symbols) if symbol.strip())
    global_channels, required_channels = _required_channels(config, provider)
    paths = _archive_paths(config.state_dir, archive_path, provider)
    current = now or datetime.now(UTC)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    latest: dict[tuple[str, str], datetime] = {}
    events_read = 0

    for path in paths:
        for row in _read_jsonl_tail(path, max_lines_per_file):
            events_read += 1
            channel = str(row.get("channel", ""))
            received = _parse_time(row.get("received_at"))
            if received is None:
                continue
            symbols_for_row = _symbols_for_row(row, channel, global_channels, normalizer)
            for symbol in symbols_for_row:
                key = (symbol, channel)
                counts[key] += 1
                if received > latest.get(key, datetime.min.replace(tzinfo=UTC)):
                    latest[key] = received

    rows: list[StreamFreshnessRow] = []
    for channel in global_channels:
        rows.append(
            _freshness_row(
                key=("*", channel),
                counts=counts,
                latest=latest,
                now=current,
                max_age_seconds=_channel_max_age_seconds(
                    provider,
                    channel,
                    max_age_seconds,
                ),
            )
        )
    for symbol in selected:
        for channel in required_channels:
            rows.append(
                _freshness_row(
                    key=(symbol, channel),
                    counts=counts,
                    latest=latest,
                    now=current,
                    max_age_seconds=_channel_max_age_seconds(
                        provider,
                        channel,
                        max_age_seconds,
                    ),
                )
            )

    return StreamStatusSummary(
        provider=provider,
        archive_paths=paths,
        symbols=selected,
        max_age_seconds=max_age_seconds,
        events_read=events_read,
        rows=tuple(rows),
    )


def _required_channels(
    config: CryptoTradingConfig,
    provider: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if provider == "okx":
        return OKX_GLOBAL_CHANNELS, (
            *OKX_BASE_REQUIRED_SYMBOL_CHANNELS,
            okx_candle_channel(config.interval),
        )
    return HYPERLIQUID_GLOBAL_CHANNELS, HYPERLIQUID_REQUIRED_SYMBOL_CHANNELS


def _symbol_normalizer(provider: str):
    if provider == "okx":
        return OKXClient.normalize_symbol
    if provider == "hyperliquid":
        return HyperliquidClient.normalize_symbol
    return lambda value: str(value).strip().upper()


def _channel_max_age_seconds(
    provider: str,
    channel: str,
    requested_max_age_seconds: int,
) -> int:
    if provider == "okx" and channel.startswith("candle"):
        return max(requested_max_age_seconds, 120)
    return requested_max_age_seconds


def _archive_paths(
    state_dir: Path,
    archive_path: Path | None,
    provider: str,
) -> tuple[Path, ...]:
    if archive_path:
        return (archive_path,)
    event_dir = Path(state_dir) / "events"
    if not event_dir.exists():
        return ()
    pattern = "okx-ws-*.jsonl" if provider == "okx" else "hyperliquid-ws-*.jsonl"
    return tuple(sorted(event_dir.glob(pattern)))


def _read_jsonl_tail(path: Path, max_lines: int) -> tuple[dict[str, Any], ...]:
    if not path.exists() or path.stat().st_size == 0:
        return ()
    lines: deque[str] = deque(maxlen=max_lines)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            lines.append(line)
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return tuple(rows)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _symbols_for_row(
    row: dict[str, Any],
    channel: str,
    global_channels: tuple[str, ...],
    normalizer,
) -> tuple[str, ...]:
    if channel in global_channels:
        return ("*",)
    raw = row.get("symbols")
    if isinstance(raw, list):
        return tuple(normalizer(item) for item in raw if isinstance(item, str) and item.strip())
    return ()


def _freshness_row(
    *,
    key: tuple[str, str],
    counts: dict[tuple[str, str], int],
    latest: dict[tuple[str, str], datetime],
    now: datetime,
    max_age_seconds: int,
) -> StreamFreshnessRow:
    symbol, channel = key
    count = counts.get(key, 0)
    received = latest.get(key)
    if received is None:
        return StreamFreshnessRow(
            symbol=symbol,
            channel=channel,
            count=count,
            last_received_at="",
            age_seconds=None,
            fresh=False,
            message="No WebSocket archive event found.",
        )
    age = max(0.0, (now - received).total_seconds())
    fresh = age <= max_age_seconds
    return StreamFreshnessRow(
        symbol=symbol,
        channel=channel,
        count=count,
        last_received_at=received.isoformat(),
        age_seconds=age,
        fresh=fresh,
        message="Fresh." if fresh else f"Last event is older than {max_age_seconds} seconds.",
    )
