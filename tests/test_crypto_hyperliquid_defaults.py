from __future__ import annotations

import json
import urllib.error
from dataclasses import replace
from datetime import UTC, datetime

from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.execution import ExecutionRouter
from tradingagents.crypto.hyperliquid_client import HyperliquidClient
from tradingagents.crypto.hyperliquid_execution import HyperliquidExecutionAdapter
from tradingagents.crypto.live_readiness import LiveReadinessChecker
from tradingagents.crypto.models import OpportunitySignal, OrderIntent, SymbolRules
from tradingagents.crypto.order_recovery import OrderRecoveryService
from tradingagents.crypto.paper_status import summarize_paper_status
from tradingagents.crypto.position_guardian import PositionGuardian
from tradingagents.crypto.positions import PositionStore
from tradingagents.crypto.risk import RiskManager


def test_crypto_config_defaults_to_okx(monkeypatch):
    for key in (
        "TRADINGAGENTS_CRYPTO_EXCHANGE_PROVIDER",
        "TRADINGAGENTS_CRYPTO_SYMBOLS",
        "TRADINGAGENTS_CRYPTO_LIVE_CONFIRM_PHRASE",
        "TRADINGAGENTS_CRYPTO_OKX_DEMO",
    ):
        monkeypatch.delenv(key, raising=False)

    config = CryptoTradingConfig.from_env()

    assert config.exchange_provider == "okx"
    assert config.symbols == ("BTC", "ETH", "SOL", "XRP")
    assert config.okx_demo is True
    assert config.okx_inst_type == "SWAP"
    assert config.okx_max_leverage == 1
    assert config.resolved_okx_ws_public_url.endswith("/ws/v5/public")
    assert config.resolved_okx_ws_business_url.endswith("/ws/v5/business")
    assert config.live_confirm_phrase == "I_UNDERSTAND_THIS_PLACES_REAL_OKX_ORDERS"
    assert config.hyperliquid_sdk_execution_enabled is False
    assert config.hyperliquid_require_protective_orders is True
    assert config.entry_quality_enabled is True
    assert config.entry_quality_min_close_position == 0.55
    assert config.position_guardian_enabled is True
    assert config.position_guardian_strategy_exit_enabled is False


def test_hyperliquid_risk_rejects_leverage_above_one():
    config = replace(
        CryptoTradingConfig(),
        exchange_provider="hyperliquid",
        hyperliquid_max_leverage=2,
    )
    decision = RiskManager(config).evaluate(_signal(), _rules())

    assert decision.approved is False
    assert "Hyperliquid 初始阶段杠杆上限必须为 1" in decision.rejected_rules


def test_hyperliquid_risk_accepts_valid_one_x_long_candidate():
    config = replace(
        CryptoTradingConfig(),
        exchange_provider="hyperliquid",
        hyperliquid_max_leverage=1,
    )
    decision = RiskManager(config).evaluate(_signal(), _rules())

    assert decision.approved is True
    assert decision.intent is not None
    assert decision.intent.symbol == "BTC"


def test_hyperliquid_execution_blocks_when_sdk_flag_disabled(tmp_path):
    config = replace(CryptoTradingConfig(), exchange_provider="hyperliquid", state_dir=tmp_path)
    result = ExecutionRouter(client=object(), config=config).execute(
        _intent(),
        mode="testnet",
    )

    assert result.accepted is False
    assert result.mode == "testnet"
    assert "SDK execution is disabled" in result.message


def test_hyperliquid_info_retries_transient_url_error(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"BTC":"100"}'

    def fake_urlopen(_request, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise urllib.error.URLError("temporary network reset")
        return Response()

    monkeypatch.setattr(
        "tradingagents.crypto.hyperliquid_client.urllib.request.urlopen",
        fake_urlopen,
    )
    monkeypatch.setattr("tradingagents.crypto.hyperliquid_client.time.sleep", lambda _seconds: None)

    mids = HyperliquidClient(CryptoTradingConfig()).get_all_mids()

    assert mids == {"BTC": 100.0}
    assert len(calls) == 2


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


def test_hyperliquid_rejects_non_reduce_only_sell():
    config = replace(
        CryptoTradingConfig(),
        hyperliquid_sdk_execution_enabled=True,
        hyperliquid_wallet_address="0x1111111111111111111111111111111111111111",
        hyperliquid_private_key="0x" + "1" * 64,
    )
    sell_intent = replace(_intent(), side="SELL", reduce_only=False)

    result = HyperliquidExecutionAdapter(config)._blocking_reason(
        sell_intent,
        mode="testnet",
        live_confirmation="",
    )

    assert "short selling is disabled" in result


def test_hyperliquid_reduce_only_sell_submits_close_order(monkeypatch):
    config = replace(
        CryptoTradingConfig(),
        hyperliquid_sdk_execution_enabled=True,
        hyperliquid_wallet_address="0x1111111111111111111111111111111111111111",
        hyperliquid_private_key="0x" + "1" * 64,
    )
    orders = []

    class FakeExchange:
        def bulk_orders(self, payload, grouping=None):
            orders.extend(payload)
            return {"status": "ok", "orders": payload, "grouping": grouping}

    monkeypatch.setattr("tradingagents.crypto.hyperliquid_execution._sdk_available", lambda: True)
    monkeypatch.setattr(HyperliquidExecutionAdapter, "_exchange", lambda _self: FakeExchange())

    sell_intent = OrderIntent(
        symbol="BTC",
        side="SELL",
        quantity=0.25,
        notional_usdt=25.0,
        entry_price=100.0,
        stop_loss=None,
        take_profit=None,
        reason="position_guardian:stop_loss_hit",
        reduce_only=True,
    )

    result = HyperliquidExecutionAdapter(config).execute(sell_intent, mode="testnet")

    assert result.accepted is True
    assert orders == [
        {
            "coin": "BTC",
            "is_buy": False,
            "sz": 0.25,
            "limit_px": 99.0,
            "order_type": {"limit": {"tif": "Ioc"}},
            "reduce_only": True,
        }
    ]


def test_position_guardian_paper_closes_stop_loss_position(tmp_path):
    config = replace(CryptoTradingConfig(), state_dir=tmp_path)
    store = PositionStore.from_state_dir(tmp_path)
    store.apply_fill(
        symbol="BTC",
        side="BUY",
        quantity=0.25,
        price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        order_id="entry-1",
        notes="test_entry",
    )

    result = PositionGuardian(
        _FakeMarkClient({"BTC": 94.0}),
        config,
        positions=store,
    ).run(mode="paper", execute=True)
    record = PositionStore.from_state_dir(tmp_path).load()["BTC"]

    assert result.accepted_closes[0].reason.startswith("stop_loss_hit")
    assert result.accepted_closes[0].execution is not None
    assert record.status == "CLOSED"
    assert record.quantity == 0.0
    assert record.realized_pnl_usdt == -1.5


def test_hyperliquid_recovery_syncs_clearinghouse_position(tmp_path):
    config = replace(
        CryptoTradingConfig(),
        state_dir=tmp_path,
        exchange_provider="hyperliquid",
    )
    service = OrderRecoveryService(
        _FakeHyperliquidRecoveryClient(),
        config,
        positions=PositionStore.from_state_dir(tmp_path),
    )

    result = service.recover_symbol("BTCUSDT")
    record = PositionStore.from_state_dir(tmp_path).load()["BTC"]

    assert result.position_updated is True
    assert result.open_orders == 1
    assert result.trades_seen == 1
    assert record.quantity == 0.25
    assert record.avg_entry_price == 100.0
    assert record.notes == "hyperliquid_clearinghouse_recovery"


def test_hyperliquid_recovery_refuses_to_sync_short_position(tmp_path):
    config = replace(
        CryptoTradingConfig(),
        state_dir=tmp_path,
        exchange_provider="hyperliquid",
    )
    service = OrderRecoveryService(
        _FakeHyperliquidRecoveryClient(size="-0.25"),
        config,
        positions=PositionStore.from_state_dir(tmp_path),
    )

    result = service.recover_symbol("BTC")

    assert result.position_updated is False
    assert "short position detected" in result.message
    assert PositionStore.from_state_dir(tmp_path).load() == {}


def test_live_readiness_blocks_default_live_config(tmp_path):
    config = replace(CryptoTradingConfig(), exchange_provider="hyperliquid", state_dir=tmp_path)

    report = LiveReadinessChecker(config).run(target="live")

    assert report.ready is False
    failed_names = {check.name for check in report.failures}
    assert "hyperliquid_testnet" in failed_names
    assert "live_order_switch" in failed_names
    assert "sdk_execution_enabled" in failed_names
    assert "protective_orders_enabled" in failed_names
    assert "paper_evidence" in failed_names


def test_live_readiness_requires_position_guardian_for_live(tmp_path):
    config = replace(
        CryptoTradingConfig(),
        exchange_provider="hyperliquid",
        state_dir=tmp_path,
        position_guardian_enabled=False,
    )

    report = LiveReadinessChecker(config).run(target="live")

    failed_names = {check.name for check in report.failures}
    assert "position_guardian_enabled" in failed_names


def test_paper_readiness_allows_safe_default_with_warnings(tmp_path):
    config = replace(CryptoTradingConfig(), exchange_provider="hyperliquid", state_dir=tmp_path)

    report = LiveReadinessChecker(config).run(target="paper")

    assert report.ready is True
    warning_names = {check.name for check in report.warnings}
    assert "paper_evidence" in warning_names


def test_paper_status_summarizes_journal_orders_and_queue(tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    run_id = "abc123"
    (tmp_path / "decision_journal.jsonl").write_text(
        (
            '{"run_id":"abc123","created_at":"2026-05-18T00:00:00+00:00",'
            '"summary":{"final_action":"REJECT","top_symbol":null}}\n'
        ),
        encoding="utf-8",
    )
    (report_dir / f"workflow-20260518T000000Z-{run_id}.md").write_text(
        "# report\n",
        encoding="utf-8",
    )
    (tmp_path / "paper_orders.jsonl").write_text('{"status":"FILLED_SIMULATED"}\n', encoding="utf-8")
    (tmp_path / "paper_queue.json").write_text(
        (
            '{"ready_count":1,"items":[{"command":"python -m cli.main crypto-autopilot '
            '--mode paper","review_note":"review later"}]}'
        ),
        encoding="utf-8",
    )

    summary = summarize_paper_status(replace(CryptoTradingConfig(), state_dir=tmp_path))

    assert summary.decision_runs == 1
    assert summary.paper_orders == 1
    assert summary.stream_evidence.archive_fresh is False
    assert summary.stream_evidence.autopilot_stream_cycles == 0
    assert summary.last_action == "REJECT"
    assert summary.last_top_symbol == "-"
    assert summary.last_report_path == report_dir / f"workflow-20260518T000000Z-{run_id}.md"
    assert summary.queue_ready_count == 1
    assert "--mode paper" in summary.queue_top_command


def test_paper_status_summarizes_stream_evidence(tmp_path):
    now = datetime.now(UTC)
    archive = tmp_path / "events" / "hyperliquid-ws-20260720.jsonl"
    _write_required_stream_events(archive, now, symbol="BTC")
    (tmp_path / "decision_journal.jsonl").write_text(
        (
            json.dumps(
                {
                    "run_id": "fresh",
                    "created_at": now.isoformat(),
                    "execution_mode": "paper",
                    "context": {
                        "command": "crypto-autopilot",
                        "stream_freshness": {"fresh": True},
                    },
                    "summary": {"final_action": "BUY", "top_symbol": "BTC"},
                }
            )
            + "\n"
            + json.dumps(
                {
                    "run_id": "stale",
                    "created_at": now.isoformat(),
                    "execution_mode": "paper",
                    "context": {
                        "command": "crypto-autopilot",
                        "stream_freshness": {"fresh": False},
                    },
                    "summary": {"final_action": "STOP", "top_symbol": None},
                }
            )
            + "\n"
        ),
        encoding="utf-8",
    )
    config = replace(
        CryptoTradingConfig(),
        exchange_provider="hyperliquid",
        state_dir=tmp_path,
        symbols=("BTC",),
    )

    summary = summarize_paper_status(config, stream_max_age_seconds=600)

    assert summary.stream_evidence.archive_fresh is True
    assert summary.stream_evidence.archive_events == 5
    assert summary.stream_evidence.required_channels == 5
    assert summary.stream_evidence.fresh_channels == 5
    assert summary.stream_evidence.autopilot_stream_cycles == 2
    assert summary.stream_evidence.autopilot_fresh_stream_cycles == 1
    assert summary.stream_evidence.autopilot_stale_stream_cycles == 1
    assert summary.stream_evidence.fresh_cycle_ratio == 0.5


def _write_required_stream_events(path, received_at, *, symbol: str):
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


class _FakeHyperliquidRecoveryClient:
    def __init__(self, size: str = "0.25"):
        self.size = size

    def get_user_state(self):
        return {
            "assetPositions": [
                {
                    "position": {
                        "coin": "BTC",
                        "szi": self.size,
                        "entryPx": "100",
                    }
                }
            ]
        }

    def get_open_orders(self):
        return [{"coin": "BTC"}, {"coin": "ETH"}]


class _FakeMarkClient:
    def __init__(self, mids):
        self.mids = mids

    def get_all_mids(self):
        return self.mids
