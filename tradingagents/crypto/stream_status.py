"""Read Hyperliquid WebSocket archives and report data freshness."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import CryptoTradingConfig
from .hyperliquid_client import HyperliquidClient


REQUIRED_SYMBOL_CHANNELS: tuple[str, ...] = ("l2Book", "trades", "candle", "activeAssetCtx")
GLOBAL_CHANNELS: tuple[str, ...] = ("allMids",)


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
    selected = tuple(
        HyperliquidClient.normalize_symbol(symbol)
        for symbol in (symbols or config.symbols)
        if symbol.strip()
    )
    paths = _archive_paths(config.state_dir, archive_path)
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
            symbols_for_row = _symbols_for_row(row, channel)
            for symbol in symbols_for_row:
                key = (symbol, channel)
                counts[key] += 1
                if received > latest.get(key, datetime.min.replace(tzinfo=UTC)):
                    latest[key] = received

    rows: list[StreamFreshnessRow] = []
    for channel in GLOBAL_CHANNELS:
        rows.append(
            _freshness_row(
                key=("*", channel),
                counts=counts,
                latest=latest,
                now=current,
                max_age_seconds=max_age_seconds,
            )
        )
    for symbol in selected:
        for channel in REQUIRED_SYMBOL_CHANNELS:
            rows.append(
                _freshness_row(
                    key=(symbol, channel),
                    counts=counts,
                    latest=latest,
                    now=current,
                    max_age_seconds=max_age_seconds,
                )
            )

    return StreamStatusSummary(
        archive_paths=paths,
        symbols=selected,
        max_age_seconds=max_age_seconds,
        events_read=events_read,
        rows=tuple(rows),
    )


def _archive_paths(state_dir: Path, archive_path: Path | None) -> tuple[Path, ...]:
    if archive_path:
        return (archive_path,)
    event_dir = Path(state_dir) / "events"
    if not event_dir.exists():
        return ()
    return tuple(sorted(event_dir.glob("hyperliquid-ws-*.jsonl")))


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


def _symbols_for_row(row: dict[str, Any], channel: str) -> tuple[str, ...]:
    if channel in GLOBAL_CHANNELS:
        return ("*",)
    raw = row.get("symbols")
    if isinstance(raw, list):
        return tuple(
            HyperliquidClient.normalize_symbol(item)
            for item in raw
            if isinstance(item, str) and item.strip()
        )
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
