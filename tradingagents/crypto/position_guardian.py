"""Position guardian for automated long-only exit checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import CryptoTradingConfig
from .execution import ExecutionRouter
from .hyperliquid_client import HyperliquidClient
from .models import ExecutionMode, OpportunitySignal, OrderIntent, OrderResult
from .positions import PositionRecord, PositionStore
from .scanner import OpportunityScanner
from .strategy_fusion import StrategyFusionEngine


@dataclass(frozen=True)
class PositionGuardDecision:
    symbol: str
    action: str
    reason: str
    mark_price: float
    quantity: float
    intent: OrderIntent | None = None
    execution: OrderResult | None = None

    @property
    def wants_close(self) -> bool:
        return self.action == "CLOSE" and self.intent is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "reason": self.reason,
            "mark_price": self.mark_price,
            "quantity": self.quantity,
            "intent": _intent_payload(self.intent),
            "execution": _execution_payload(self.execution),
        }


@dataclass(frozen=True)
class PositionGuardResult:
    enabled: bool
    mode: ExecutionMode
    decisions: tuple[PositionGuardDecision, ...]

    @property
    def close_signals(self) -> tuple[PositionGuardDecision, ...]:
        return tuple(item for item in self.decisions if item.wants_close)

    @property
    def close_attempts(self) -> tuple[PositionGuardDecision, ...]:
        return tuple(item for item in self.decisions if item.execution is not None)

    @property
    def accepted_closes(self) -> tuple[PositionGuardDecision, ...]:
        return tuple(
            item for item in self.decisions if item.execution is not None and item.execution.accepted
        )

    @property
    def summary(self) -> str:
        if not self.enabled:
            return "Position guardian disabled."
        if not self.decisions:
            return "Position guardian: no open positions."
        closes = len(self.close_signals)
        accepted = len(self.accepted_closes)
        return (
            f"Position guardian checked {len(self.decisions)} open position(s); "
            f"close_signals={closes}; accepted_closes={accepted}."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "summary": self.summary,
            "decisions": [item.to_dict() for item in self.decisions],
        }


class PositionGuardian:
    """Close long positions through the execution router when exit gates trigger."""

    def __init__(
        self,
        client,
        config: CryptoTradingConfig,
        *,
        positions: PositionStore | None = None,
    ):
        self.client = client
        self.config = config
        self.positions = positions or PositionStore.from_state_dir(config.state_dir)
        self.execution = ExecutionRouter(client, config)

    def run(
        self,
        *,
        mode: ExecutionMode,
        live_confirmation: str = "",
        execute: bool = False,
    ) -> PositionGuardResult:
        if not self.config.position_guardian_enabled:
            return PositionGuardResult(enabled=False, mode=mode, decisions=())

        active_positions = self.positions.active_positions()
        marks = self._mark_prices(active_positions)
        strategy_signals = (
            self._strategy_signals(active_positions)
            if self.config.position_guardian_strategy_exit_enabled
            else {}
        )

        decisions: list[PositionGuardDecision] = []
        for position in active_positions:
            mark_price = marks.get(position.symbol, 0.0)
            decision = self._decision(position, mark_price, strategy_signals.get(position.symbol))
            if execute and decision.intent is not None:
                execution = self.execution.execute(
                    decision.intent,
                    mode=mode,
                    live_confirmation=live_confirmation,
                )
                decision = PositionGuardDecision(
                    symbol=decision.symbol,
                    action=decision.action,
                    reason=decision.reason,
                    mark_price=decision.mark_price,
                    quantity=decision.quantity,
                    intent=decision.intent,
                    execution=execution,
                )
            decisions.append(decision)
        return PositionGuardResult(enabled=True, mode=mode, decisions=tuple(decisions))

    def _decision(
        self,
        position: PositionRecord,
        mark_price: float,
        signal: OpportunitySignal | None,
    ) -> PositionGuardDecision:
        if mark_price <= 0:
            return PositionGuardDecision(
                symbol=position.symbol,
                action="HOLD",
                reason="mark_price_unavailable",
                mark_price=mark_price,
                quantity=position.quantity,
            )

        close_reason = self._close_reason(position, mark_price, signal)
        if close_reason:
            intent = OrderIntent(
                symbol=position.symbol,
                side="SELL",
                quantity=position.quantity,
                notional_usdt=position.quantity * mark_price,
                entry_price=mark_price,
                stop_loss=None,
                take_profit=None,
                reason=f"position_guardian:{close_reason}",
                reduce_only=True,
            )
            return PositionGuardDecision(
                symbol=position.symbol,
                action="CLOSE",
                reason=close_reason,
                mark_price=mark_price,
                quantity=position.quantity,
                intent=intent,
            )

        return PositionGuardDecision(
            symbol=position.symbol,
            action="HOLD",
            reason="no_exit_trigger",
            mark_price=mark_price,
            quantity=position.quantity,
        )

    def _close_reason(
        self,
        position: PositionRecord,
        mark_price: float,
        signal: OpportunitySignal | None,
    ) -> str:
        if (
            self.config.position_guardian_close_on_stop
            and position.stop_loss is not None
            and mark_price <= position.stop_loss
        ):
            return f"stop_loss_hit:{mark_price:.8f}<={position.stop_loss:.8f}"
        if (
            self.config.position_guardian_close_on_take_profit
            and position.take_profit is not None
            and mark_price >= position.take_profit
        ):
            return f"take_profit_hit:{mark_price:.8f}>={position.take_profit:.8f}"
        max_minutes = self.config.position_guardian_max_holding_minutes
        if max_minutes > 0 and _age_minutes(position.opened_at) >= max_minutes:
            return f"max_holding_minutes:{max_minutes}"
        if self.config.position_guardian_strategy_exit_enabled:
            if signal is None:
                return "strategy_exit:no_current_signal"
            if signal.side != "BUY":
                return f"strategy_exit:signal_side_{signal.side}"
            if signal.confidence < self.config.position_guardian_strategy_min_confidence:
                return (
                    "strategy_exit:confidence_"
                    f"{signal.confidence:.4f}<"
                    f"{self.config.position_guardian_strategy_min_confidence:.4f}"
                )
        return ""

    def _mark_prices(self, positions: list[PositionRecord]) -> dict[str, float]:
        if not positions:
            return {}
        marks: dict[str, float] = {}
        try:
            mids = self.client.get_all_mids()
        except Exception:
            mids = {}
        for position in positions:
            symbol = (
                HyperliquidClient.normalize_symbol(position.symbol)
                if self.config.exchange_provider.strip().lower() == "hyperliquid"
                else position.symbol
            )
            mark = _safe_float(mids.get(symbol))
            if mark <= 0:
                try:
                    mark = _safe_float(self.client.get_24h_ticker(symbol).last_price)
                except Exception:
                    mark = 0.0
            marks[position.symbol] = mark
        return marks

    def _strategy_signals(self, positions: list[PositionRecord]) -> dict[str, OpportunitySignal]:
        if not positions:
            return {}
        symbols = tuple(position.symbol for position in positions)
        try:
            scanner = OpportunityScanner(self.client, self.config)
            fusion = StrategyFusionEngine(self.config)
            return {signal.symbol: fusion.fuse(signal) for signal in scanner.scan(symbols)}
        except Exception:
            return {}


def _age_minutes(opened_at: str) -> float:
    try:
        opened = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - opened).total_seconds() / 60)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _intent_payload(intent: OrderIntent | None) -> dict[str, Any] | None:
    if intent is None:
        return None
    return {
        "symbol": intent.symbol,
        "side": intent.side,
        "quantity": intent.quantity,
        "notional_usdt": intent.notional_usdt,
        "entry_price": intent.entry_price,
        "stop_loss": intent.stop_loss,
        "take_profit": intent.take_profit,
        "reason": intent.reason,
        "reduce_only": intent.reduce_only,
    }


def _execution_payload(execution: OrderResult | None) -> dict[str, Any] | None:
    if execution is None:
        return None
    return {
        "mode": execution.mode,
        "accepted": execution.accepted,
        "symbol": execution.symbol,
        "side": execution.side,
        "quantity": execution.quantity,
        "message": execution.message,
        "exchange_payload": execution.exchange_payload,
    }
