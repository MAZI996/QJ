"""Lana-inspired crypto opportunity filter.

The referenced post describes a high-risk attention strategy: scrape high
traffic/high-post-volume Binance Square coins, then pick volatile gainers and
place a stop immediately. This module adapts that idea into a spot-only signal
layer. Open interest is an optional extra filter from our system design, not a
required part of the original post.
"""

from __future__ import annotations

from .config import CryptoTradingConfig
from .indicators import atr, sma
from .models import Candle, OpenInterestPoint, OpportunitySignal, TickerSnapshot


class LanaInspiredStrategy:
    def __init__(self, config: CryptoTradingConfig):
        self.config = config

    def evaluate(
        self,
        symbol: str,
        candles: list[Candle],
        ticker: TickerSnapshot,
        open_interest: list[OpenInterestPoint],
        hot_symbols: tuple[str, ...] | None = None,
    ) -> OpportunitySignal | None:
        if not self.config.lana_strategy_enabled or len(candles) < 30:
            return None

        closes = [candle.close for candle in candles]
        volumes = [candle.volume for candle in candles]
        last = closes[-1]
        avg_volume_20 = sma(volumes, 20) or 0.0
        volume_ratio = (volumes[-1] / avg_volume_20) if avg_volume_20 else 0.0
        atr_value = atr(candles, 14) or 0.0
        atr_pct = atr_value / last if last else 0.0
        oi_change_pct = self._oi_change_pct(open_interest)
        hot_symbol_set = set(hot_symbols or self.config.lana_hot_symbols)

        score = 0.0
        reasons: list[str] = []

        if hot_symbol_set:
            if symbol.upper() in hot_symbol_set:
                score += 0.18
                reasons.append("进入人工/舆情热度名单，允许继续追踪")
            else:
                score -= 0.10
                reasons.append("未进入热度名单，只作为行情异动候选")
        else:
            reasons.append("暂未配置热度名单，跳过舆情前置筛选")

        change_pct = ticker.price_change_pct_24h
        if self.config.lana_min_price_change_pct <= change_pct <= self.config.lana_max_price_change_pct:
            score += 0.22
            reasons.append("24小时涨幅进入策略观察区间")
        elif change_pct > self.config.lana_max_price_change_pct:
            score -= 0.12
            reasons.append("24小时涨幅过高，可能已经进入追高区")
        else:
            reasons.append("24小时涨幅不足，暂未形成强势异动")

        if ticker.quote_volume_24h >= self.config.lana_min_quote_volume_usdt:
            score += 0.12
            reasons.append("24小时成交额满足流动性要求")
        else:
            reasons.append("成交额不足，滑点和假突破风险较高")

        if volume_ratio >= self.config.lana_min_volume_ratio:
            score += 0.14
            reasons.append("最新成交量相对20期均量放大")

        if oi_change_pct is not None and oi_change_pct >= self.config.lana_min_oi_change_pct:
            score += 0.12
            reasons.append("可选OI过滤通过，衍生品仓位活跃度增强")
        elif oi_change_pct is None:
            reasons.append("未取得OI数据，跳过可选仓位活跃度过滤")
        else:
            reasons.append("OI变化不足，仅按热度和波动处理")

        if 0.003 <= atr_pct <= 0.07:
            score += 0.10
            reasons.append("波动率足够交易但未极端失控")
        elif atr_pct > 0.10:
            score -= 0.15
            reasons.append("波动率过高，固定止损容易被扫")

        confidence = max(0.0, min(score, 0.92))
        side = "BUY" if confidence >= 0.58 else "HOLD"
        stop_loss = None
        take_profit = None
        if side == "BUY":
            stop_loss = last * (1 - self.config.lana_fixed_stop_loss_pct)
            risk = last - stop_loss
            take_profit = last + risk * self.config.lana_take_profit_r_multiple

        metrics = {
            "last_price": last,
            "change_pct_24h": change_pct,
            "quote_volume_24h": ticker.quote_volume_24h,
            "volume_ratio_20": volume_ratio,
            "atr_14": atr_value,
            "atr_pct": atr_pct,
            "oi_change_pct": oi_change_pct or 0.0,
            "fixed_stop_loss_pct": self.config.lana_fixed_stop_loss_pct,
        }
        return OpportunitySignal(
            symbol=symbol,
            side=side,
            confidence=confidence,
            entry_price=last,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timeframe=self.config.interval,
            strategy="lana-inspired-attention-oi-v1",
            reasons=tuple(reasons),
            metrics=metrics,
        )

    @staticmethod
    def _oi_change_pct(points: list[OpenInterestPoint]) -> float | None:
        if len(points) < 2:
            return None
        first = points[0].open_interest
        last = points[-1].open_interest
        if first <= 0:
            return None
        return ((last - first) / first) * 100
