from __future__ import annotations

from dataclasses import replace

from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.hyperliquid_client import (
    HyperliquidBookLevel,
    HyperliquidClient,
    HyperliquidOrderBook,
)
from tradingagents.crypto.market_quality import MarketQualityGate
from tradingagents.crypto.models import OpportunitySignal
from tradingagents.crypto.okx_client import (
    OKXBookLevel,
    OKXClient,
    OKXInstrument,
    OKXOrderBook,
)


def test_okx_market_quality_uses_contract_value_for_depth(monkeypatch):
    config = replace(
        CryptoTradingConfig(),
        market_quality_min_depth_usdc=900,
        market_quality_min_open_interest_usd=1_000_000,
    )
    client = OKXClient(config)
    monkeypatch.setattr(client, "get_order_book", lambda symbol, depth: _book(size=1_000))
    monkeypatch.setattr(client, "get_instrument", lambda symbol: _instrument())
    monkeypatch.setattr(
        client,
        "get_market_context",
        lambda symbol: {
            "funding": 0.0001,
            "openInterest": 2_000_000,
            "openInterestUsd": 5_000_000,
        },
    )

    decision = MarketQualityGate(config, client).evaluate("BTC")

    assert decision.approved is True
    assert round(decision.bid_depth_usdc, 2) == 999.9
    assert round(decision.ask_depth_usdc, 2) == 1000.1
    assert decision.open_interest_usd == 5_000_000
    assert any("open interest 5000000 USD" in reason for reason in decision.reasons)


def test_okx_market_quality_rejects_low_open_interest_and_demotes_buy(monkeypatch):
    config = replace(
        CryptoTradingConfig(),
        market_quality_min_depth_usdc=900,
        market_quality_min_open_interest_usd=1_000_000,
    )
    client = OKXClient(config)
    monkeypatch.setattr(client, "get_order_book", lambda symbol, depth: _book(size=1_000))
    monkeypatch.setattr(client, "get_instrument", lambda symbol: _instrument())
    monkeypatch.setattr(
        client,
        "get_market_context",
        lambda symbol: {
            "funding": 0.0001,
            "openInterest": 10_000,
            "openInterestUsd": 250_000,
        },
    )
    gate = MarketQualityGate(config, client)

    decision = gate.evaluate("BTC")
    filtered = gate.apply(_buy_signal())

    assert decision.approved is False
    assert any("open interest 250000 USD < 1000000" in reason for reason in decision.reasons)
    assert filtered.side == "HOLD"
    assert filtered.confidence < config.min_confidence
    assert filtered.metrics["market_open_interest_usd"] == 250_000


def test_okx_market_quality_rejects_non_swap_mode():
    config = replace(CryptoTradingConfig(), okx_inst_type="SPOT")

    decision = MarketQualityGate(config, OKXClient(config)).evaluate("BTC")

    assert decision.approved is False
    assert decision.reasons == ("OKX market quality currently requires SWAP instruments",)


def test_inverse_contract_notional_uses_fixed_usd_face_value():
    instrument = replace(
        _instrument(),
        inst_id="BTC-USD-SWAP",
        quote_ccy="USD",
        settle_ccy="BTC",
        contract_type="inverse",
        contract_value=100,
        contract_value_ccy="USD",
    )

    assert instrument.notional_usd(price=100_000, size=25) == 2_500


def test_hyperliquid_market_quality_remains_supported(monkeypatch):
    config = replace(
        CryptoTradingConfig(),
        exchange_provider="hyperliquid",
        market_quality_min_depth_usdc=900,
    )
    client = HyperliquidClient(config)
    monkeypatch.setattr(
        client,
        "get_l2_book",
        lambda symbol: HyperliquidOrderBook(
            coin="BTC",
            time_ms=1_720_000_000_000,
            bids=(HyperliquidBookLevel(price=99.99, size=10),),
            asks=(HyperliquidBookLevel(price=100.01, size=10),),
        ),
    )
    monkeypatch.setattr(
        client,
        "get_asset_context",
        lambda symbol: {"funding": "0.0001", "openInterest": "5000"},
    )

    decision = MarketQualityGate(config, client).evaluate("BTC")

    assert decision.approved is True
    assert decision.open_interest == 5000
    assert decision.open_interest_usd is None


def _book(*, size: float) -> OKXOrderBook:
    return OKXOrderBook(
        inst_id="BTC-USDT-SWAP",
        time_ms=1_720_000_000_000,
        bids=(OKXBookLevel(price=99.99, size=size),),
        asks=(OKXBookLevel(price=100.01, size=size),),
    )


def _instrument() -> OKXInstrument:
    return OKXInstrument(
        inst_id="BTC-USDT-SWAP",
        inst_type="SWAP",
        base_ccy="BTC",
        quote_ccy="USDT",
        settle_ccy="USDT",
        min_size=0.01,
        lot_size=0.01,
        tick_size=0.01,
        contract_value=0.01,
        contract_type="linear",
        contract_value_ccy="BTC",
        state="live",
    )


def _buy_signal() -> OpportunitySignal:
    return OpportunitySignal(
        symbol="BTC",
        side="BUY",
        confidence=0.80,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        timeframe="15m",
        strategy="test",
        reasons=("candidate",),
    )
