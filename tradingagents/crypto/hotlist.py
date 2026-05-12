"""Local hot-symbol list used by attention-driven crypto scans."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import CryptoTradingConfig


@dataclass(frozen=True)
class HotSymbol:
    symbol: str
    source: str
    score: float
    reason: str
    observed_at: str
    expires_at: str | None = None

    @property
    def observed_datetime(self) -> datetime | None:
        return _parse_datetime(self.observed_at)

    @property
    def expires_datetime(self) -> datetime | None:
        if not self.expires_at:
            return None
        return _parse_datetime(self.expires_at)


def load_hot_symbols(config: CryptoTradingConfig) -> tuple[str, ...]:
    if not config.hotlist_enabled:
        return config.lana_hot_symbols

    entries = load_hotlist(config.hotlist_path)
    active = filter_hotlist(
        entries,
        max_age_hours=config.hotlist_max_age_hours,
        min_score=config.hotlist_min_score,
    )
    merged = list(config.lana_hot_symbols)
    for entry in active:
        if entry.symbol not in merged:
            merged.append(entry.symbol)
    return tuple(merged)


def load_hotlist(path: Path) -> list[HotSymbol]:
    if not path.exists():
        return []
    if path.suffix.lower() in {".txt", ".csv"}:
        return _load_text_hotlist(path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("symbols", [])
    else:
        rows = []
    entries: list[HotSymbol] = []
    for row in rows:
        if isinstance(row, str):
            entries.append(_new_hot_symbol(row, source="manual", reason=""))
        elif isinstance(row, dict):
            entries.append(
                HotSymbol(
                    symbol=str(row.get("symbol", "")).strip().upper(),
                    source=str(row.get("source", "manual")).strip() or "manual",
                    score=float(row.get("score", 1.0)),
                    reason=str(row.get("reason", "")).strip(),
                    observed_at=str(row.get("observed_at", _now_iso())),
                    expires_at=row.get("expires_at"),
                )
            )
    return [entry for entry in entries if entry.symbol]


def save_hotlist(path: Path, entries: list[HotSymbol]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "updated_at": _now_iso(),
        "symbols": [asdict(entry) for entry in entries],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def add_hot_symbol(
    path: Path,
    symbol: str,
    source: str = "manual",
    score: float = 1.0,
    reason: str = "",
    ttl_hours: float = 24.0,
) -> HotSymbol:
    symbol = symbol.strip().upper()
    entries = [entry for entry in load_hotlist(path) if entry.symbol != symbol]
    now = datetime.now(timezone.utc)
    entry = HotSymbol(
        symbol=symbol,
        source=source,
        score=max(0.0, min(score, 1.0)),
        reason=reason,
        observed_at=now.isoformat(),
        expires_at=(now + timedelta(hours=ttl_hours)).isoformat() if ttl_hours > 0 else None,
    )
    entries.append(entry)
    save_hotlist(path, entries)
    return entry


def merge_hot_symbols(path: Path, new_entries: list[HotSymbol]) -> list[HotSymbol]:
    merged = {entry.symbol: entry for entry in load_hotlist(path)}
    for entry in new_entries:
        existing = merged.get(entry.symbol)
        if existing is None or entry.score >= existing.score:
            merged[entry.symbol] = entry
    entries = list(merged.values())
    save_hotlist(path, entries)
    return entries


def filter_hotlist(
    entries: list[HotSymbol],
    max_age_hours: float,
    min_score: float,
    include_expired: bool = False,
) -> list[HotSymbol]:
    now = datetime.now(timezone.utc)
    active: list[HotSymbol] = []
    for entry in entries:
        if entry.score < min_score:
            continue
        if not include_expired:
            expires_at = entry.expires_datetime
            if expires_at and expires_at < now:
                continue
            observed_at = entry.observed_datetime
            if observed_at and max_age_hours > 0:
                if observed_at < now - timedelta(hours=max_age_hours):
                    continue
        active.append(entry)
    return sorted(active, key=lambda item: item.score, reverse=True)


def _load_text_hotlist(path: Path) -> list[HotSymbol]:
    entries: list[HotSymbol] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.split("#", 1)[0].strip()
        if not clean:
            continue
        parts = [part.strip() for part in clean.replace(",", " ").split() if part.strip()]
        for symbol in parts:
            entries.append(_new_hot_symbol(symbol, source="text", reason=""))
    return entries


def _new_hot_symbol(symbol: str, source: str, reason: str) -> HotSymbol:
    return HotSymbol(
        symbol=symbol.strip().upper(),
        source=source,
        score=1.0,
        reason=reason,
        observed_at=_now_iso(),
    )


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
