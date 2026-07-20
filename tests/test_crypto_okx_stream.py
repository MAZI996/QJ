from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from tradingagents.crypto.autopilot import CryptoAutoPilot
from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.okx_stream import (
    OKXEventArchive,
    OKXStreamService,
    default_okx_stream_archive_path,
    okx_candle_channel,
    okx_stream_event_from_message,
)
from tradingagents.crypto.stream_status import summarize_stream_status


def test_default_okx_stream_archive_path_uses_daily_event_partition(tmp_path):
    path = default_okx_stream_archive_path(
        tmp_path,
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )

    assert path == tmp_path / "events" / "okx-ws-20260720.jsonl"


def test_okx_stream_subscription_plan_uses_public_market_channels(tmp_path):
    config = replace(CryptoTradingConfig(), state_dir=tmp_path, interval="5m")
    service = OKXStreamService(config, symbols=("BTC", "ETH-USDT-SWAP"))

    plan = service.subscription_plan()

    assert plan == (
        {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
        {"channel": "books", "instId": "BTC-USDT-SWAP"},
        {"channel": "trades", "instId": "BTC-USDT-SWAP"},
        {"channel": "candle5m", "instId": "BTC-USDT-SWAP"},
        {"channel": "tickers", "instId": "ETH-USDT-SWAP"},
        {"channel": "books", "instId": "ETH-USDT-SWAP"},
        {"channel": "trades", "instId": "ETH-USDT-SWAP"},
        {"channel": "candle5m", "instId": "ETH-USDT-SWAP"},
    )


def test_okx_stream_writes_market_events_to_archive(tmp_path):
    archive_path = tmp_path / "stream.jsonl"
    archive = OKXEventArchive(archive_path)
    fake_ws = _FakeWebSocket()
    config = replace(CryptoTradingConfig(), state_dir=tmp_path, interval="15m")
    service = OKXStreamService(
        config,
        symbols=("BTC",),
        archive=archive,
        ws_factory=lambda _url: fake_ws,
        now=lambda: datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
    )

    subscriptions = service.start()
    for message in _okx_messages():
        service._on_message(message)
    service.stop()

    public_sent = json.loads(fake_ws.sent[0])
    business_sent = json.loads(fake_ws.sent[1])
    rows = [json.loads(line) for line in archive_path.read_text(encoding="utf-8").splitlines()]
    assert public_sent["op"] == "subscribe"
    assert business_sent["op"] == "subscribe"
    assert len(public_sent["args"]) == 3
    assert public_sent["args"][0]["channel"] == "tickers"
    assert len(business_sent["args"]) == 1
    assert business_sent["args"][0]["channel"] == "candle15m"
    assert len(subscriptions) == 4
    assert len(rows) == 4
    assert rows[0]["channel"] == "tickers"
    assert rows[0]["symbols"] == ["BTC"]
    assert rows[0]["summary"]["last"] == 100.0
    assert rows[1]["summary"]["best_bid"] == 99.5
    assert rows[2]["summary"]["trade_count"] == 1
    assert rows[3]["summary"]["interval"] == "15m"
    assert fake_ws.closed is True


def test_okx_stream_status_accepts_fresh_required_channels(tmp_path):
    archive = tmp_path / "events" / "okx-ws-20260720.jsonl"
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    _write_required_okx_events(archive, now - timedelta(seconds=30), symbol="BTC")
    config = replace(CryptoTradingConfig(), state_dir=tmp_path, interval="15m")

    summary = summarize_stream_status(
        config,
        symbols=("BTC",),
        max_age_seconds=60,
        now=now,
    )

    assert summary.provider == "okx"
    assert summary.fresh is True
    assert summary.events_read == 4
    assert {row.channel for row in summary.rows} == {"tickers", "books", "candle15m"}


def test_okx_autopilot_stops_when_stream_is_missing(tmp_path, monkeypatch):
    config = replace(CryptoTradingConfig(), state_dir=tmp_path, symbols=("BTC",))

    def fail_engine(_config):
        raise AssertionError("scanner engine should not start when OKX stream evidence is stale")

    monkeypatch.setattr("tradingagents.crypto.autopilot.CryptoTradingEngine", fail_engine)

    result = CryptoAutoPilot(config).run_once(
        symbols=("BTC",),
        guard_positions=False,
        require_fresh_stream=True,
    )

    assert result.stopped is True
    assert result.stream_status is not None
    assert result.stream_status.provider == "okx"
    assert "OKX WebSocket stream is stale or missing" in result.reason
    assert "crypto-okx-stream" in result.reason


def test_okx_candle_channel_normalizes_hours_and_minutes():
    assert okx_candle_channel("15m") == "candle15m"
    assert okx_candle_channel("1h") == "candle1H"
    assert okx_candle_channel("1d") == "candle1D"


def test_okx_stream_event_summarizes_book_payload():
    event = okx_stream_event_from_message(
        _okx_messages()[1],
        received_at="2026-07-20T12:00:00+00:00",
    )

    assert event.channel == "books"
    assert event.symbols == ("BTC",)
    assert event.summary["spread_bps"] > 0


class _FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value

    def send(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True


def _okx_messages():
    return [
        {
            "arg": {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "last": "100",
                    "bidPx": "99.5",
                    "askPx": "100.5",
                    "open24h": "90",
                    "volCcyQuote24h": "50000000",
                }
            ],
        },
        {
            "arg": {"channel": "books", "instId": "BTC-USDT-SWAP"},
            "data": [
                {
                    "ts": "1720000000100",
                    "bids": [["99.5", "2", "0", "4"]],
                    "asks": [["100.5", "3", "0", "5"]],
                }
            ],
        },
        {
            "arg": {"channel": "trades", "instId": "BTC-USDT-SWAP"},
            "data": [{"instId": "BTC-USDT-SWAP", "px": "100", "sz": "0.25", "side": "buy"}],
        },
        {
            "arg": {"channel": "candle15m", "instId": "BTC-USDT-SWAP"},
            "data": [["1720000000000", "99", "101", "98", "100", "5", "0", "0", "1"]],
        },
    ]


def _write_required_okx_events(path, received_at, *, symbol: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"channel": "tickers", "symbols": [symbol], "received_at": received_at.isoformat()},
        {"channel": "books", "symbols": [symbol], "received_at": received_at.isoformat()},
        {"channel": "trades", "symbols": [symbol], "received_at": received_at.isoformat()},
        {"channel": "candle15m", "symbols": [symbol], "received_at": received_at.isoformat()},
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
