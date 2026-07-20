"""Risk gate for personal crypto trading accounts."""

from __future__ import annotations

import math

from .config import CryptoTradingConfig
from .models import OpportunitySignal, OrderIntent, RiskDecision, SymbolRules


class RiskManager:
    def __init__(self, config: CryptoTradingConfig):
        self.config = config

    def evaluate(
        self,
        signal: OpportunitySignal,
        rules: SymbolRules | None = None,
        available_quote_balance: float | None = None,
    ) -> RiskDecision:
        rejected: list[str] = []
        provider = self.config.exchange_provider.strip().lower()
        if signal.side != "BUY":
            rejected.append("当前第一阶段只允许做多候选，不做空")
        if provider == "okx" and self.config.okx_max_leverage > 1:
            rejected.append("OKX initial leverage cap must be 1")
        if provider == "hyperliquid" and self.config.hyperliquid_max_leverage > 1:
            rejected.append("Hyperliquid 初始阶段杠杆上限必须为 1")
        if signal.confidence < self.config.min_confidence:
            rejected.append(
                f"置信度 {signal.confidence:.2f} 低于阈值 {self.config.min_confidence:.2f}"
            )
        if signal.stop_loss is None or signal.stop_loss >= signal.entry_price:
            rejected.append("缺少有效止损价")
        if signal.take_profit is None or signal.take_profit <= signal.entry_price:
            rejected.append("缺少有效止盈价")

        rr = signal.risk_reward
        if rr is None or rr < 1.5:
            rejected.append("盈亏比低于 1.5")

        if rejected:
            return RiskDecision(False, "风控拒绝", rejected_rules=tuple(rejected))

        assert signal.stop_loss is not None
        effective_equity = self.config.account_equity_usdt
        if available_quote_balance is not None:
            effective_equity = min(effective_equity, available_quote_balance)

        per_unit_risk = signal.entry_price - signal.stop_loss
        risk_budget = effective_equity * self.config.risk_per_trade_pct
        if self.config.max_loss_per_trade_usdt > 0:
            risk_budget = min(risk_budget, self.config.max_loss_per_trade_usdt)
        raw_quantity = risk_budget / per_unit_risk
        max_notional = effective_equity * self.config.max_position_pct
        if available_quote_balance is not None:
            max_notional = min(max_notional, available_quote_balance)
        quantity = min(raw_quantity, max_notional / signal.entry_price)

        if rules:
            quantity = self._floor_to_step(quantity, rules.step_size)
            min_notional = max(rules.min_notional, self.config.min_order_notional_usdt)
            if quantity < rules.min_qty:
                rejected.append(f"数量 {quantity:.8f} 低于交易对最小数量 {rules.min_qty}")
        else:
            min_notional = self.config.min_order_notional_usdt

        notional = quantity * signal.entry_price
        if notional < min_notional:
            rejected.append(f"名义金额 {notional:.2f} USDT 低于最小下单金额 {min_notional:.2f}")
        if quantity <= 0:
            rejected.append("计算后的下单数量为 0")

        if rejected:
            return RiskDecision(False, "风控拒绝", rejected_rules=tuple(rejected))

        intent = OrderIntent(
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            notional_usdt=notional,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            reason="; ".join(signal.reasons[:3]),
        )
        return RiskDecision(True, "通过风控，允许进入执行层", intent=intent)

    @staticmethod
    def _floor_to_step(quantity: float, step_size: float) -> float:
        if step_size <= 0:
            return quantity
        precision = max(0, int(round(-math.log10(step_size)))) if step_size < 1 else 0
        units = math.floor(quantity / step_size)
        return round(units * step_size, precision)
