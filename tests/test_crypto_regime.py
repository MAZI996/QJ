from __future__ import annotations

from dataclasses import replace

from tradingagents.crypto import CryptoTradingConfig, RegimeEngine
from tradingagents.crypto.models import Candle


class FakeRegimeClient:
    def __init__(self, candles_by_symbol: dict[str, list[Candle]]):
        self.candles_by_symbol = candles_by_symbol
        self.calls: list[tuple[str, str, int]] = []

    def get_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        self.calls.append((symbol, interval, limit))
        return self.candles_by_symbol[symbol][-limit:]


def test_regime_engine_classifies_still_water_as_winter():
    candles = [_candle(index, 100.0 + ((index % 2) * 0.01)) for index in range(96)]
    client = FakeRegimeClient({"BTC": candles})
    config = replace(CryptoTradingConfig(), interval="1h", symbols=("BTC",))

    assessment = RegimeEngine(config, client).assess(bars=96)

    assert assessment.season == "winter"
    assert assessment.confidence >= 0.55
    assert assessment.snapshots[0].symbol == "BTC"
    assert "heat_score" in assessment.to_dict()
    assert "# Crypto Regime Assessment" in assessment.render_markdown()
    assert client.calls == [("BTC", "1h", 96)]


def test_regime_engine_classifies_strong_breakout_as_autumn():
    candles = [_candle(index, 100.0 + (index * 0.35)) for index in range(96)]
    client = FakeRegimeClient({"BTC": candles, "ETH": candles})
    config = replace(CryptoTradingConfig(), interval="1h", symbols=("BTC", "ETH"))

    assessment = RegimeEngine(config, client).assess(interval="1h", bars=96)

    assert assessment.season == "autumn"
    assert assessment.heat_score >= 8.0
    assert {snapshot.symbol for snapshot in assessment.snapshots} == {"BTC", "ETH"}


def _candle(index: int, close: float) -> Candle:
    open_time = index * 3_600_000
    return Candle(
        open_time_ms=open_time,
        open=close,
        high=close * 1.002,
        low=close * 0.998,
        close=close,
        volume=1000.0,
        close_time_ms=open_time + 3_599_999,
    )
