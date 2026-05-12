"""Rule-first opportunity scanner for Binance spot symbols."""

from __future__ import annotations

from .binance_client import BinanceAPIError, BinanceClient
from .config import CryptoTradingConfig
from .hotlist import load_hot_symbols
from .indicators import atr, ema, rsi, sma
from .lana_strategy import LanaInspiredStrategy
from .models import Candle, OpportunitySignal, TickerSnapshot


class OpportunityScanner:
    """Find spot long candidates before any AI or execution layer is involved."""

    def __init__(self, client: BinanceClient, config: CryptoTradingConfig):
        self.client = client
        self.config = config
        self.lana_strategy = LanaInspiredStrategy(config)

    def scan(self, symbols: tuple[str, ...] | None = None) -> list[OpportunitySignal]:
        selected = self._selected_symbols(symbols)
        hot_symbols = load_hot_symbols(self.config)
        signals: list[OpportunitySignal] = []
        for symbol in selected:
            candles = self.client.get_klines(symbol, self.config.interval, self.config.lookback_limit)
            ticker = self.client.get_24h_ticker(symbol)
            baseline = self.evaluate_symbol(symbol, candles, ticker)
            lana_signal = None
            if self.config.lana_strategy_enabled:
                try:
                    open_interest = self.client.get_open_interest_history(
                        symbol,
                        self.config.lana_oi_lookback,
                        self.config.lana_oi_limit,
                    )
                except BinanceAPIError:
                    open_interest = []
                lana_signal = self.lana_strategy.evaluate(
                    symbol,
                    candles,
                    ticker,
                    open_interest,
                    hot_symbols=hot_symbols,
                )
            signals.append(self._choose_signal(baseline, lana_signal))
        return sorted(signals, key=lambda signal: signal.confidence, reverse=True)

    def evaluate_symbol(
        self,
        symbol: str,
        candles: list[Candle],
        ticker: TickerSnapshot,
    ) -> OpportunitySignal:
        if len(candles) < 60:
            return OpportunitySignal(
                symbol=symbol,
                side="HOLD",
                confidence=0.0,
                entry_price=ticker.last_price,
                stop_loss=None,
                take_profit=None,
                timeframe=self.config.interval,
                strategy="insufficient-data",
                reasons=("K线数量不足，暂不交易",),
            )

        closes = [candle.close for candle in candles]
        highs = [candle.high for candle in candles]
        volumes = [candle.volume for candle in candles]
        last = closes[-1]
        atr_value = atr(candles, 14)
        ema_fast = ema(closes, 12)
        ema_slow = ema(closes, 26)
        ema_trend = ema(closes, 50)
        rsi_value = rsi(closes, 14)
        avg_volume_20 = sma(volumes, 20) or 0.0
        prior_high_20 = max(highs[-21:-1])
        atr_pct = (atr_value / last) if atr_value else 0.0

        score = 0.0
        reasons: list[str] = []

        if ema_fast and ema_slow and ema_trend and last > ema_trend and ema_fast > ema_slow:
            score += 0.25
            reasons.append("短期均线位于长期均线上方，趋势偏多")
        elif ema_trend and last < ema_trend:
            reasons.append("价格低于中期趋势线，趋势条件不足")

        if last > prior_high_20:
            score += 0.20
            reasons.append("价格突破近20根K线高点")
        else:
            distance = ((prior_high_20 - last) / last) * 100
            reasons.append(f"距离近20根K线高点约 {distance:.2f}%")

        if avg_volume_20 > 0 and volumes[-1] > avg_volume_20 * 1.5:
            score += 0.15
            reasons.append("最新成交量明显放大")

        if rsi_value is not None and 45 <= rsi_value <= 70:
            score += 0.12
            reasons.append("RSI处于可追踪但未明显过热区域")
        elif rsi_value is not None and rsi_value > 78:
            score -= 0.08
            reasons.append("RSI过热，追高风险较大")

        if 0 < ticker.price_change_pct_24h < 12:
            score += 0.10
            reasons.append("24小时涨幅为正且未极端拉升")
        elif ticker.price_change_pct_24h <= 0:
            reasons.append("24小时动量未转正")

        if 0.002 <= atr_pct <= 0.05:
            score += 0.10
            reasons.append("波动率足够形成交易空间")
        elif atr_pct > 0.08:
            score -= 0.10
            reasons.append("波动率过高，容易触发止损")

        if ticker.quote_volume_24h >= 10_000_000:
            score += 0.08
            reasons.append("24小时成交额较高，流动性较好")

        confidence = max(0.0, min(score, 0.95))
        side = "BUY" if confidence >= 0.55 else "HOLD"
        stop_loss = None
        take_profit = None
        if side == "BUY" and atr_value:
            stop_loss = max(last - (atr_value * 1.6), last * 0.985)
            take_profit = last + (last - stop_loss) * 2.0

        metrics = {
            "last_price": last,
            "ema_12": ema_fast or 0.0,
            "ema_26": ema_slow or 0.0,
            "ema_50": ema_trend or 0.0,
            "rsi_14": rsi_value or 0.0,
            "atr_14": atr_value or 0.0,
            "atr_pct": atr_pct,
            "volume_ratio_20": (volumes[-1] / avg_volume_20) if avg_volume_20 else 0.0,
            "change_pct_24h": ticker.price_change_pct_24h,
            "quote_volume_24h": ticker.quote_volume_24h,
        }
        return OpportunitySignal(
            symbol=symbol,
            side=side,
            confidence=confidence,
            entry_price=last,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timeframe=self.config.interval,
            strategy="spot-trend-breakout-v1",
            reasons=tuple(reasons),
            metrics=metrics,
        )

    @staticmethod
    def _choose_signal(
        baseline: OpportunitySignal,
        lana_signal: OpportunitySignal | None,
    ) -> OpportunitySignal:
        if lana_signal is None:
            return baseline
        if lana_signal.side == "BUY" and baseline.side != "BUY":
            return lana_signal
        if lana_signal.confidence > baseline.confidence:
            return lana_signal
        return baseline

    def _selected_symbols(self, symbols: tuple[str, ...] | None) -> tuple[str, ...]:
        selected = list(symbols or self.config.symbols)
        for symbol in load_hot_symbols(self.config):
            if symbol not in selected:
                selected.append(symbol)
        return tuple(selected)
