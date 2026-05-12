"""Coordinator for crypto scan, risk review, and optional execution."""

from __future__ import annotations

from dataclasses import dataclass

from .binance_client import BinanceClient
from .config import CryptoTradingConfig
from .execution import ExecutionRouter
from .hyperliquid_client import HyperliquidClient
from .market_quality import MarketQualityGate
from .models import (
    AccountBalance,
    ExecutionMode,
    OpportunitySignal,
    OrderResult,
    RiskDecision,
    SymbolRules,
)
from .risk import RiskManager
from .scanner import OpportunityScanner
from .strategy_fusion import StrategyFusionEngine


@dataclass(frozen=True)
class ReviewedSignal:
    signal: OpportunitySignal
    risk: RiskDecision
    rules: SymbolRules | None = None
    execution: OrderResult | None = None


class CryptoTradingEngine:
    def __init__(self, config: CryptoTradingConfig | None = None):
        self.config = config or CryptoTradingConfig.from_env()
        self.client = self._create_client()
        self.scanner = OpportunityScanner(self.client, self.config)
        self.strategy_fusion = StrategyFusionEngine(self.config)
        self.market_quality = MarketQualityGate(self.config, self.client)
        self.risk = RiskManager(self.config)
        self.execution = ExecutionRouter(self.client, self.config)

    def _create_client(self):
        if self.config.exchange_provider.strip().lower() == "hyperliquid":
            return HyperliquidClient(self.config)
        return BinanceClient(self.config)

    def scan_and_review(
        self,
        symbols: tuple[str, ...] | None = None,
        execute_top: bool = False,
        execution_mode: ExecutionMode | None = None,
        live_confirmation: str = "",
    ) -> list[ReviewedSignal]:
        signals = sorted(
            (
                self.market_quality.apply(self.strategy_fusion.fuse(signal))
                for signal in self.scanner.scan(symbols)
            ),
            key=lambda signal: signal.confidence,
            reverse=True,
        )
        reviewed: list[ReviewedSignal] = []
        executed = False
        quote_balances = self._quote_balances_for_execution(execute_top, execution_mode)
        for signal in signals:
            rules = self._safe_symbol_rules(signal.symbol)
            quote_balance = quote_balances.get(rules.quote_asset) if rules else None
            decision = self.risk.evaluate(
                signal,
                rules,
                available_quote_balance=quote_balance,
            )
            result = None
            if execute_top and not executed and decision.approved and decision.intent:
                result = self.execution.execute(
                    decision.intent,
                    mode=execution_mode,
                    live_confirmation=live_confirmation,
                )
                executed = True
            reviewed.append(
                ReviewedSignal(signal=signal, risk=decision, rules=rules, execution=result)
            )
        return reviewed

    def account_balances(self) -> list[AccountBalance]:
        return self.client.get_account_balances()

    def _safe_symbol_rules(self, symbol: str) -> SymbolRules | None:
        try:
            return self.client.get_symbol_rules(symbol)
        except Exception:
            return None

    def _quote_balances_for_execution(
        self,
        execute_top: bool,
        execution_mode: ExecutionMode | None,
    ) -> dict[str, float]:
        mode = execution_mode or self.config.execution_mode
        if not execute_top or mode not in {"testnet", "live"}:
            return {}
        try:
            return {balance.asset: balance.free for balance in self.account_balances()}
        except Exception:
            return {}
