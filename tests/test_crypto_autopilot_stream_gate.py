from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import tradingagents.crypto.autopilot as autopilot_module
from tradingagents.crypto.autopilot import CryptoAutoPilot
from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.decision_journal import DecisionJournalWrite
from tradingagents.crypto.workflow_report import CryptoWorkflowReport


def test_autopilot_stops_before_scan_when_stream_is_missing(tmp_path, monkeypatch):
    config = replace(
        CryptoTradingConfig(),
        exchange_provider="hyperliquid",
        state_dir=tmp_path,
        symbols=("BTC",),
    )

    def fail_engine(_config):
        raise AssertionError("scanner engine should not start when stream evidence is stale")

    monkeypatch.setattr(autopilot_module, "CryptoTradingEngine", fail_engine)

    result = CryptoAutoPilot(config).run_once(
        symbols=("BTC",),
        guard_positions=False,
        require_fresh_stream=True,
    )

    assert result.stopped is True
    assert result.report is None
    assert result.stream_status is not None
    assert result.stream_status.fresh is False
    assert "WebSocket stream is stale or missing" in result.reason


def test_autopilot_can_bypass_stream_gate_for_manual_diagnostics(tmp_path, monkeypatch):
    config = replace(
        CryptoTradingConfig(),
        exchange_provider="hyperliquid",
        state_dir=tmp_path,
        symbols=("BTC",),
    )
    _patch_workflow(monkeypatch, mode="analysis")

    result = CryptoAutoPilot(config).run_once(
        symbols=("BTC",),
        guard_positions=False,
        require_fresh_stream=False,
    )

    assert result.stopped is False
    assert result.report is not None
    assert result.stream_status is None


def test_autopilot_records_fresh_stream_context(tmp_path, monkeypatch):
    archive = tmp_path / "events" / "hyperliquid-ws-20260720.jsonl"
    _write_required_events(archive, datetime.now(UTC), symbol="BTC")
    captured = _patch_workflow(monkeypatch, mode="paper")
    config = replace(
        CryptoTradingConfig(),
        exchange_provider="hyperliquid",
        state_dir=tmp_path,
        symbols=("BTC",),
    )

    result = CryptoAutoPilot(config).run_once(
        symbols=("BTC",),
        execution_mode="paper",
        guard_positions=False,
        require_fresh_stream=True,
        stream_archive_path=archive,
        stream_max_age_seconds=600,
    )

    assert result.stopped is False
    assert result.stream_status is not None
    assert result.stream_status.fresh is True
    stream_context = captured["context"]["stream_freshness"]
    assert stream_context["fresh"] is True
    assert stream_context["events_read"] == 5
    assert stream_context["missing_or_stale"] == []


def _patch_workflow(monkeypatch, *, mode):
    captured = {}

    class FakeEngine:
        def __init__(self, _config):
            self.client = object()

    class FakeWorkflow:
        def __init__(self, config, engine):
            self.config = config
            self.engine = engine

        def run(self, **_kwargs):
            return CryptoWorkflowReport(
                reviewed=(),
                ai_review=None,
                role_reports=(),
                execution_mode=mode,
            )

    def fake_write(report, state_dir, context):
        captured["report"] = report
        captured["state_dir"] = state_dir
        captured["context"] = context
        return DecisionJournalWrite(
            run_id="test",
            jsonl_path=Path(state_dir) / "decision_journal.jsonl",
            json_path=Path(state_dir) / "reports" / "workflow-test.json",
            markdown_path=Path(state_dir) / "reports" / "workflow-test.md",
        )

    monkeypatch.setattr(autopilot_module, "CryptoTradingEngine", FakeEngine)
    monkeypatch.setattr(autopilot_module, "CryptoTradingAgentsWorkflow", FakeWorkflow)
    monkeypatch.setattr(autopilot_module, "write_workflow_report", fake_write)
    return captured


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
