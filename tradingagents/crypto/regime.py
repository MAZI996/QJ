"""Deterministic market-regime classifier for runtime champion parameters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from .config import CryptoTradingConfig
from .hyperliquid_client import HyperliquidClient
from .models import Candle


REGIME_SEASONS: tuple[str, ...] = ("winter", "spring", "summer", "autumn")


@dataclass(frozen=True)
class RegimeSymbolSnapshot:
    symbol: str
    bars: int
    momentum_pct: float
    trend_pct: float
    volatility_pct: float
    drawdown_pct: float
    quote_volume_usdt: float
    heat_score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RegimeAssessment:
    season: str
    confidence: float
    heat_score: float
    interval: str
    bars_requested: int
    symbols: tuple[str, ...]
    created_at: str
    reasons: tuple[str, ...]
    snapshots: tuple[RegimeSymbolSnapshot, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["symbols"] = list(self.symbols)
        payload["reasons"] = list(self.reasons)
        payload["snapshots"] = [item.to_dict() for item in self.snapshots]
        return payload

    def render_markdown(self) -> str:
        lines = [
            "# Crypto Regime Assessment",
            "",
            f"- Season: {self.season}",
            f"- Confidence: {self.confidence:.2%}",
            f"- Heat score: {self.heat_score:.4f}",
            f"- Interval: {self.interval}",
            f"- Bars: {self.bars_requested}",
            f"- Symbols: {', '.join(self.symbols)}",
            "",
            "## Reasons",
            "",
        ]
        lines.extend(f"- {reason}" for reason in self.reasons)
        lines.extend(
            [
                "",
                "## Symbol Snapshots",
                "",
                "| Symbol | Bars | Momentum | Trend | Volatility | Drawdown | Heat |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for item in self.snapshots:
            lines.append(
                "| {symbol} | {bars} | {momentum:.2f}% | {trend:.2f}% | "
                "{volatility:.2f}% | {drawdown:.2f}% | {heat:.4f} |".format(
                    symbol=item.symbol,
                    bars=item.bars,
                    momentum=item.momentum_pct,
                    trend=item.trend_pct,
                    volatility=item.volatility_pct,
                    drawdown=item.drawdown_pct,
                    heat=item.heat_score,
                )
            )
        return "\n".join(lines).strip() + "\n"


class RegimeEngine:
    """Classify market water level without LLM discretion or order authority."""

    def __init__(self, config: CryptoTradingConfig | None = None, client=None):
        self.config = config or CryptoTradingConfig.from_env()
        self.client = client or HyperliquidClient(self.config)

    def assess(
        self,
        symbols: tuple[str, ...] | None = None,
        interval: str | None = None,
        bars: int | None = None,
    ) -> RegimeAssessment:
        selected = tuple(
            item.strip().upper()
            for item in (symbols or self.config.symbols)
            if item and item.strip()
        )
        if not selected:
            raise ValueError("regime assessment requires at least one symbol.")
        active_interval = interval or self.config.interval
        requested_bars = bars or max(96, self.config.lookback_limit)
        if requested_bars < 12:
            raise ValueError("regime assessment requires at least 12 bars.")

        snapshots: list[RegimeSymbolSnapshot] = []
        for symbol in selected:
            candles = self.client.get_klines(symbol, active_interval, requested_bars)
            snapshot = _symbol_snapshot(symbol, candles)
            if snapshot is not None:
                snapshots.append(snapshot)
        if not snapshots:
            raise ValueError("regime assessment received no usable candle snapshots.")

        heat_score = mean(item.heat_score for item in snapshots)
        averages = {
            "momentum_pct": mean(item.momentum_pct for item in snapshots),
            "trend_pct": mean(item.trend_pct for item in snapshots),
            "volatility_pct": mean(item.volatility_pct for item in snapshots),
            "drawdown_pct": mean(item.drawdown_pct for item in snapshots),
        }
        season, confidence, reasons = _classify_regime(heat_score, averages)
        return RegimeAssessment(
            season=season,
            confidence=confidence,
            heat_score=heat_score,
            interval=active_interval,
            bars_requested=requested_bars,
            symbols=selected,
            created_at=datetime.now(timezone.utc).isoformat(),
            reasons=reasons,
            snapshots=tuple(snapshots),
        )


def _symbol_snapshot(symbol: str, candles: list[Candle]) -> RegimeSymbolSnapshot | None:
    closes = [candle.close for candle in candles if candle.close > 0]
    if len(closes) < 2:
        return None
    first = closes[0]
    last = closes[-1]
    momentum_pct = ((last - first) / first) * 100 if first else 0.0
    ema_period = min(48, len(closes))
    ema_value = _ema(closes, ema_period)
    trend_pct = ((last - ema_value) / ema_value) * 100 if ema_value else 0.0
    returns = [
        ((closes[index] - closes[index - 1]) / closes[index - 1]) * 100
        for index in range(1, len(closes))
        if closes[index - 1] > 0
    ]
    volatility_pct = mean(abs(value) for value in returns) if returns else 0.0
    peak = max(closes)
    drawdown_pct = ((peak - last) / peak) * 100 if peak else 0.0
    quote_volume = sum(candle.volume * candle.close for candle in candles)
    heat_score = (
        (momentum_pct * 0.50)
        + (trend_pct * 0.35)
        + (min(volatility_pct * 3.0, 8.0) * 0.15)
        - (drawdown_pct * 0.25)
    )
    return RegimeSymbolSnapshot(
        symbol=symbol.strip().upper(),
        bars=len(closes),
        momentum_pct=momentum_pct,
        trend_pct=trend_pct,
        volatility_pct=volatility_pct,
        drawdown_pct=drawdown_pct,
        quote_volume_usdt=quote_volume,
        heat_score=heat_score,
    )


def _classify_regime(
    heat_score: float,
    averages: dict[str, float],
) -> tuple[str, float, tuple[str, ...]]:
    momentum = averages["momentum_pct"]
    trend = averages["trend_pct"]
    volatility = averages["volatility_pct"]
    drawdown = averages["drawdown_pct"]
    hard_winter = momentum <= -6.0 or trend <= -4.0 or drawdown >= 12.0
    still_water = abs(momentum) < 1.0 and abs(trend) < 0.75 and volatility < 0.20

    if hard_winter or still_water or heat_score < 1.0:
        season = "winter"
        boundary = 1.0
    elif heat_score < 4.0:
        season = "spring"
        boundary = 2.5
    elif heat_score < 8.0:
        season = "summer"
        boundary = 6.0
    else:
        season = "autumn"
        boundary = 8.0

    confidence = _clamp(0.55 + abs(heat_score - boundary) / 12.0, 0.55, 0.95)
    if hard_winter:
        confidence = max(confidence, 0.72)
    reasons = (
        f"avg_momentum_pct={momentum:.2f}",
        f"avg_trend_pct={trend:.2f}",
        f"avg_volatility_pct={volatility:.2f}",
        f"avg_drawdown_pct={drawdown:.2f}",
        f"heat_score={heat_score:.4f}",
        "rule=hard_winter" if hard_winter else "rule=heat_threshold",
    )
    return season, confidence, reasons


def _ema(values: list[float], period: int) -> float:
    alpha = 2 / (period + 1)
    value = values[0]
    for item in values[1:]:
        value = (item * alpha) + (value * (1 - alpha))
    return value


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
