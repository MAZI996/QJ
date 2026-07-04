"""Deterministic entry-quality filters for long-only crypto candidates."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .config import CryptoTradingConfig
from .indicators import atr, ema
from .models import Candle, OpportunitySignal


@dataclass(frozen=True)
class EntryQualityDecision:
    symbol: str
    approved: bool
    score: float
    close_position: float | None = None
    trend_efficiency: float | None = None
    ema_extension_pct: float | None = None
    chase_atr_multiple: float | None = None
    reasons: tuple[str, ...] = ()


class EntryQualityGate:
    """Demote fragile BUY setups before risk sizing.

    The goal is not to make the strategy more aggressive. It rejects common
    false-positive shapes: weak closes after a breakout, noisy chop, and entries
    stretched too far above the local trend anchor.
    """

    def __init__(self, config: CryptoTradingConfig):
        self.config = config

    def evaluate(
        self,
        candles: list[Candle],
        signal: OpportunitySignal,
    ) -> EntryQualityDecision:
        if not self.config.entry_quality_enabled:
            return EntryQualityDecision(
                symbol=signal.symbol,
                approved=True,
                score=1.0,
                reasons=("entry quality gate disabled",),
            )

        metrics = _entry_metrics(candles, signal)
        if signal.side != "BUY":
            return EntryQualityDecision(
                symbol=signal.symbol,
                approved=True,
                score=metrics.score,
                close_position=metrics.close_position,
                trend_efficiency=metrics.trend_efficiency,
                ema_extension_pct=metrics.ema_extension_pct,
                chase_atr_multiple=metrics.chase_atr_multiple,
                reasons=("entry quality is advisory for HOLD signals",),
            )

        rejected: list[str] = []
        notes: list[str] = []
        if metrics.close_position is None:
            rejected.append("latest candle range is unavailable")
        elif metrics.close_position < self.config.entry_quality_min_close_position:
            rejected.append(
                "close_position "
                f"{metrics.close_position:.2f} < "
                f"{self.config.entry_quality_min_close_position:.2f}"
            )
        else:
            notes.append(f"close_position {metrics.close_position:.2f}")

        if metrics.trend_efficiency is None:
            rejected.append("trend efficiency is unavailable")
        elif metrics.trend_efficiency < self.config.entry_quality_min_trend_efficiency:
            rejected.append(
                "trend_efficiency "
                f"{metrics.trend_efficiency:.2f} < "
                f"{self.config.entry_quality_min_trend_efficiency:.2f}"
            )
        else:
            notes.append(f"trend_efficiency {metrics.trend_efficiency:.2f}")

        if (
            metrics.ema_extension_pct is not None
            and metrics.ema_extension_pct > self.config.entry_quality_max_ema_extension_pct
        ):
            rejected.append(
                "ema_extension "
                f"{metrics.ema_extension_pct:.2%} > "
                f"{self.config.entry_quality_max_ema_extension_pct:.2%}"
            )
        elif metrics.ema_extension_pct is not None:
            notes.append(f"ema_extension {metrics.ema_extension_pct:.2%}")

        if (
            metrics.chase_atr_multiple is not None
            and metrics.chase_atr_multiple > self.config.entry_quality_max_chase_atr_multiple
        ):
            rejected.append(
                "chase_atr_multiple "
                f"{metrics.chase_atr_multiple:.2f} > "
                f"{self.config.entry_quality_max_chase_atr_multiple:.2f}"
            )
        elif metrics.chase_atr_multiple is not None:
            notes.append(f"chase_atr_multiple {metrics.chase_atr_multiple:.2f}")

        return EntryQualityDecision(
            symbol=signal.symbol,
            approved=not rejected,
            score=metrics.score,
            close_position=metrics.close_position,
            trend_efficiency=metrics.trend_efficiency,
            ema_extension_pct=metrics.ema_extension_pct,
            chase_atr_multiple=metrics.chase_atr_multiple,
            reasons=tuple(rejected or notes or ("entry quality metrics loaded",)),
        )

    def apply(
        self,
        candles: list[Candle],
        signal: OpportunitySignal,
    ) -> OpportunitySignal:
        decision = self.evaluate(candles, signal)
        metrics = dict(signal.metrics)
        metrics["entry_quality_score"] = decision.score
        if decision.close_position is not None:
            metrics["entry_close_position"] = decision.close_position
        if decision.trend_efficiency is not None:
            metrics["entry_trend_efficiency"] = decision.trend_efficiency
        if decision.ema_extension_pct is not None:
            metrics["entry_ema_extension_pct"] = decision.ema_extension_pct
        if decision.chase_atr_multiple is not None:
            metrics["entry_chase_atr_multiple"] = decision.chase_atr_multiple

        reasons = tuple(
            list(signal.reasons)
            + ["entry quality: " + "; ".join(decision.reasons)]
        )
        if decision.approved:
            return replace(signal, metrics=metrics, reasons=reasons)

        side = "HOLD" if signal.side == "BUY" else signal.side
        confidence = signal.confidence
        if signal.side == "BUY":
            confidence = min(signal.confidence, max(0.0, self.config.min_confidence - 0.03))
        return replace(signal, side=side, confidence=confidence, metrics=metrics, reasons=reasons)


@dataclass(frozen=True)
class _EntryMetrics:
    score: float
    close_position: float | None
    trend_efficiency: float | None
    ema_extension_pct: float | None
    chase_atr_multiple: float | None


def _entry_metrics(candles: list[Candle], signal: OpportunitySignal) -> _EntryMetrics:
    if not candles:
        return _EntryMetrics(0.0, None, None, None, None)

    latest = candles[-1]
    candle_range = latest.high - latest.low
    close_position = None
    if candle_range > 0:
        close_position = _clamp((latest.close - latest.low) / candle_range, 0.0, 1.0)

    closes = [candle.close for candle in candles if candle.close > 0]
    trend_efficiency = _trend_efficiency(closes[-20:])
    anchor_period = min(50, len(closes))
    ema_anchor = ema(closes, anchor_period) if anchor_period >= 2 else None
    ema_extension_pct = None
    chase_atr_multiple = None
    if ema_anchor and ema_anchor > 0:
        ema_extension_pct = max(0.0, (signal.entry_price - ema_anchor) / ema_anchor)
        atr_value = signal.metrics.get("atr_14") or atr(candles, 14) or 0.0
        if atr_value > 0:
            chase_atr_multiple = max(0.0, (signal.entry_price - ema_anchor) / atr_value)

    score = 1.0
    if close_position is not None:
        score -= max(0.0, 0.55 - close_position) * 0.80
    else:
        score -= 0.35
    if trend_efficiency is not None:
        score -= max(0.0, 0.35 - trend_efficiency) * 0.70
    else:
        score -= 0.25
    if ema_extension_pct is not None:
        score -= max(0.0, ema_extension_pct - 0.08) * 2.0
    if chase_atr_multiple is not None:
        score -= max(0.0, chase_atr_multiple - 8.0) * 0.03
    return _EntryMetrics(
        score=_clamp(score, 0.0, 1.0),
        close_position=close_position,
        trend_efficiency=trend_efficiency,
        ema_extension_pct=ema_extension_pct,
        chase_atr_multiple=chase_atr_multiple,
    )


def _trend_efficiency(closes: list[float]) -> float | None:
    if len(closes) < 3:
        return None
    path = sum(abs(current - previous) for previous, current in zip(closes[:-1], closes[1:]))
    if path <= 0:
        return 0.0
    return _clamp(abs(closes[-1] - closes[0]) / path, 0.0, 1.0)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
