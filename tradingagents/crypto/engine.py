"""Coordinator for separated crypto scan, risk, and execution stages."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from .binance_client import BinanceClient
from .config import CryptoTradingConfig
from .execution import ExecutionRouter
from .hyperliquid_client import HyperliquidClient
from .okx_client import OKXClient
from .market_quality import MarketQualityGate
from .models import (
    AccountBalance,
    AITradeReview,
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
        provider = self.config.exchange_provider.strip().lower()
        if provider == "okx":
            return OKXClient(self.config)
        if provider == "hyperliquid":
            return HyperliquidClient(self.config)
        return BinanceClient(self.config)

    def scan_and_review(
        self,
        symbols: tuple[str, ...] | None = None,
        execute_top: bool = False,
        execution_mode: ExecutionMode | None = None,
        live_confirmation: str = "",
    ) -> list[ReviewedSignal]:
        if execute_top:
            raise ValueError(
                "Direct engine execution is disabled; use "
                "CryptoTradingAgentsWorkflow so AI review runs before risk and execution."
            )
        return self.review_candidates(self.scan_candidates(symbols))

    def scan_candidates(
        self,
        symbols: tuple[str, ...] | None = None,
    ) -> list[OpportunitySignal]:
        return sorted(
            (
                self.market_quality.apply(self.strategy_fusion.fuse(signal))
                for signal in self.scanner.scan(symbols)
            ),
            key=lambda signal: signal.confidence,
            reverse=True,
        )

    def review_candidates(
        self,
        signals: Sequence[OpportunitySignal],
        *,
        for_execution: bool = False,
        execution_mode: ExecutionMode | None = None,
    ) -> list[ReviewedSignal]:
        reviewed: list[ReviewedSignal] = []
        quote_balances = self._quote_balances_for_execution(
            for_execution,
            execution_mode,
        )
        for signal in signals:
            rules = self._safe_symbol_rules(signal.symbol)
            quote_balance = quote_balances.get(rules.quote_asset) if rules else None
            decision = self.risk.evaluate(
                signal,
                rules,
                available_quote_balance=quote_balance,
            )
            reviewed.append(
                ReviewedSignal(signal=signal, risk=decision, rules=rules)
            )
        return reviewed

    def execute_ai_approved(
        self,
        reviewed: Sequence[ReviewedSignal],
        ai_review: AITradeReview | None,
        *,
        execution_mode: ExecutionMode | None = None,
        live_confirmation: str = "",
    ) -> tuple[list[ReviewedSignal], str]:
        blocked_reason = self.ai_execution_block_reason(ai_review, reviewed)
        if blocked_reason:
            return list(reviewed), blocked_reason

        assert ai_review is not None and ai_review.symbol is not None
        selected_symbol = ai_review.symbol.strip().upper()
        updated = list(reviewed)
        for index, item in enumerate(updated):
            if item.signal.symbol.strip().upper() != selected_symbol:
                continue
            assert item.risk.intent is not None
            result = self.execution.execute(
                item.risk.intent,
                mode=execution_mode,
                live_confirmation=live_confirmation,
            )
            updated[index] = replace(item, execution=result)
            return updated, ""
        return updated, f"AI selected unknown candidate: {selected_symbol}"

    def ai_execution_block_reason(
        self,
        ai_review: AITradeReview | None,
        reviewed: Sequence[ReviewedSignal] = (),
    ) -> str:
        if ai_review is None:
            return "Execution blocked: a successful AI review is required."
        if ai_review.action != "BUY":
            return f"Execution blocked: AI action is {ai_review.action}."
        if ai_review.confidence < self.config.ai_execution_min_confidence:
            return (
                "Execution blocked: AI confidence "
                f"{ai_review.confidence:.2f} is below "
                f"{self.config.ai_execution_min_confidence:.2f}."
            )
        if not ai_review.symbol:
            return "Execution blocked: AI BUY review did not select a symbol."

        selected_symbol = ai_review.symbol.strip().upper()
        if not reviewed:
            return ""
        selected = next(
            (
                item
                for item in reviewed
                if item.signal.symbol.strip().upper() == selected_symbol
            ),
            None,
        )
        if selected is None:
            return f"Execution blocked: AI selected unknown candidate {selected_symbol}."
        if not selected.risk.approved or selected.risk.intent is None:
            return (
                f"Execution blocked: deterministic risk rejected {selected_symbol}."
            )
        return ""

    def account_balances(self) -> list[AccountBalance]:
        return self.client.get_account_balances()

    def _safe_symbol_rules(self, symbol: str) -> SymbolRules | None:
        try:
            return self.client.get_symbol_rules(symbol)
        except Exception:
            return None

    def _quote_balances_for_execution(
        self,
        for_execution: bool,
        execution_mode: ExecutionMode | None,
    ) -> dict[str, float]:
        mode = execution_mode or self.config.execution_mode
        if not for_execution or mode not in {"testnet", "live"}:
            return {}
        try:
            return {balance.asset: balance.free for balance in self.account_balances()}
        except Exception:
            return {}
