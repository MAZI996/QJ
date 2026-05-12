from __future__ import annotations

from dataclasses import replace

from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.execution import ExecutionRouter
from tradingagents.crypto.hyperliquid_execution import HyperliquidExecutionAdapter
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
    assert config.hyperliquid_sdk_execution_enabled is False
    assert config.hyperliquid_require_protective_orders is True


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


def test_hyperliquid_execution_blocks_when_sdk_flag_disabled(tmp_path):
    config = replace(CryptoTradingConfig(), state_dir=tmp_path)
    result = ExecutionRouter(client=object(), config=config).execute(
        _intent(),
        mode="testnet",
    )

    assert result.accepted is False
    assert result.mode == "testnet"
    assert "SDK execution is disabled" in result.message


def test_hyperliquid_live_requires_protective_orders():
    config = replace(
        CryptoTradingConfig(),
        hyperliquid_sdk_execution_enabled=True,
        hyperliquid_testnet=False,
        enable_live_orders=True,
        hyperliquid_wallet_address="0x1111111111111111111111111111111111111111",
        hyperliquid_private_key="0x" + "1" * 64,
        protective_oco_enabled=False,
    )

    result = HyperliquidExecutionAdapter(config)._blocking_reason(
        _intent(),
        mode="live",
        live_confirmation=config.live_confirm_phrase,
    )

    assert "protective stop/take-profit" in result


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


def _intent():
    return RiskManager(CryptoTradingConfig()).evaluate(_signal(), _rules()).intent


def _rules() -> SymbolRules:
    return SymbolRules(
        symbol="BTC",
        base_asset="BTC",
        quote_asset="USDC",
        min_qty=0.00001,
        step_size=0.00001,
        min_notional=10.0,
    )
