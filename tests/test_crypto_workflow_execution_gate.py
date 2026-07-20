from __future__ import annotations

from types import SimpleNamespace

import pytest

import tradingagents.crypto.workflow_report as workflow_module
from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.engine import CryptoTradingEngine, ReviewedSignal
from tradingagents.crypto.models import (
    AITradeReview,
    AccountBalance,
    OpportunitySignal,
    OrderIntent,
    OrderResult,
    RiskDecision,
    SymbolRules,
)
from tradingagents.crypto.workflow_report import CryptoTradingAgentsWorkflow


def test_execution_order_is_scan_ai_risk_then_execution(monkeypatch):
    events: list[str] = []
    engine = _engine(events, risk_approved=True)
    _patch_ai(monkeypatch, events, action="BUY", symbol="BTC", confidence=0.91)

    report = CryptoTradingAgentsWorkflow(engine.config, engine).run(
        symbols=("BTC",),
        execute_top=True,
        execution_mode="testnet",
        ai_review_enabled=True,
    )

    assert events == ["scan", "ai", "balance", "risk", "execute"]
    assert report.execution_gate_reason == ""
    assert report.reviewed[0].execution is not None
    assert report.reviewed[0].execution.accepted is True


@pytest.mark.parametrize("action", ["HOLD", "REJECT"])
def test_ai_hold_or_reject_blocks_execution(monkeypatch, action):
    events: list[str] = []
    engine = _engine(events, risk_approved=True)
    _patch_ai(monkeypatch, events, action=action, symbol=None, confidence=0.91)

    report = CryptoTradingAgentsWorkflow(engine.config, engine).run(
        execute_top=True,
        execution_mode="testnet",
        ai_review_enabled=True,
    )

    assert events == ["scan", "ai", "risk"]
    assert f"AI action is {action}" in report.execution_gate_reason
    assert report.reviewed[0].execution is None


def test_ai_failure_blocks_execution(monkeypatch):
    events: list[str] = []
    engine = _engine(events, risk_approved=True)

    class FailingLLM:
        def invoke(self, _prompt):
            events.append("ai")
            raise RuntimeError("Hermes unavailable")

    monkeypatch.setattr(
        workflow_module,
        "create_crypto_review_llm",
        lambda _config: FailingLLM(),
    )

    report = CryptoTradingAgentsWorkflow(engine.config, engine).run(
        execute_top=True,
        execution_mode="testnet",
        ai_review_enabled=True,
    )

    assert events == ["scan", "ai", "risk"]
    assert "Hermes unavailable" in report.ai_error
    assert "AI review failed" in report.execution_gate_reason
    assert report.reviewed[0].execution is None


def test_ai_buy_cannot_bypass_deterministic_risk(monkeypatch):
    events: list[str] = []
    engine = _engine(events, risk_approved=False)
    _patch_ai(monkeypatch, events, action="BUY", symbol="BTC", confidence=0.91)

    report = CryptoTradingAgentsWorkflow(engine.config, engine).run(
        execute_top=True,
        execution_mode="testnet",
        ai_review_enabled=True,
    )

    assert events == ["scan", "ai", "balance", "risk"]
    assert "deterministic risk rejected BTC" in report.execution_gate_reason
    assert report.reviewed[0].execution is None


def test_execute_top_requires_ai_review(monkeypatch):
    events: list[str] = []
    engine = _engine(events, risk_approved=True)
    monkeypatch.setattr(
        workflow_module,
        "create_crypto_review_llm",
        lambda _config: pytest.fail("AI router must not be called when disabled"),
    )

    report = CryptoTradingAgentsWorkflow(engine.config, engine).run(
        execute_top=True,
        execution_mode="testnet",
        ai_review_enabled=False,
    )

    assert events == ["scan", "risk"]
    assert "requires --ai-review" in report.execution_gate_reason
    assert report.reviewed[0].execution is None


def test_low_confidence_or_missing_symbol_blocks_execution(monkeypatch):
    events: list[str] = []
    engine = _engine(events, risk_approved=True)
    _patch_ai(monkeypatch, events, action="BUY", symbol="BTC", confidence=0.20)

    low_confidence = CryptoTradingAgentsWorkflow(engine.config, engine).run(
        execute_top=True,
        execution_mode="testnet",
        ai_review_enabled=True,
    )

    assert "AI confidence" in low_confidence.execution_gate_reason
    assert "execute" not in events

    events.clear()
    _patch_ai(monkeypatch, events, action="BUY", symbol=None, confidence=0.91)
    missing_symbol = CryptoTradingAgentsWorkflow(engine.config, engine).run(
        execute_top=True,
        execution_mode="testnet",
        ai_review_enabled=True,
    )

    assert "did not select a symbol" in missing_symbol.execution_gate_reason
    assert "execute" not in events


def test_direct_engine_execution_path_is_disabled():
    engine = _engine([], risk_approved=True)

    with pytest.raises(ValueError, match="AI review runs before risk"):
        engine.scan_and_review(execute_top=True)


def test_engine_rejects_unknown_ai_symbol_without_submitting_order():
    events: list[str] = []
    engine = _engine(events, risk_approved=True)
    signal = _signal()
    reviewed = engine.review_candidates((signal,))
    ai_review = AITradeReview(
        action="BUY",
        confidence=0.91,
        summary="",
        main_risk="",
        invalidation="",
        role_notes="",
        model="test",
        router="test",
        symbol="ETH",
    )

    result, reason = engine.execute_ai_approved(reviewed, ai_review)

    assert "unknown candidate ETH" in reason
    assert result[0].execution is None
    assert "execute" not in events


def _patch_ai(monkeypatch, events, *, action, symbol, confidence):
    selected = symbol or "NONE"

    class FakeLLM:
        model = "test-model"

        def invoke(self, prompt):
            events.append("ai")
            assert "Deterministic risk sizing" in prompt
            return SimpleNamespace(
                content=(
                    f"Action: {action}\n"
                    f"Symbol: {selected}\n"
                    f"Confidence: {confidence}\n"
                    "Summary: test\n"
                    "Main risk: test\n"
                    "Invalidation: test\n"
                    "Role notes: test"
                )
            )

    monkeypatch.setattr(
        workflow_module,
        "create_crypto_review_llm",
        lambda _config: FakeLLM(),
    )


def _engine(events: list[str], *, risk_approved: bool) -> CryptoTradingEngine:
    config = CryptoTradingConfig(
        exchange_provider="okx",
        ai_execution_min_confidence=0.62,
        execution_mode="testnet",
    )
    engine = object.__new__(CryptoTradingEngine)
    engine.config = config
    engine.client = _Client(events)
    engine.scanner = _Scanner(events)
    engine.strategy_fusion = _IdentityFusion()
    engine.market_quality = _IdentityQuality()
    engine.risk = _Risk(events, approved=risk_approved)
    engine.execution = _Execution(events)
    return engine


def _signal() -> OpportunitySignal:
    return OpportunitySignal(
        symbol="BTC",
        side="BUY",
        confidence=0.80,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        timeframe="15m",
        strategy="test",
        reasons=("test setup",),
    )


class _Scanner:
    def __init__(self, events):
        self.events = events

    def scan(self, _symbols):
        self.events.append("scan")
        return [_signal()]


class _IdentityFusion:
    @staticmethod
    def fuse(signal):
        return signal


class _IdentityQuality:
    @staticmethod
    def apply(signal):
        return signal


class _Client:
    def __init__(self, events):
        self.events = events

    @staticmethod
    def get_symbol_rules(symbol):
        return SymbolRules(
            symbol=symbol,
            base_asset=symbol,
            quote_asset="USDT",
            min_qty=0.001,
            step_size=0.001,
            min_notional=10.0,
        )

    def get_account_balances(self):
        self.events.append("balance")
        return [AccountBalance(asset="USDT", free=1_000.0, locked=0.0)]


class _Risk:
    def __init__(self, events, *, approved):
        self.events = events
        self.approved = approved

    def evaluate(self, signal, _rules, available_quote_balance=None):
        self.events.append("risk")
        if not self.approved:
            return RiskDecision(
                approved=False,
                reason="rejected",
                rejected_rules=("test rejection",),
            )
        assert available_quote_balance in {None, 1_000.0}
        return RiskDecision(
            approved=True,
            reason="approved",
            intent=OrderIntent(
                symbol=signal.symbol,
                side="BUY",
                quantity=1.0,
                notional_usdt=100.0,
                entry_price=100.0,
                stop_loss=95.0,
                take_profit=110.0,
                reason="test",
            ),
        )


class _Execution:
    def __init__(self, events):
        self.events = events

    def execute(self, intent, mode=None, live_confirmation=""):
        self.events.append("execute")
        return OrderResult(
            mode=mode or "analysis",
            accepted=True,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            message="accepted",
        )
