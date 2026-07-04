"""Historical candle replay for the Hyperliquid crypto workflow."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace

from .config import CryptoTradingConfig
from .hyperliquid_client import HyperliquidClient
from .models import Candle, SymbolRules, TickerSnapshot
from .risk import RiskManager
from .scanner import OpportunityScanner
from .strategy_fusion import StrategyFusionEngine


@dataclass(frozen=True)
class BacktestTrade:
    symbol: str
    strategy: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: float
    notional_usdt: float
    pnl_usdt: float
    pnl_pct: float
    outcome: str
    holding_bars: int
    confidence: float
    risk_reward: float | None
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BacktestInventoryEvent:
    symbol: str
    time: str
    event: str
    price: float
    quantity: float
    dead_quantity: float
    float_quantity: float
    quote_balance_usdt: float
    cost_usdt: float
    realized_pnl_usdt: float
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BacktestInventoryBridgeConfig:
    dead_reserve_ratio: float = 0.20
    max_dca_slices: int = 12
    unlock_acceleration_threshold: float = 0.002
    sell_ratio: float = 1.0
    macro_tick_bars: int = 0
    ema_anchor_bars: int = 50
    beta_threshold: float = 0.0
    moon_phase_pressure: float = 0.0
    deadline_force_pct: float = 0.0
    gc_threshold_bars: int = 0
    gc_max_ratio: float = 0.0


@dataclass(frozen=True)
class BacktestReport:
    symbols: tuple[str, ...]
    interval: str
    bars_requested: int
    lookback_limit: int
    max_holding_bars: int
    fee_bps: float
    slippage_bps: float
    signals_evaluated: int
    risk_rejected: int
    trades: tuple[BacktestTrade, ...]
    total_pnl_usdt: float
    ending_equity_usdt: float
    total_return_pct: float
    max_drawdown_pct: float
    inventory_events: tuple[BacktestInventoryEvent, ...] = ()

    @property
    def wins(self) -> int:
        return sum(1 for trade in self.trades if trade.pnl_usdt > 0)

    @property
    def losses(self) -> int:
        return sum(1 for trade in self.trades if trade.pnl_usdt <= 0)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return self.wins / len(self.trades)

    @property
    def max_consecutive_losses(self) -> int:
        current = 0
        worst = 0
        for trade in self.trades:
            if trade.pnl_usdt <= 0:
                current += 1
                worst = max(worst, current)
            else:
                current = 0
        return worst

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["trades"] = [trade.to_dict() for trade in self.trades]
        payload["inventory_events"] = [event.to_dict() for event in self.inventory_events]
        payload["wins"] = self.wins
        payload["losses"] = self.losses
        payload["win_rate"] = self.win_rate
        payload["max_consecutive_losses"] = self.max_consecutive_losses
        return payload


@dataclass(frozen=True)
class BacktestCandidateRules:
    min_trades: int = 5
    min_win_rate: float = 0.40
    min_total_return_pct: float = 0.0
    max_drawdown_pct: float = 5.0
    max_consecutive_losses: int = 3


@dataclass(frozen=True)
class BacktestCandidateDecision:
    approved: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class BacktestSweepCase:
    interval: str
    lookback_limit: int
    max_holding_bars: int
    fee_bps: float
    slippage_bps: float
    fusion_enabled: bool
    lana_enabled: bool


@dataclass(frozen=True)
class BacktestSweepResult:
    case: BacktestSweepCase
    report: BacktestReport

    @property
    def risk_adjusted_score(self) -> float:
        if not self.report.trades:
            return -1000.0
        activity_factor = min(1.0, len(self.report.trades) / 5)
        return (self.report.total_return_pct - self.report.max_drawdown_pct) * activity_factor

    def evaluate_candidate(
        self,
        rules: BacktestCandidateRules | None = None,
    ) -> BacktestCandidateDecision:
        active_rules = rules or BacktestCandidateRules()
        report = self.report
        reasons: list[str] = []
        if len(report.trades) < active_rules.min_trades:
            reasons.append(f"trades {len(report.trades)} < {active_rules.min_trades}")
        if report.win_rate < active_rules.min_win_rate:
            reasons.append(f"win_rate {report.win_rate:.2%} < {active_rules.min_win_rate:.2%}")
        if report.total_return_pct < active_rules.min_total_return_pct:
            reasons.append(
                f"return {report.total_return_pct:.2f}% < {active_rules.min_total_return_pct:.2f}%"
            )
        if report.max_drawdown_pct > active_rules.max_drawdown_pct:
            reasons.append(
                f"drawdown {report.max_drawdown_pct:.2f}% > {active_rules.max_drawdown_pct:.2f}%"
            )
        if report.max_consecutive_losses > active_rules.max_consecutive_losses:
            reasons.append(
                "loss_streak "
                f"{report.max_consecutive_losses} > {active_rules.max_consecutive_losses}"
            )
        if not reasons:
            return BacktestCandidateDecision(True, ("passed candidate filters",))
        return BacktestCandidateDecision(False, tuple(reasons))


@dataclass(frozen=True)
class BacktestSweepReport:
    symbols: tuple[str, ...]
    bars_requested: int
    results: tuple[BacktestSweepResult, ...]

    @property
    def ranked_results(self) -> tuple[BacktestSweepResult, ...]:
        return tuple(
            sorted(
                self.results,
                key=lambda result: (
                    result.risk_adjusted_score,
                    result.report.total_return_pct,
                    result.report.win_rate,
                ),
                reverse=True,
            )
        )

    @property
    def best_result(self) -> BacktestSweepResult | None:
        ranked = self.ranked_results
        return ranked[0] if ranked else None

    def candidate_results(
        self,
        rules: BacktestCandidateRules | None = None,
    ) -> tuple[BacktestSweepResult, ...]:
        active_rules = rules or BacktestCandidateRules()
        return tuple(
            result
            for result in self.ranked_results
            if result.evaluate_candidate(active_rules).approved
        )

    def best_candidate(
        self,
        rules: BacktestCandidateRules | None = None,
    ) -> BacktestSweepResult | None:
        candidates = self.candidate_results(rules)
        return candidates[0] if candidates else None


class CryptoBacktester:
    """Replay historical candles through scanner, fusion, and risk gates."""

    def __init__(
        self,
        config: CryptoTradingConfig | None = None,
        client=None,
    ):
        self.config = config or CryptoTradingConfig.from_env()
        self.client = client or HyperliquidClient(self.config)
        self.scanner = OpportunityScanner(self.client, self.config)
        self.fusion = StrategyFusionEngine(self.config)
        self.risk = RiskManager(self.config)

    def run(
        self,
        symbols: tuple[str, ...] | None = None,
        bars: int = 500,
        max_holding_bars: int = 32,
        fee_bps: float = 4.0,
        slippage_bps: float = 2.0,
        inventory_bridge: BacktestInventoryBridgeConfig | None = None,
    ) -> BacktestReport:
        selected = tuple(symbols or self.config.symbols)
        all_trades: list[BacktestTrade] = []
        all_inventory_events: list[BacktestInventoryEvent] = []
        signals_evaluated = 0
        risk_rejected = 0

        for symbol in selected:
            candles = self.client.get_klines(symbol, self.config.interval, bars)
            rules = self._safe_symbol_rules(symbol)
            symbol_result = self._run_symbol(
                symbol=symbol,
                candles=candles,
                rules=rules,
                max_holding_bars=max_holding_bars,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                inventory_bridge=inventory_bridge,
            )
            all_trades.extend(symbol_result.trades)
            all_inventory_events.extend(symbol_result.inventory_events)
            signals_evaluated += symbol_result.signals_evaluated
            risk_rejected += symbol_result.risk_rejected

        all_trades.sort(key=lambda trade: trade.entry_time)
        all_inventory_events.sort(key=lambda event: event.time)
        total_pnl = sum(trade.pnl_usdt for trade in all_trades)
        starting_equity = self.config.account_equity_usdt
        ending_equity = starting_equity + total_pnl
        total_return = (total_pnl / starting_equity) * 100 if starting_equity else 0.0
        max_drawdown = _max_drawdown_pct(starting_equity, all_trades)
        return BacktestReport(
            symbols=selected,
            interval=self.config.interval,
            bars_requested=bars,
            lookback_limit=self.config.lookback_limit,
            max_holding_bars=max_holding_bars,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            signals_evaluated=signals_evaluated,
            risk_rejected=risk_rejected,
            trades=tuple(all_trades),
            total_pnl_usdt=total_pnl,
            ending_equity_usdt=ending_equity,
            total_return_pct=total_return,
            max_drawdown_pct=max_drawdown,
            inventory_events=tuple(all_inventory_events),
        )

    def _run_symbol(
        self,
        symbol: str,
        candles: list[Candle],
        rules: SymbolRules | None,
        max_holding_bars: int,
        fee_bps: float,
        slippage_bps: float,
        inventory_bridge: BacktestInventoryBridgeConfig | None,
    ) -> BacktestReport:
        trades: list[BacktestTrade] = []
        inventory_events: list[BacktestInventoryEvent] = []
        signals_evaluated = 0
        risk_rejected = 0
        index = max(self.config.lookback_limit, 60)
        bridge = None
        next_dca_index = index
        dca_interval = 1
        if inventory_bridge is not None:
            bridge = _InventoryBridge(
                symbol=symbol,
                starting_equity=self.config.account_equity_usdt,
                config=inventory_bridge,
                rules=rules,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            )
            dca_interval = max(1, (len(candles) - index) // max(1, bridge.config.max_dca_slices))
            if bridge.config.macro_tick_bars > 0:
                dca_interval = bridge.config.macro_tick_bars

        def advance_inventory_clock(cursor: int) -> None:
            nonlocal next_dca_index
            if bridge is None:
                return
            while next_dca_index <= cursor and next_dca_index < len(candles):
                event = bridge.macro_buy(candles, next_dca_index)
                if event is not None:
                    inventory_events.append(event)
                next_dca_index += dca_interval
            bridge_trade, events = bridge.maybe_unlock_and_sell(candles, cursor)
            inventory_events.extend(events)
            if bridge_trade is not None:
                trades.append(bridge_trade)

        while index < len(candles) - 1:
            advance_inventory_clock(index)

            window_start = max(0, index - self.config.lookback_limit + 1)
            window = candles[window_start : index + 1]
            ticker = _ticker_from_window(symbol, window, self.config.interval)
            signal = self.scanner.evaluate_symbol(symbol, window, ticker)
            if self.config.strategy_fusion_enabled:
                signal = self.fusion.fuse(signal)
            signals_evaluated += 1

            if signal.side != "BUY":
                index += 1
                continue

            decision = self.risk.evaluate(signal, rules)
            if not decision.approved or decision.intent is None:
                risk_rejected += 1
                index += 1
                continue

            trade, exit_index = _simulate_trade(
                candles=candles,
                signal_index=index,
                symbol=symbol,
                strategy=signal.strategy,
                confidence=signal.confidence,
                risk_reward=signal.risk_reward,
                quantity=decision.intent.quantity,
                notional=decision.intent.notional_usdt,
                stop_loss=decision.intent.stop_loss,
                take_profit=decision.intent.take_profit,
                max_holding_bars=max_holding_bars,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                reason="; ".join(signal.reasons[:3]),
            )
            trades.append(trade)
            if bridge is not None and exit_index > index:
                for cursor in range(index + 1, min(exit_index, len(candles) - 2) + 1):
                    advance_inventory_clock(cursor)
            index = max(exit_index + 1, index + 1)

        trades.sort(key=lambda trade: trade.entry_time)
        total_pnl = sum(trade.pnl_usdt for trade in trades)
        starting_equity = self.config.account_equity_usdt
        return BacktestReport(
            symbols=(symbol,),
            interval=self.config.interval,
            bars_requested=len(candles),
            lookback_limit=self.config.lookback_limit,
            max_holding_bars=max_holding_bars,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            signals_evaluated=signals_evaluated,
            risk_rejected=risk_rejected,
            trades=tuple(trades),
            total_pnl_usdt=total_pnl,
            ending_equity_usdt=starting_equity + total_pnl,
            total_return_pct=(total_pnl / starting_equity) * 100 if starting_equity else 0.0,
            max_drawdown_pct=_max_drawdown_pct(starting_equity, trades),
            inventory_events=tuple(inventory_events),
        )

    def _safe_symbol_rules(self, symbol: str) -> SymbolRules | None:
        try:
            return self.client.get_symbol_rules(symbol)
        except Exception:
            return None


class CryptoBacktestSweepRunner:
    """Run a conservative parameter sweep over the Hyperliquid replay layer."""

    def __init__(
        self,
        config: CryptoTradingConfig | None = None,
        client=None,
    ):
        self.config = config or CryptoTradingConfig.from_env()
        self.client = client or HyperliquidClient(self.config)

    def run(
        self,
        symbols: tuple[str, ...] | None = None,
        intervals: tuple[str, ...] = ("5m", "15m", "1h"),
        lookbacks: tuple[int, ...] = (60, 120),
        max_holding_bars_options: tuple[int, ...] = (16, 32, 48),
        bars: int = 500,
        fee_bps: float = 4.0,
        slippage_bps: float = 2.0,
        fusion_enabled: bool = True,
        lana_enabled: bool = False,
    ) -> BacktestSweepReport:
        selected = tuple(symbols or self.config.symbols)
        results: list[BacktestSweepResult] = []

        for interval in intervals:
            for lookback in lookbacks:
                case_config = replace(
                    self.config,
                    interval=interval,
                    lookback_limit=lookback,
                    strategy_fusion_enabled=fusion_enabled,
                    lana_strategy_enabled=lana_enabled,
                    hotlist_enabled=False,
                )
                backtester = CryptoBacktester(case_config, self.client)
                for max_holding_bars in max_holding_bars_options:
                    case = BacktestSweepCase(
                        interval=interval,
                        lookback_limit=lookback,
                        max_holding_bars=max_holding_bars,
                        fee_bps=fee_bps,
                        slippage_bps=slippage_bps,
                        fusion_enabled=fusion_enabled,
                        lana_enabled=lana_enabled,
                    )
                    report = backtester.run(
                        symbols=selected,
                        bars=bars,
                        max_holding_bars=max_holding_bars,
                        fee_bps=fee_bps,
                        slippage_bps=slippage_bps,
                    )
                    results.append(BacktestSweepResult(case=case, report=report))

        return BacktestSweepReport(
            symbols=selected,
            bars_requested=bars,
            results=tuple(results),
        )


@dataclass
class _InventoryLot:
    quantity: float
    cost_usdt: float
    entry_time: str
    entry_index: int
    entry_price: float


class _InventoryBridge:
    def __init__(
        self,
        symbol: str,
        starting_equity: float,
        config: BacktestInventoryBridgeConfig,
        rules: SymbolRules | None,
        fee_bps: float,
        slippage_bps: float,
    ):
        self.symbol = symbol
        self.config = BacktestInventoryBridgeConfig(
            dead_reserve_ratio=_clamp(config.dead_reserve_ratio, 0.0, 0.95),
            max_dca_slices=max(1, int(config.max_dca_slices)),
            unlock_acceleration_threshold=max(0.0, config.unlock_acceleration_threshold),
            sell_ratio=_clamp(config.sell_ratio, 0.0, 1.0),
            macro_tick_bars=max(0, int(config.macro_tick_bars)),
            ema_anchor_bars=max(2, int(config.ema_anchor_bars)),
            beta_threshold=max(0.0, config.beta_threshold),
            moon_phase_pressure=max(0.0, config.moon_phase_pressure),
            deadline_force_pct=_clamp(config.deadline_force_pct, 0.0, 1.0),
            gc_threshold_bars=max(0, int(config.gc_threshold_bars)),
            gc_max_ratio=_clamp(config.gc_max_ratio, 0.0, 1.0),
        )
        self.quote_balance_usdt = starting_equity * self.config.dead_reserve_ratio
        self.slice_budget_usdt = self.quote_balance_usdt / self.config.max_dca_slices
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps
        self.rules = rules
        self.dead_lots: list[_InventoryLot] = []
        self.float_quantity = 0.0
        self.last_macro_buy_index: int | None = None

    @property
    def dead_quantity(self) -> float:
        return sum(lot.quantity for lot in self.dead_lots)

    def macro_buy(self, candles: list[Candle], index: int) -> BacktestInventoryEvent | None:
        candle = candles[index]
        if self.quote_balance_usdt <= 0 or len(self.dead_lots) >= self.config.max_dca_slices:
            return None
        anchor = _ema_close(candles, index, self.config.ema_anchor_bars)
        discount = ((anchor - candle.close) / anchor) if anchor > 0 else 0.0
        phase_pressure = _moon_phase_pressure(index, self.config.moon_phase_pressure)
        trigger_budget = self.slice_budget_usdt * phase_pressure
        forced_budget = 0.0
        beta_triggered = self.config.beta_threshold <= 0 or discount >= self.config.beta_threshold
        if not beta_triggered:
            forced_budget = self.quote_balance_usdt * self.config.deadline_force_pct
        budget = trigger_budget if beta_triggered else forced_budget
        budget = min(budget, self.quote_balance_usdt)
        if budget <= 0:
            return None
        fill_price = candle.close * (1 + self.slippage_bps / 10_000)
        fee_rate = self.fee_bps / 10_000
        quantity = budget / (fill_price * (1 + fee_rate)) if fill_price > 0 else 0.0
        quantity = _normalize_quantity(quantity, self.rules)
        if quantity <= 0:
            return self._event(
                candle=candle,
                event="LOT_SIZE_REJECTED",
                price=fill_price,
                quantity=quantity,
                cost_usdt=budget,
                realized_pnl_usdt=0.0,
                reason="macro inventory quantity was truncated to zero",
            )
        notional = quantity * fill_price
        fee_usdt = notional * fee_rate
        total_spent = notional + fee_usdt
        if self.rules and quantity < self.rules.min_qty:
            return self._event(
                candle=candle,
                event="LOT_SIZE_REJECTED",
                price=fill_price,
                quantity=quantity,
                cost_usdt=total_spent,
                realized_pnl_usdt=0.0,
                reason=f"macro inventory quantity below min_qty {self.rules.min_qty}",
            )
        if self.rules and total_spent < self.rules.min_notional:
            return self._event(
                candle=candle,
                event="LOT_SIZE_REJECTED",
                price=fill_price,
                quantity=quantity,
                cost_usdt=total_spent,
                realized_pnl_usdt=0.0,
                reason=f"macro inventory notional below min_notional {self.rules.min_notional}",
            )
        self.quote_balance_usdt -= total_spent
        self.dead_lots.append(
            _InventoryLot(
                quantity=quantity,
                cost_usdt=total_spent,
                entry_time=candle.close_time.isoformat(),
                entry_index=index,
                entry_price=fill_price,
            )
        )
        self.last_macro_buy_index = index
        reason = (
            "macro DCA beta trigger"
            if beta_triggered
            else "deadline force macro DCA"
        )
        return self._event(
            candle=candle,
            event="MACRO_DCA_BUY",
            price=fill_price,
            quantity=quantity,
            cost_usdt=total_spent,
            realized_pnl_usdt=0.0,
            reason=f"{reason}; discount={discount:.6f}; anchor={anchor:.6f}",
        )

    def maybe_unlock_and_sell(
        self,
        candles: list[Candle],
        index: int,
    ) -> tuple[BacktestTrade | None, tuple[BacktestInventoryEvent, ...]]:
        if index < 2 or not self.dead_lots:
            return None, ()
        acceleration = _normalized_acceleration(candles, index)
        unlock_event_name = ""
        unlock_ratio = 0.0
        unlock_reason = ""
        if acceleration >= self.config.unlock_acceleration_threshold and self.config.sell_ratio > 0:
            unlock_event_name = "ACCELERATION_UNLOCK"
            unlock_ratio = self.config.sell_ratio
            unlock_reason = f"normalized_acceleration={acceleration:.6f}"
        elif (
            self.config.gc_threshold_bars > 0
            and self.config.gc_max_ratio > 0
            and index - self.dead_lots[0].entry_index >= self.config.gc_threshold_bars
        ):
            unlock_event_name = "GC_UNLOCK"
            unlock_ratio = self.config.gc_max_ratio
            unlock_reason = f"oldest_dead_age_bars={index - self.dead_lots[0].entry_index}"
        else:
            return None, ()

        candle = candles[index]
        sell_quantity = self.dead_quantity * unlock_ratio
        sell_quantity = _normalize_quantity(sell_quantity, self.rules)
        if sell_quantity <= 0:
            return None, (
                self._event(
                    candle=candle,
                    event="LOT_SIZE_REJECTED",
                    price=candle.close,
                    quantity=sell_quantity,
                    cost_usdt=0.0,
                    realized_pnl_usdt=0.0,
                    reason="micro sell quantity was truncated to zero",
                ),
            )
        if self.rules and sell_quantity < self.rules.min_qty:
            return None, (
                self._event(
                    candle=candle,
                    event="LOT_SIZE_REJECTED",
                    price=candle.close,
                    quantity=sell_quantity,
                    cost_usdt=0.0,
                    realized_pnl_usdt=0.0,
                    reason=f"micro sell quantity below min_qty {self.rules.min_qty}",
                ),
            )
        lots, cost_basis, entry_time = self._release_lots(sell_quantity)
        if sell_quantity <= 0 or cost_basis <= 0:
            return None, ()

        self.float_quantity += sell_quantity
        unlock_event = self._event(
            candle=candle,
            event=unlock_event_name,
            price=candle.close,
            quantity=sell_quantity,
            cost_usdt=cost_basis,
            realized_pnl_usdt=0.0,
            reason=unlock_reason,
        )

        exit_fill = candle.close * (1 - self.slippage_bps / 10_000)
        proceeds = sell_quantity * exit_fill
        fee_usdt = proceeds * (self.fee_bps / 10_000)
        realized = proceeds - fee_usdt - cost_basis
        self.float_quantity = max(0.0, self.float_quantity - sell_quantity)
        self.quote_balance_usdt += proceeds - fee_usdt
        entry_price = cost_basis / sell_quantity if sell_quantity else 0.0
        trade = BacktestTrade(
            symbol=self.symbol,
            strategy="inventory_bridge",
            entry_time=entry_time,
            exit_time=candle.close_time.isoformat(),
            entry_price=entry_price,
            exit_price=exit_fill,
            quantity=sell_quantity,
            notional_usdt=cost_basis,
            pnl_usdt=realized,
            pnl_pct=(realized / cost_basis) * 100 if cost_basis else 0.0,
            outcome="INVENTORY_SELL",
            holding_bars=max(1, index - lots[0].entry_index + 1),
            confidence=1.0,
            risk_reward=None,
            reason=f"dead inventory unlocked by {unlock_event_name.lower()}; {unlock_reason}",
        )
        sell_event = self._event(
            candle=candle,
            event="MICRO_SELL_FLOAT",
            price=exit_fill,
            quantity=sell_quantity,
            cost_usdt=cost_basis,
            realized_pnl_usdt=realized,
            reason="float inventory sold after unlock",
        )
        return trade, (unlock_event, sell_event)

    def _release_lots(self, quantity: float) -> tuple[list[_InventoryLot], float, str]:
        remaining = quantity
        released: list[_InventoryLot] = []
        cost_basis = 0.0
        entry_time = self.dead_lots[0].entry_time if self.dead_lots else ""
        while remaining > 0 and self.dead_lots:
            lot = self.dead_lots[0]
            take = min(lot.quantity, remaining)
            ratio = take / lot.quantity if lot.quantity else 0.0
            cost = lot.cost_usdt * ratio
            released.append(
                _InventoryLot(
                    quantity=take,
                    cost_usdt=cost,
                    entry_time=lot.entry_time,
                    entry_index=lot.entry_index,
                    entry_price=lot.entry_price,
                )
            )
            cost_basis += cost
            lot.quantity -= take
            lot.cost_usdt -= cost
            remaining -= take
            if lot.quantity <= 1e-12:
                self.dead_lots.pop(0)
        return released, cost_basis, entry_time

    def _event(
        self,
        candle: Candle,
        event: str,
        price: float,
        quantity: float,
        cost_usdt: float,
        realized_pnl_usdt: float,
        reason: str,
    ) -> BacktestInventoryEvent:
        return BacktestInventoryEvent(
            symbol=self.symbol,
            time=candle.close_time.isoformat(),
            event=event,
            price=price,
            quantity=quantity,
            dead_quantity=self.dead_quantity,
            float_quantity=self.float_quantity,
            quote_balance_usdt=self.quote_balance_usdt,
            cost_usdt=cost_usdt,
            realized_pnl_usdt=realized_pnl_usdt,
            reason=reason,
        )


def _simulate_trade(
    candles: list[Candle],
    signal_index: int,
    symbol: str,
    strategy: str,
    confidence: float,
    risk_reward: float | None,
    quantity: float,
    notional: float,
    stop_loss: float | None,
    take_profit: float | None,
    max_holding_bars: int,
    fee_bps: float,
    slippage_bps: float,
    reason: str,
) -> tuple[BacktestTrade, int]:
    entry_index = signal_index + 1
    entry_candle = candles[entry_index]
    entry_fill = entry_candle.open * (1 + slippage_bps / 10_000)
    max_exit_index = min(len(candles) - 1, entry_index + max(1, max_holding_bars))

    exit_index = max_exit_index
    exit_price = candles[max_exit_index].close
    outcome = "TIME_EXIT"
    for cursor in range(entry_index, max_exit_index + 1):
        candle = candles[cursor]
        if stop_loss is not None and candle.low <= stop_loss:
            exit_index = cursor
            exit_price = stop_loss
            outcome = "STOP"
            break
        if take_profit is not None and candle.high >= take_profit:
            exit_index = cursor
            exit_price = take_profit
            outcome = "TAKE_PROFIT"
            break

    exit_fill = exit_price * (1 - slippage_bps / 10_000)
    gross = (exit_fill - entry_fill) * quantity
    fees = ((entry_fill + exit_fill) * quantity) * (fee_bps / 10_000)
    pnl = gross - fees
    pnl_pct = (pnl / notional) * 100 if notional else 0.0
    return (
        BacktestTrade(
            symbol=symbol,
            strategy=strategy,
            entry_time=entry_candle.close_time.isoformat(),
            exit_time=candles[exit_index].close_time.isoformat(),
            entry_price=entry_fill,
            exit_price=exit_fill,
            quantity=quantity,
            notional_usdt=notional,
            pnl_usdt=pnl,
            pnl_pct=pnl_pct,
            outcome=outcome,
            holding_bars=exit_index - entry_index + 1,
            confidence=confidence,
            risk_reward=risk_reward,
            reason=reason,
        ),
        exit_index,
    )


def _ticker_from_window(symbol: str, candles: list[Candle], interval: str) -> TickerSnapshot:
    last = candles[-1].close if candles else 0.0
    bars_per_day = max(1, int(86_400_000 / _interval_to_ms(interval)))
    recent = candles[-bars_per_day:]
    first = recent[0].close if recent else last
    change_pct = ((last - first) / first) * 100 if first else 0.0
    quote_volume = sum(candle.volume * candle.close for candle in recent)
    return TickerSnapshot(
        symbol=symbol,
        last_price=last,
        price_change_pct_24h=change_pct,
        quote_volume_24h=quote_volume,
    )


def _interval_to_ms(interval: str) -> int:
    if not interval:
        return 3_600_000
    unit = interval[-1]
    try:
        amount = int(interval[:-1])
    except ValueError:
        amount = 1
    multipliers = {
        "m": 60_000,
        "h": 3_600_000,
        "d": 86_400_000,
    }
    return amount * multipliers.get(unit, 3_600_000)


def _normalized_acceleration(candles: list[Candle], index: int) -> float:
    if index < 2:
        return 0.0
    previous_previous = candles[index - 2].close
    previous = candles[index - 1].close
    current = candles[index].close
    denominator = max(abs(previous), 1e-9)
    return (current - (2 * previous) + previous_previous) / denominator


def _ema_close(candles: list[Candle], index: int, span: int) -> float:
    start = max(0, index - max(2, span) + 1)
    window = candles[start : index + 1]
    if not window:
        return 0.0
    alpha = 2 / (len(window) + 1)
    ema = window[0].close
    for candle in window[1:]:
        ema = (candle.close * alpha) + (ema * (1 - alpha))
    return ema


def _moon_phase_pressure(index: int, pressure: float) -> float:
    if pressure <= 0:
        return 1.0
    phase = (1 + math.cos((2 * math.pi * (index % 28)) / 28)) / 2
    return 1.0 + (pressure * phase)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize_quantity(quantity: float, rules: SymbolRules | None) -> float:
    if rules is None or rules.step_size <= 0:
        return quantity
    precision = max(0, int(round(-math.log10(rules.step_size)))) if rules.step_size < 1 else 0
    units = math.floor(quantity / rules.step_size)
    return round(units * rules.step_size, precision)


def _max_drawdown_pct(starting_equity: float, trades: list[BacktestTrade]) -> float:
    equity = starting_equity
    peak = starting_equity
    max_drawdown = 0.0
    for trade in trades:
        equity += trade.pnl_usdt
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, ((peak - equity) / peak) * 100)
    return max_drawdown
