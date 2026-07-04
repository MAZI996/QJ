from __future__ import annotations

from dataclasses import replace

from tradingagents.crypto.backtest import (
    BacktestCandidateRules,
    BacktestInventoryBridgeConfig,
    CryptoBacktester,
    CryptoBacktestSweepRunner,
)
from tradingagents.crypto.paper_queue import build_paper_queue_plan, paper_queue_output_paths
from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.models import Candle, SymbolRules, TickerSnapshot
from tradingagents.crypto.scanner import OpportunityScanner


class FakeBacktestClient:
    def __init__(self, candles: list[Candle], rules: SymbolRules | None = None):
        self.candles = candles
        self.rules = rules

    def get_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        return self.candles[-limit:]

    def get_symbol_rules(self, symbol: str) -> SymbolRules:
        if self.rules is not None:
            return self.rules
        return SymbolRules(
            symbol=symbol,
            base_asset=symbol,
            quote_asset="USDC",
            min_qty=0.00001,
            step_size=0.00001,
            min_notional=10.0,
        )


def test_crypto_backtest_replays_take_profit_trade():
    candles = _breakout_candles()
    config = replace(
        CryptoTradingConfig(),
        lookback_limit=60,
        min_confidence=0.60,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )

    report = CryptoBacktester(config, FakeBacktestClient(candles)).run(
        symbols=("BTC",),
        bars=len(candles),
        max_holding_bars=5,
        fee_bps=0,
        slippage_bps=0,
    )

    assert report.signals_evaluated > 0
    assert len(report.trades) == 1
    assert report.trades[0].outcome == "TAKE_PROFIT"
    assert report.total_pnl_usdt > 0


def test_entry_quality_keeps_clean_breakout_buy_signal():
    candles = _breakout_candles()[:61]
    config = replace(
        CryptoTradingConfig(),
        lookback_limit=60,
        min_confidence=0.60,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=False,
    )

    signal = OpportunityScanner(object(), config).evaluate_symbol(
        "BTC",
        candles,
        _ticker("BTC", candles),
    )

    assert signal.side == "BUY"
    assert signal.metrics["entry_quality_score"] > 0.50
    assert signal.metrics["entry_close_position"] >= config.entry_quality_min_close_position


def test_entry_quality_demotes_upper_wick_false_breakout():
    candles = _flat_candles(60)
    candles.append(
        _candle(
            60,
            open_price=101.18,
            close=102.0,
            high=112.0,
            low=101.9,
            volume=15000,
        )
    )
    config = replace(
        CryptoTradingConfig(),
        lookback_limit=60,
        min_confidence=0.60,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=False,
    )

    signal = OpportunityScanner(object(), config).evaluate_symbol(
        "BTC",
        candles,
        _ticker("BTC", candles),
    )

    assert signal.side == "HOLD"
    assert signal.metrics["entry_close_position"] < config.entry_quality_min_close_position
    assert any("entry quality:" in reason for reason in signal.reasons)


def test_crypto_backtest_inventory_bridge_unlocks_dead_inventory():
    candles = _breakout_candles()
    config = replace(
        CryptoTradingConfig(),
        lookback_limit=60,
        min_confidence=0.99,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )

    report = CryptoBacktester(config, FakeBacktestClient(candles)).run(
        symbols=("BTC",),
        bars=len(candles),
        max_holding_bars=5,
        fee_bps=0,
        slippage_bps=0,
        inventory_bridge=BacktestInventoryBridgeConfig(
            dead_reserve_ratio=0.20,
            max_dca_slices=4,
            unlock_acceleration_threshold=0.01,
            sell_ratio=1.0,
        ),
    )

    event_names = [event.event for event in report.inventory_events]
    bridge_trades = [trade for trade in report.trades if trade.strategy == "inventory_bridge"]

    assert "MACRO_DCA_BUY" in event_names
    assert "ACCELERATION_UNLOCK" in event_names
    assert "MICRO_SELL_FLOAT" in event_names
    assert len(bridge_trades) == 1
    assert bridge_trades[0].pnl_usdt > 0
    payload = report.to_dict()
    assert payload["inventory_events"][0]["event"] == "MACRO_DCA_BUY"
    assert payload["trades"][0]["strategy"] == "inventory_bridge"


def test_crypto_backtest_inventory_bridge_advances_during_scanner_hold():
    candles = _breakout_candles()
    config = replace(
        CryptoTradingConfig(),
        lookback_limit=60,
        min_confidence=0.60,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )

    report = CryptoBacktester(config, FakeBacktestClient(candles)).run(
        symbols=("BTC",),
        bars=len(candles),
        max_holding_bars=5,
        fee_bps=0,
        slippage_bps=0,
        inventory_bridge=BacktestInventoryBridgeConfig(
            dead_reserve_ratio=0.20,
            max_dca_slices=8,
            unlock_acceleration_threshold=999.0,
            sell_ratio=0.0,
            macro_tick_bars=1,
        ),
    )

    macro_buy_times = {
        event.time for event in report.inventory_events if event.event == "MACRO_DCA_BUY"
    }

    assert len([trade for trade in report.trades if trade.strategy != "inventory_bridge"]) == 1
    assert candles[61].close_time.isoformat() in macro_buy_times


def test_crypto_backtest_inventory_bridge_records_lot_size_rejections():
    candles = _breakout_candles()
    rules = SymbolRules(
        symbol="BTC",
        base_asset="BTC",
        quote_asset="USDC",
        min_qty=10.0,
        step_size=10.0,
        min_notional=10_000.0,
    )
    config = replace(
        CryptoTradingConfig(),
        lookback_limit=60,
        min_confidence=0.99,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )

    report = CryptoBacktester(config, FakeBacktestClient(candles, rules)).run(
        symbols=("BTC",),
        bars=len(candles),
        max_holding_bars=5,
        fee_bps=0,
        slippage_bps=0,
        inventory_bridge=BacktestInventoryBridgeConfig(
            dead_reserve_ratio=0.20,
            max_dca_slices=4,
            unlock_acceleration_threshold=999.0,
            sell_ratio=0.0,
        ),
    )

    assert any(event.event == "LOT_SIZE_REJECTED" for event in report.inventory_events)
    assert not any(event.event == "MACRO_DCA_BUY" for event in report.inventory_events)


def test_crypto_backtest_skips_insufficient_history():
    config = replace(CryptoTradingConfig(), lookback_limit=60)
    report = CryptoBacktester(config, FakeBacktestClient(_flat_candles(50))).run(
        symbols=("BTC",),
        bars=50,
    )

    assert report.signals_evaluated == 0
    assert report.trades == ()


def test_crypto_backtest_sweep_ranks_parameter_cases():
    candles = _breakout_candles()
    config = replace(
        CryptoTradingConfig(),
        lookback_limit=60,
        min_confidence=0.60,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )

    sweep = CryptoBacktestSweepRunner(config, FakeBacktestClient(candles)).run(
        symbols=("BTC",),
        intervals=("5m", "15m"),
        lookbacks=(60,),
        max_holding_bars_options=(3, 5),
        bars=len(candles),
        fee_bps=0,
        slippage_bps=0,
    )

    assert len(sweep.results) == 4
    assert sweep.best_result is not None
    assert sweep.ranked_results[0].risk_adjusted_score >= sweep.ranked_results[-1].risk_adjusted_score


def test_crypto_backtest_candidate_filters_gate_sweep_results():
    candles = _breakout_candles()
    config = replace(
        CryptoTradingConfig(),
        lookback_limit=60,
        min_confidence=0.60,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )
    sweep = CryptoBacktestSweepRunner(config, FakeBacktestClient(candles)).run(
        symbols=("BTC",),
        intervals=("15m",),
        lookbacks=(60,),
        max_holding_bars_options=(5,),
        bars=len(candles),
        fee_bps=0,
        slippage_bps=0,
    )

    loose_rules = BacktestCandidateRules(
        min_trades=1,
        min_win_rate=0.0,
        min_total_return_pct=-1.0,
        max_drawdown_pct=100.0,
        max_consecutive_losses=5,
    )
    strict_rules = BacktestCandidateRules(
        min_trades=2,
        min_win_rate=0.0,
        min_total_return_pct=-1.0,
        max_drawdown_pct=100.0,
        max_consecutive_losses=5,
    )

    assert sweep.best_candidate(loose_rules) is not None
    assert len(sweep.candidate_results(loose_rules)) == 1
    assert sweep.candidate_results(strict_rules) == ()
    decision = sweep.ranked_results[0].evaluate_candidate(strict_rules)
    assert not decision.approved
    assert decision.reasons[0].startswith("trades 1 < 2")


def test_paper_queue_plan_uses_only_backtest_candidates():
    candles = _breakout_candles()
    config = replace(
        CryptoTradingConfig(),
        lookback_limit=60,
        min_confidence=0.60,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )
    sweep = CryptoBacktestSweepRunner(config, FakeBacktestClient(candles)).run(
        symbols=("BTC",),
        intervals=("15m",),
        lookbacks=(60,),
        max_holding_bars_options=(5,),
        bars=len(candles),
        fee_bps=0,
        slippage_bps=0,
    )
    rules = BacktestCandidateRules(
        min_trades=1,
        min_win_rate=0.0,
        min_total_return_pct=-1.0,
        max_drawdown_pct=100.0,
        max_consecutive_losses=5,
    )

    plan = build_paper_queue_plan(sweep, rules, top=1, interval_seconds=60, cycles=2)

    assert plan.ready_count == 1
    assert plan.items[0].command.startswith("python -m cli.main crypto-autopilot")
    assert "--mode paper" in plan.items[0].command
    assert "--execute-top" in plan.items[0].command
    assert "--lookback 60" in plan.items[0].command
    assert "--interval 15m" in plan.items[0].command
    assert "--mode live" not in plan.items[0].command


def test_paper_queue_output_paths_use_base_name(tmp_path):
    json_path, markdown_path = paper_queue_output_paths(tmp_path / "paper_queue")

    assert json_path == tmp_path / "paper_queue.json"
    assert markdown_path == tmp_path / "paper_queue.md"


def test_paper_queue_deduplicates_equivalent_paper_commands():
    candles = _breakout_candles()
    config = replace(
        CryptoTradingConfig(),
        lookback_limit=60,
        min_confidence=0.60,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )
    sweep = CryptoBacktestSweepRunner(config, FakeBacktestClient(candles)).run(
        symbols=("BTC",),
        intervals=("15m",),
        lookbacks=(60,),
        max_holding_bars_options=(3, 5),
        bars=len(candles),
        fee_bps=0,
        slippage_bps=0,
    )
    rules = BacktestCandidateRules(
        min_trades=1,
        min_win_rate=0.0,
        min_total_return_pct=-1.0,
        max_drawdown_pct=100.0,
        max_consecutive_losses=5,
    )

    plan = build_paper_queue_plan(sweep, rules, top=3)

    assert plan.ready_count == 1


def _breakout_candles() -> list[Candle]:
    candles = _flat_candles(60)
    candles.append(_candle(60, open_price=101.18, close=102.0, high=102.3, low=101.0, volume=12000))
    candles.append(_candle(61, open_price=102.0, close=105.0, high=106.0, low=101.8, volume=11000))
    candles.extend(
        _candle(index, open_price=105.0, close=105.2, high=105.4, low=104.8, volume=5000)
        for index in range(62, 70)
    )
    return candles


def _flat_candles(count: int) -> list[Candle]:
    candles: list[Candle] = []
    for index in range(count):
        close = 100.0 + index * 0.02
        candles.append(
            _candle(
                index,
                open_price=close - 0.01,
                close=close,
                high=close + 0.05,
                low=close - 0.05,
                volume=5000,
            )
        )
    return candles


def _ticker(symbol: str, candles: list[Candle]) -> TickerSnapshot:
    first = candles[0].close
    last = candles[-1].close
    return TickerSnapshot(
        symbol=symbol,
        last_price=last,
        price_change_pct_24h=((last - first) / first) * 100 if first else 0.0,
        quote_volume_24h=sum(candle.volume * candle.close for candle in candles),
    )


def _candle(
    index: int,
    open_price: float,
    close: float,
    high: float,
    low: float,
    volume: float,
) -> Candle:
    open_time = index * 60_000
    return Candle(
        open_time_ms=open_time,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        close_time_ms=open_time + 59_999,
    )
