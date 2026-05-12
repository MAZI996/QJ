"""Strategy fusion layer inspired by high-signal open-source trading projects."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .config import CryptoTradingConfig
from .models import OpportunitySignal


@dataclass(frozen=True)
class StrategyReference:
    repo: str
    url: str
    family: str
    adopted_for: str
    license: str
    stars_snapshot: int


@dataclass(frozen=True)
class FusionContribution:
    name: str
    delta: float
    reason: str


HIGH_STAR_STRATEGY_REFERENCES: tuple[StrategyReference, ...] = (
    StrategyReference(
        repo="freqtrade/freqtrade",
        url="https://github.com/freqtrade/freqtrade",
        family="strategy lifecycle",
        adopted_for="dry-run, backtest-first workflow, dynamic whitelist, risk-aware strategy plugins",
        license="GPL-3.0",
        stars_snapshot=50222,
    ),
    StrategyReference(
        repo="freqtrade/freqtrade-strategies",
        url="https://github.com/freqtrade/freqtrade-strategies",
        family="community strategy library",
        adopted_for="indicator voting patterns and comparable static backtest discipline",
        license="GPL-3.0",
        stars_snapshot=5124,
    ),
    StrategyReference(
        repo="hummingbot/hummingbot",
        url="https://github.com/hummingbot/hummingbot",
        family="liquidity and market making",
        adopted_for="liquidity, spread, and execution-quality filters before placing orders",
        license="Apache-2.0",
        stars_snapshot=18529,
    ),
    StrategyReference(
        repo="mementum/backtrader",
        url="https://github.com/mementum/backtrader",
        family="backtesting",
        adopted_for="future backtest analyzers and run-level performance diagnostics",
        license="GPL-3.0",
        stars_snapshot=21511,
    ),
    StrategyReference(
        repo="QuantConnect/Lean",
        url="https://github.com/QuantConnect/Lean",
        family="algorithm engine",
        adopted_for="future brokerage abstraction and research/live separation patterns",
        license="Apache-2.0",
        stars_snapshot=18928,
    ),
    StrategyReference(
        repo="jesse-ai/jesse",
        url="https://github.com/jesse-ai/jesse",
        family="crypto strategy research",
        adopted_for="route-based strategy design, stop discipline, optimization, and live-trading separation",
        license="MIT",
        stars_snapshot=7867,
    ),
    StrategyReference(
        repo="microsoft/qlib",
        url="https://github.com/microsoft/qlib",
        family="AI quant research",
        adopted_for="future factor mining, model validation, and research-production separation",
        license="MIT",
        stars_snapshot=42664,
    ),
    StrategyReference(
        repo="AI4Finance-Foundation/FinRL",
        url="https://github.com/AI4Finance-Foundation/FinRL",
        family="reinforcement learning",
        adopted_for="future policy research only; no RL live authority until trained and benchmarked",
        license="MIT",
        stars_snapshot=15128,
    ),
    StrategyReference(
        repo="TA-Lib/ta-lib-python",
        url="https://github.com/TA-Lib/ta-lib-python",
        family="technical indicators",
        adopted_for="indicator vocabulary such as RSI, MACD, ADX, Bollinger Bands, and candlestick signals",
        license="BSD-2-Clause",
        stars_snapshot=11953,
    ),
    StrategyReference(
        repo="ccxt/ccxt",
        url="https://github.com/ccxt/ccxt",
        family="exchange abstraction",
        adopted_for="future multi-exchange adapter ideas while Binance remains the first live venue",
        license="MIT",
        stars_snapshot=42403,
    ),
    StrategyReference(
        repo="ccxt/binance-trade-bot",
        url="https://github.com/ccxt/binance-trade-bot",
        family="portfolio rotation",
        adopted_for="simple Binance rotation lessons and small-bot failure-mode review",
        license="GPL-3.0",
        stars_snapshot=8668,
    ),
    StrategyReference(
        repo="bukosabino/ta",
        url="https://github.com/bukosabino/ta",
        family="technical indicators",
        adopted_for="Pandas/Numpy indicator implementation ideas if TA-Lib is unavailable",
        license="MIT",
        stars_snapshot=5032,
    ),
    StrategyReference(
        repo="CyberPunkMetalHead/Binance-volatility-trading-bot",
        url="https://github.com/CyberPunkMetalHead/Binance-volatility-trading-bot",
        family="volatility rotation",
        adopted_for="high-volatility Binance rotation idea, adapted only with strict spot risk gates",
        license="MIT",
        stars_snapshot=3489,
    ),
)


class StrategyFusionEngine:
    """Blend strategy families into a conservative confidence adjustment."""

    def __init__(self, config: CryptoTradingConfig):
        self.config = config

    def fuse(self, signal: OpportunitySignal) -> OpportunitySignal:
        if not self.config.strategy_fusion_enabled:
            return signal

        contributions = self._contributions(signal)
        raw_delta = sum(item.delta for item in contributions)
        if self._score(contributions) < self.config.strategy_fusion_min_score:
            contributions.append(
                FusionContribution(
                    "cross_strategy_consensus",
                    -0.06,
                    "multi-strategy consensus is weak; demote instead of promoting a thin setup",
                )
            )
            raw_delta -= 0.06

        bounded_delta = max(-0.20, min(raw_delta, 0.14))
        fused_confidence = max(0.0, min(signal.confidence + bounded_delta, 0.95))
        fused_side = signal.side
        if signal.side == "BUY" and fused_confidence < 0.55:
            fused_side = "HOLD"

        metrics = dict(signal.metrics)
        metrics["fusion_delta"] = bounded_delta
        metrics["fusion_score"] = self._score(contributions)
        for item in contributions:
            metrics[f"fusion_{item.name}"] = item.delta

        reasons = tuple(
            list(signal.reasons)
            + [
                "strategy fusion: "
                + "; ".join(f"{item.name} {item.delta:+.2f}" for item in contributions)
            ]
        )
        return replace(
            signal,
            side=fused_side,
            confidence=fused_confidence,
            reasons=reasons,
            metrics=metrics,
        )

    def _contributions(self, signal: OpportunitySignal) -> list[FusionContribution]:
        return [
            self._freqtrade_style(signal),
            self._hummingbot_style(signal),
            self._jesse_style(signal),
            self._volatility_rotation(signal),
            self._ta_stack(signal),
            self._ai_research_guard(signal),
        ]

    def _freqtrade_style(self, signal: OpportunitySignal) -> FusionContribution:
        metrics = signal.metrics
        volume_ratio = metrics.get("volume_ratio_20", 0.0)
        change = metrics.get("change_pct_24h", 0.0)
        rr = signal.risk_reward or 0.0
        if volume_ratio >= 1.4 and 0 < change < 12 and rr >= 1.5:
            return FusionContribution(
                "freqtrade_style",
                0.04,
                "trend, volume, and reward/risk line up like a strategy-vote candidate",
            )
        if rr < 1.5:
            return FusionContribution(
                "freqtrade_style",
                -0.08,
                "reward/risk is below a basic strategy gate",
            )
        return FusionContribution("freqtrade_style", 0.00, "neutral strategy-vote evidence")

    def _hummingbot_style(self, signal: OpportunitySignal) -> FusionContribution:
        metrics = signal.metrics
        quote_volume = metrics.get("quote_volume_24h", 0.0)
        atr_pct = metrics.get("atr_pct", 0.0)
        if quote_volume < 5_000_000:
            return FusionContribution(
                "hummingbot_liquidity",
                -0.08,
                "liquidity is too thin for clean automated execution",
            )
        if quote_volume >= 20_000_000 and 0.002 <= atr_pct <= 0.05:
            return FusionContribution(
                "hummingbot_liquidity",
                0.03,
                "liquidity and volatility are within an execution-friendly zone",
            )
        if atr_pct > 0.08:
            return FusionContribution(
                "hummingbot_liquidity",
                -0.07,
                "volatility is too wide for a small personal account",
            )
        return FusionContribution("hummingbot_liquidity", 0.00, "neutral liquidity evidence")

    def _jesse_style(self, signal: OpportunitySignal) -> FusionContribution:
        rr = signal.risk_reward or 0.0
        if signal.stop_loss is None or signal.take_profit is None:
            return FusionContribution(
                "jesse_route_risk",
                -0.12,
                "route has no complete stop/take-profit structure",
            )
        if rr >= 2.0:
            return FusionContribution(
                "jesse_route_risk",
                0.04,
                "route has explicit stop, take-profit, and at least 2R target",
            )
        return FusionContribution(
            "jesse_route_risk",
            0.00,
            "route risk is complete but not strong enough to add confidence",
        )

    def _volatility_rotation(self, signal: OpportunitySignal) -> FusionContribution:
        metrics = signal.metrics
        change = metrics.get("change_pct_24h", 0.0)
        volume_ratio = metrics.get("volume_ratio_20", 0.0)
        if change > 25:
            return FusionContribution(
                "volatility_rotation",
                -0.10,
                "price move is too extended for conservative spot chasing",
            )
        if 3 <= change <= 18 and volume_ratio >= 1.4:
            return FusionContribution(
                "volatility_rotation",
                0.04,
                "hot mover has enough volume expansion without being extremely extended",
            )
        return FusionContribution(
            "volatility_rotation",
            0.00,
            "neutral volatility-rotation evidence",
        )

    def _ta_stack(self, signal: OpportunitySignal) -> FusionContribution:
        metrics = signal.metrics
        rsi = metrics.get("rsi_14", 0.0)
        ema_12 = metrics.get("ema_12", 0.0)
        ema_26 = metrics.get("ema_26", 0.0)
        ema_50 = metrics.get("ema_50", 0.0)
        last = metrics.get("last_price", signal.entry_price)
        if rsi > 78:
            return FusionContribution(
                "ta_indicator_stack",
                -0.07,
                "RSI is overheated",
            )
        if 45 <= rsi <= 70 and last > ema_50 and ema_12 > ema_26:
            return FusionContribution(
                "ta_indicator_stack",
                0.03,
                "RSI and EMA stack support a spot trend continuation setup",
            )
        return FusionContribution("ta_indicator_stack", 0.00, "neutral indicator stack")

    def _ai_research_guard(self, signal: OpportunitySignal) -> FusionContribution:
        return FusionContribution(
            "ai_research_guard",
            0.00,
            "Qlib/FinRL-style ML and RL remain advisory until trained, backtested, and journaled",
        )

    @staticmethod
    def _score(contributions: list[FusionContribution]) -> float:
        positive = sum(item.delta for item in contributions if item.delta > 0)
        negative = abs(sum(item.delta for item in contributions if item.delta < 0))
        return max(0.0, min(1.0, 0.50 + positive - negative))
