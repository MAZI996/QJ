from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.live_readiness import LiveReadinessChecker
from tradingagents.crypto.stream_status import summarize_stream_status


def test_stream_status_flags_missing_archive(tmp_path):
    config = replace(CryptoTradingConfig(), exchange_provider="hyperliquid", state_dir=tmp_path)

    summary = summarize_stream_status(
        config,
        symbols=("BTC",),
        now=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
    )

    assert summary.fresh is False
    assert summary.events_read == 0
    assert {row.channel for row in summary.missing_or_stale} == {
        "allMids",
        "l2Book",
        "trades",
        "candle",
        "activeAssetCtx",
    }


def test_stream_status_accepts_fresh_required_channels(tmp_path):
    archive = tmp_path / "events" / "hyperliquid-ws-20260720.jsonl"
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    _write_required_events(archive, now - timedelta(seconds=30), symbol="BTC")
    config = replace(CryptoTradingConfig(), exchange_provider="hyperliquid", state_dir=tmp_path)

    summary = summarize_stream_status(
        config,
        symbols=("BTC",),
        max_age_seconds=60,
        now=now,
    )

    assert summary.fresh is True
    assert summary.events_read == 5
    assert all(row.count == 1 for row in summary.rows)


def test_stream_status_marks_stale_channels(tmp_path):
    archive = tmp_path / "events" / "hyperliquid-ws-20260720.jsonl"
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    _write_required_events(archive, now - timedelta(seconds=120), symbol="BTC")
    config = replace(CryptoTradingConfig(), exchange_provider="hyperliquid", state_dir=tmp_path)

    summary = summarize_stream_status(
        config,
        symbols=("BTC",),
        max_age_seconds=60,
        now=now,
    )

    assert summary.fresh is False
    assert all(row.age_seconds == 120 for row in summary.rows)
    assert all("older than 60 seconds" in row.message for row in summary.rows)


def test_live_readiness_reports_realtime_stream_evidence(tmp_path):
    archive = tmp_path / "events" / "hyperliquid-ws-20260720.jsonl"
    _write_required_events(archive, datetime.now(UTC), symbol="BTC")
    config = replace(
        CryptoTradingConfig(),
        exchange_provider="hyperliquid",
        state_dir=tmp_path,
        symbols=("BTC",),
    )

    report = LiveReadinessChecker(config).run(target="live")
    stream_check = next(check for check in report.checks if check.name == "realtime_stream_evidence")

    assert stream_check.status == "PASS"
    assert "Fresh HYPERLIQUID WebSocket archive found" in stream_check.message


def _write_required_events(path, received_at, *, symbol: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"channel": "allMids", "symbols": [], "received_at": received_at.isoformat()},
        {"channel": "l2Book", "symbols": [symbol], "received_at": received_at.isoformat()},
        {"channel": "trades", "symbols": [symbol], "received_at": received_at.isoformat()},
        {"channel": "candle", "symbols": [symbol], "received_at": received_at.isoformat()},
        {"channel": "activeAssetCtx", "symbols": [symbol], "received_at": received_at.isoformat()},
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
