from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.hyperliquid_stream import (
    HyperliquidEventArchive,
    HyperliquidStreamError,
    HyperliquidStreamService,
    default_stream_archive_path,
    stream_event_from_message,
)


def test_default_stream_archive_path_uses_daily_event_partition(tmp_path):
    path = default_stream_archive_path(
        tmp_path,
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )

    assert path == tmp_path / "events" / "hyperliquid-ws-20260720.jsonl"


def test_stream_subscription_plan_includes_public_realtime_feeds(tmp_path):
    config = replace(CryptoTradingConfig(), state_dir=tmp_path)
    service = HyperliquidStreamService(config, symbols=("BTC", "ETH"), interval="5m")

    plan = service.subscription_plan()

    assert plan == (
        {"type": "allMids"},
        {"type": "l2Book", "coin": "BTC"},
        {"type": "trades", "coin": "BTC"},
        {"type": "candle", "coin": "BTC", "interval": "5m"},
        {"type": "activeAssetCtx", "coin": "BTC"},
        {"type": "l2Book", "coin": "ETH"},
        {"type": "trades", "coin": "ETH"},
        {"type": "candle", "coin": "ETH", "interval": "5m"},
        {"type": "activeAssetCtx", "coin": "ETH"},
    )


def test_user_event_stream_requires_wallet_address(tmp_path):
    config = replace(CryptoTradingConfig(), state_dir=tmp_path, hyperliquid_wallet_address="")
    service = HyperliquidStreamService(config)

    with pytest.raises(HyperliquidStreamError):
        service.subscription_plan(user_events=True)


def test_stream_writes_callback_events_to_archive(tmp_path):
    archive_path = tmp_path / "stream.jsonl"
    archive = HyperliquidEventArchive(archive_path)
    fake_info = _FakeInfo()
    config = replace(CryptoTradingConfig(), state_dir=tmp_path)
    service = HyperliquidStreamService(
        config,
        symbols=("BTC",),
        archive=archive,
        info_factory=lambda _base_url: fake_info,
        now=lambda: datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
    )

    subscriptions = service.start()
    service.stop()

    rows = [json.loads(line) for line in archive_path.read_text(encoding="utf-8").splitlines()]
    assert len(subscriptions) == 5
    assert len(rows) == 5
    assert rows[1]["channel"] == "l2Book"
    assert rows[1]["symbols"] == ["BTC"]
    assert rows[1]["summary"]["best_bid"] == 100.0
    assert rows[1]["summary"]["best_ask"] == 101.0
    assert fake_info.disconnected is True


def test_stream_event_summarizes_user_fills():
    event = stream_event_from_message(
        {
            "channel": "user",
            "data": {
                "fills": [
                    {"coin": "BTC", "px": "100", "sz": "0.2"},
                    {"coin": "ETH", "px": "10", "sz": "1"},
                ]
            },
        },
        received_at="2026-07-20T12:00:00+00:00",
    )

    assert event.channel == "user"
    assert event.symbols == ("BTC", "ETH")
    assert event.summary == {"fills_count": 2}


class _FakeInfo:
    def __init__(self):
        self.subscriptions = []
        self.disconnected = False

    def subscribe(self, subscription, callback):
        self.subscriptions.append(dict(subscription))
        callback(_message_for_subscription(subscription))
        return len(self.subscriptions)

    def disconnect_websocket(self):
        self.disconnected = True


def _message_for_subscription(subscription):
    kind = subscription["type"]
    coin = subscription.get("coin", "BTC")
    if kind == "allMids":
        return {"channel": "allMids", "data": {"mids": {"BTC": "100", "ETH": "10"}}}
    if kind == "l2Book":
        return {
            "channel": "l2Book",
            "data": {
                "coin": coin,
                "time": 1,
                "levels": [
                    [{"px": "100", "sz": "2", "n": 1}],
                    [{"px": "101", "sz": "3", "n": 1}],
                ],
            },
        }
    if kind == "trades":
        return {
            "channel": "trades",
            "data": [{"coin": coin, "px": "100", "sz": "0.5", "side": "B", "time": 1}],
        }
    if kind == "candle":
        return {
            "channel": "candle",
            "data": {"s": coin, "i": subscription["interval"], "o": "99", "h": "102", "l": "98", "c": "101", "v": "5"},
        }
    if kind == "activeAssetCtx":
        return {
            "channel": "activeAssetCtx",
            "data": {"coin": coin, "ctx": {"markPx": "101", "funding": "0.0001", "openInterest": "1000"}},
        }
    return {"channel": kind, "data": {}}
