from __future__ import annotations

from dataclasses import replace

from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.models import OpportunitySignal, SymbolRules
from tradingagents.crypto.risk import RiskManager


def test_crypto_config_defaults_to_hyperliquid(monkeypatch):
    for key in (
        "TRADINGAGENTS_CRYPTO_EXCHANGE_PROVIDER",
        "TRADINGAGENTS_CRYPTO_SYMBOLS",
        "TRADINGAGENTS_CRYPTO_LIVE_CONFIRM_PHRASE",
    ):
        monkeypatch.delenv(key, raising=False)

    config = CryptoTradingConfig.from_env()

    assert config.exchange_provider == "hyperliquid"
    assert config.symbols == ("BTC", "ETH", "SOL", "HYPE")
    assert config.live_confirm_phrase == "I_UNDERSTAND_THIS_PLACES_REAL_HYPERLIQUID_ORDERS"


def test_hyperliquid_risk_rejects_leverage_above_one():
    config = replace(CryptoTradingConfig(), hyperliquid_max_leverage=2)
    decision = RiskManager(config).evaluate(_signal(), _rules())

    assert decision.approved is False
    assert "Hyperliquid 初始阶段杠杆上限必须为 1" in decision.rejected_rules


def test_hyperliquid_risk_accepts_valid_one_x_long_candidate():
    config = replace(CryptoTradingConfig(), hyperliquid_max_leverage=1)
    decision = RiskManager(config).evaluate(_signal(), _rules())

    assert decision.approved is True
    assert decision.intent is not None
    assert decision.intent.symbol == "BTC"


def _signal() -> OpportunitySignal:
    return OpportunitySignal(
        symbol="BTC",
        side="BUY",
        confidence=0.75,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        timeframe="15m",
        strategy="test",
        reasons=("valid test signal",),
    )


def _rules() -> SymbolRules:
    return SymbolRules(
        symbol="BTC",
        base_asset="BTC",
        quote_asset="USDC",
        min_qty=0.00001,
        step_size=0.00001,
        min_notional=10.0,
    )
