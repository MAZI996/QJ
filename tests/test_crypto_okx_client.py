from __future__ import annotations

import json
import urllib.parse
from dataclasses import replace

from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.engine import CryptoTradingEngine
from tradingagents.crypto.execution import ExecutionRouter
from tradingagents.crypto.models import OrderIntent
from tradingagents.crypto.okx_client import OKXClient
from tradingagents.crypto.risk import RiskManager


def test_okx_client_maps_public_market_payloads(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        url = urllib.parse.urlparse(request.full_url)
        query = urllib.parse.parse_qs(url.query)
        path = url.path
        if path == "/api/v5/public/time":
            return _response({"code": "0", "msg": "", "data": [{"ts": "1720000000000"}]})
        if path == "/api/v5/market/ticker":
            assert query["instId"] == ["BTC-USDT-SWAP"]
            return _response(
                {
                    "code": "0",
                    "msg": "",
                    "data": [
                        {
                            "instId": "BTC-USDT-SWAP",
                            "last": "100",
                            "open24h": "90",
                            "volCcyQuote24h": "50000000",
                        }
                    ],
                }
            )
        if path == "/api/v5/market/books":
            return _response(
                {
                    "code": "0",
                    "msg": "",
                    "data": [
                        {
                            "ts": "1720000000100",
                            "bids": [["99.5", "2", "0", "4"]],
                            "asks": [["100.5", "3", "0", "5"]],
                        }
                    ],
                }
            )
        if path == "/api/v5/market/candles":
            return _response(
                {
                    "code": "0",
                    "msg": "",
                    "data": [
                        ["1720000060000", "101", "103", "100", "102", "7", "0", "0", "1"],
                        ["1720000000000", "99", "101", "98", "100", "5", "0", "0", "1"],
                    ],
                }
            )
        if path == "/api/v5/public/instruments":
            assert query["instType"] == ["SWAP"]
            return _response(
                {
                    "code": "0",
                    "msg": "",
                    "data": [
                        {
                            "instType": "SWAP",
                            "instId": "BTC-USDT-SWAP",
                            "baseCcy": "BTC",
                            "quoteCcy": "USDT",
                            "settleCcy": "USDT",
                            "minSz": "0.01",
                            "lotSz": "0.01",
                            "tickSz": "0.1",
                            "ctVal": "0.01",
                        }
                    ],
                }
            )
        raise AssertionError(f"unexpected OKX path: {path}")

    monkeypatch.setattr("tradingagents.crypto.okx_client.urllib.request.urlopen", fake_urlopen)

    client = OKXClient(CryptoTradingConfig())
    assert client.ping() == {"server_time_ms": 1720000000000}

    ticker = client.get_24h_ticker("BTC")
    assert ticker.symbol == "BTC"
    assert ticker.last_price == 100.0
    assert round(ticker.price_change_pct_24h, 2) == 11.11
    assert ticker.quote_volume_24h == 50_000_000.0

    book = client.get_order_book("BTC")
    assert book.best_bid == 99.5
    assert book.best_ask == 100.5
    assert book.bids[0].order_count == 4

    candles = client.get_klines("BTC", "1m", 2)
    assert [candle.open_time_ms for candle in candles] == [1720000000000, 1720000060000]
    assert candles[-1].close == 102.0

    rules = client.get_symbol_rules("BTC")
    assert rules.symbol == "BTC"
    assert rules.base_asset == "BTC"
    assert rules.quote_asset == "USDT"
    assert rules.min_qty == 0.01
    assert rules.step_size == 0.01

    assert all(_header(request, "x-simulated-trading") == "1" for request in requests)


def test_okx_signed_balance_uses_read_only_headers(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        return _response(
            {
                "code": "0",
                "msg": "",
                "data": [
                    {
                        "details": [
                            {
                                "ccy": "USDT",
                                "availBal": "123.4",
                                "frozenBal": "5.6",
                            }
                        ]
                    }
                ],
            }
        )

    monkeypatch.setattr("tradingagents.crypto.okx_client.urllib.request.urlopen", fake_urlopen)
    config = replace(
        CryptoTradingConfig(),
        okx_api_key="key",
        okx_api_secret="secret",
        okx_api_passphrase="passphrase",
    )

    balances = OKXClient(config).get_account_balances()

    assert captured["url"].endswith("/api/v5/account/balance")
    headers = captured["headers"]
    assert headers["ok-access-key"] == "key"
    assert headers["ok-access-passphrase"] == "passphrase"
    assert headers["ok-access-sign"]
    assert headers["x-simulated-trading"] == "1"
    assert balances[0].asset == "USDT"
    assert balances[0].free == 123.4
    assert balances[0].locked == 5.6


def test_engine_defaults_to_okx_and_blocks_okx_live_execution():
    config = CryptoTradingConfig()
    engine = CryptoTradingEngine(config)

    assert isinstance(engine.client, OKXClient)

    result = ExecutionRouter(engine.client, config).execute(
        OrderIntent(
            symbol="BTC",
            side="BUY",
            quantity=0.01,
            notional_usdt=100.0,
            entry_price=10000.0,
            stop_loss=9800.0,
            take_profit=10400.0,
            reason="test",
        ),
        mode="testnet",
    )

    assert result.accepted is False
    assert "OKX execution adapter is not enabled yet" in result.message


def test_okx_risk_rejects_leverage_above_one():
    config = replace(CryptoTradingConfig(), okx_max_leverage=2)
    decision = RiskManager(config).evaluate(
        _signal(),
        _rules(),
    )

    assert decision.approved is False
    assert "OKX initial leverage cap must be 1" in decision.rejected_rules


def test_okx_instrument_id_normalization():
    assert OKXClient.instrument_id_for("BTC") == "BTC-USDT-SWAP"
    assert OKXClient.instrument_id_for("BTCUSDT") == "BTC-USDT-SWAP"
    assert OKXClient.instrument_id_for("BTC-USDT") == "BTC-USDT-SWAP"
    assert OKXClient.instrument_id_for("ETH-USDT-SWAP") == "ETH-USDT-SWAP"
    assert OKXClient.instrument_id_for("ETH", inst_type="SPOT") == "ETH-USDT"


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _response(payload: dict) -> _Response:
    return _Response(payload)


def _header(request, name: str) -> str | None:
    wanted = name.lower()
    for key, value in request.header_items():
        if key.lower() == wanted:
            return value
    return None


def _signal():
    from tradingagents.crypto.models import OpportunitySignal

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


def _rules():
    from tradingagents.crypto.models import SymbolRules

    return SymbolRules(
        symbol="BTC",
        base_asset="BTC",
        quote_asset="USDT",
        min_qty=0.00001,
        step_size=0.00001,
        min_notional=10.0,
    )
