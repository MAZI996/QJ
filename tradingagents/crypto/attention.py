"""Attention text parsing for hot-symbol discovery.

This is the ingestion layer between social/forum/Hyperliquid ecosystem text and the
local hotlist. It deliberately stays offline: platform scrapers can feed text
into it later without changing scanner logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .hotlist import HotSymbol


_SYMBOL_RE = re.compile(r"(?<![A-Z0-9])(?:\$|#)?([A-Z]{2,12})(?:USDT|USD)?(?![A-Z0-9])")
_IGNORE_TOKENS = {
    "AI",
    "API",
    "ATH",
    "BTCUSDT",  # matched again as BTC via suffix handling
    "CEX",
    "DEX",
    "ETF",
    "FOMO",
    "KOL",
    "NFT",
    "ROI",
    "TVL",
    "USD",
    "USDT",
}
_HEAT_KEYWORDS = (
    "热",
    "爆",
    "冲",
    "拉",
    "涨",
    "异动",
    "涨幅榜",
    "高流量",
    "高发帖",
    "讨论",
    "fomo",
    "trend",
    "trending",
    "breakout",
    "pump",
    "moon",
    "volume",
)


@dataclass(frozen=True)
class AttentionCandidate:
    symbol: str
    score: float
    mentions: int
    reason: str


def extract_attention_candidates(
    text: str,
    quote_asset: str = "USDT",
    min_mentions: int = 1,
) -> list[AttentionCandidate]:
    counts: dict[str, int] = {}
    for raw in _SYMBOL_RE.findall(text.upper()):
        token = raw.strip().upper()
        if token in _IGNORE_TOKENS:
            continue
        if token.endswith(quote_asset):
            token = token[: -len(quote_asset)]
        if len(token) < 2:
            continue
        symbol = f"{token}{quote_asset}"
        counts[symbol] = counts.get(symbol, 0) + 1

    heat_hits = _count_heat_keywords(text)
    candidates: list[AttentionCandidate] = []
    for symbol, mentions in counts.items():
        if mentions < min_mentions:
            continue
        score = min(1.0, 0.35 + (mentions * 0.12) + (heat_hits * 0.04))
        reason = f"mentions={mentions}, heat_keywords={heat_hits}"
        candidates.append(
            AttentionCandidate(
                symbol=symbol,
                score=score,
                mentions=mentions,
                reason=reason,
            )
        )
    return sorted(candidates, key=lambda item: (item.score, item.mentions), reverse=True)


def candidates_to_hot_symbols(
    candidates: list[AttentionCandidate],
    source: str,
    ttl_hours: float,
) -> list[HotSymbol]:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(hours=ttl_hours)).isoformat() if ttl_hours > 0 else None
    return [
        HotSymbol(
            symbol=candidate.symbol,
            source=source,
            score=candidate.score,
            reason=candidate.reason,
            observed_at=now.isoformat(),
            expires_at=expires_at,
        )
        for candidate in candidates
    ]


def _count_heat_keywords(text: str) -> int:
    lowered = text.lower()
    return sum(1 for keyword in _HEAT_KEYWORDS if keyword in lowered)
