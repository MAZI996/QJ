from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.execution import ExecutionRouter
from tradingagents.crypto.models import OrderIntent
from tradingagents.crypto.okx_client import OKXInstrument, OKXTransportError
from tradingagents.crypto.okx_execution import OKXExecutionAdapter
from tradingagents.crypto.positions import PositionStore


def test_okx_demo_buy_converts_base_quantity_and_attaches_protection():
    client = _FakeOKXExecutionClient()
    adapter = OKXExecutionAdapter(
        _config(),
        client=client,
        sleep=lambda _seconds: None,
        client_order_id_factory=lambda: "taentry1",
    )

    result = adapter.execute(_intent(), mode="testnet")

    assert result.accepted is True
    assert result.quantity == 0.1
    assert client.orders == [
        {
            "instId": "BTC-USDT-SWAP",
            "tdMode": "cross",
            "clOrdId": "taentry1",
            "tag": "tradingagents",
            "side": "buy",
            "ordType": "market",
            "sz": "10",
            "reduceOnly": False,
            "attachAlgoOrds": [
                {
                    "attachAlgoClOrdId": "taentry1p",
                    "tpTriggerPx": "110",
                    "tpOrdPx": "-1",
                    "tpTriggerPxType": "mark",
                    "slTriggerPx": "95",
                    "slOrdPx": "-1",
                    "slTriggerPxType": "mark",
                }
            ],
        }
    ]
    assert result.exchange_payload["filled_contracts"] == "10"
    assert result.exchange_payload["fills"] == [{"qty": 0.1, "price": 100.2}]


def test_okx_demo_reduce_only_exit_caps_to_the_actual_long_position():
    client = _FakeOKXExecutionClient(position_contracts="5")
    adapter = OKXExecutionAdapter(
        _config(),
        client=client,
        sleep=lambda _seconds: None,
        client_order_id_factory=lambda: "taexit1",
    )
    intent = replace(
        _intent(),
        side="SELL",
        reduce_only=True,
        stop_loss=None,
        take_profit=None,
        reason="position_guardian:stop_loss_hit",
    )

    result = adapter.execute(intent, mode="testnet")

    assert result.accepted is True
    assert result.quantity == 0.05
    assert client.orders[0]["side"] == "sell"
    assert client.orders[0]["sz"] == "5"
    assert client.orders[0]["reduceOnly"] is True
    assert "attachAlgoOrds" not in client.orders[0]


def test_okx_demo_execution_blocks_account_leverage_above_one():
    client = _FakeOKXExecutionClient(leverage="2")

    result = OKXExecutionAdapter(_config(), client=client).execute(
        _intent(),
        mode="testnet",
    )

    assert result.accepted is False
    assert "set account leverage to 1" in result.message
    assert client.orders == []


def test_okx_demo_execution_blocks_read_only_api_key():
    client = _FakeOKXExecutionClient(permission="read_only")

    result = OKXExecutionAdapter(_config(), client=client).execute(
        _intent(),
        mode="testnet",
    )

    assert result.accepted is False
    assert "does not have trade permission" in result.message
    assert client.orders == []


def test_okx_demo_execution_rejects_withdraw_enabled_api_key():
    client = _FakeOKXExecutionClient(permission="read_only,trade,withdraw")

    result = OKXExecutionAdapter(_config(), client=client).execute(
        _intent(),
        mode="testnet",
    )

    assert result.accepted is False
    assert "without withdrawal permission" in result.message
    assert client.orders == []


def test_okx_demo_execution_blocks_non_reduce_only_sell():
    client = _FakeOKXExecutionClient()
    intent = replace(_intent(), side="SELL", reduce_only=False)

    result = OKXExecutionAdapter(_config(), client=client).execute(
        intent,
        mode="testnet",
    )

    assert result.accepted is False
    assert "short selling is disabled" in result.message
    assert client.orders == []


def test_okx_execution_keeps_live_mode_hard_blocked():
    result = OKXExecutionAdapter(
        replace(_config(), enable_live_orders=True),
        client=_FakeOKXExecutionClient(),
    ).execute(
        _intent(),
        mode="live",
        live_confirmation="I_UNDERSTAND_THIS_PLACES_REAL_OKX_ORDERS",
    )

    assert result.accepted is False
    assert "live execution is not implemented" in result.message


def test_okx_demo_execution_blocks_when_emergency_stop_is_active(tmp_path):
    emergency_stop = tmp_path / "STOP"
    emergency_stop.touch()

    result = OKXExecutionAdapter(
        replace(_config(), emergency_stop_file=emergency_stop),
        client=_FakeOKXExecutionClient(),
    ).execute(_intent(), mode="testnet")

    assert result.accepted is False
    assert "Emergency stop file exists" in result.message


def test_okx_demo_emergency_stop_allows_reduce_only_exit(tmp_path):
    emergency_stop = tmp_path / "STOP"
    emergency_stop.touch()
    client = _FakeOKXExecutionClient(position_contracts="5")
    intent = replace(
        _intent(),
        side="SELL",
        reduce_only=True,
        stop_loss=None,
        take_profit=None,
        reason="position_guardian:emergency_exit",
    )

    result = OKXExecutionAdapter(
        replace(_config(), emergency_stop_file=emergency_stop),
        client=client,
        sleep=lambda _seconds: None,
        client_order_id_factory=lambda: "taexitstop1",
    ).execute(intent, mode="testnet")

    assert result.accepted is True
    assert client.orders[0]["side"] == "sell"
    assert client.orders[0]["reduceOnly"] is True


def test_okx_demo_recovers_transport_timeout_by_client_order_id(tmp_path):
    client = _FakeOKXExecutionClient(transport_error_on_place=True)
    adapter = OKXExecutionAdapter(
        replace(_config(), emergency_stop_file=tmp_path / "STOP"),
        client=client,
        client_order_id_factory=lambda: "tarecover1",
    )

    result = adapter.execute(_intent(), mode="testnet")

    assert result.accepted is True
    assert result.exchange_payload["clientOrderId"] == "tarecover1"
    assert not (tmp_path / "STOP").exists()


def test_okx_demo_activates_emergency_stop_for_unresolved_order(tmp_path):
    emergency_stop = tmp_path / "STOP"
    client = _FakeOKXExecutionClient(order_state="live")
    adapter = OKXExecutionAdapter(
        replace(
            _config(),
            emergency_stop_file=emergency_stop,
            okx_order_poll_attempts=1,
        ),
        client=client,
        sleep=lambda _seconds: None,
        client_order_id_factory=lambda: "taunresolved1",
    )

    result = adapter.execute(_intent(), mode="testnet")

    assert result.accepted is False
    assert "Emergency stop activated" in result.message
    assert emergency_stop.exists()


def test_okx_execution_router_tracks_only_the_confirmed_fill(tmp_path):
    config = replace(_config(), state_dir=tmp_path)
    client = _FakeOKXExecutionClient()
    router = ExecutionRouter(client, config)
    router.okx_execution = OKXExecutionAdapter(
        config,
        client=client,
        sleep=lambda _seconds: None,
        client_order_id_factory=lambda: "tarouter1",
    )

    result = router.execute(_intent(), mode="testnet")
    position = PositionStore.from_state_dir(tmp_path).load()["BTC"]

    assert result.accepted is True
    assert position.quantity == 0.1
    assert position.avg_entry_price == 100.2


def test_okx_demo_readiness_reports_missing_credentials():
    config = replace(
        _config(),
        okx_api_key="",
        okx_api_secret="",
        okx_api_passphrase="",
    )

    report = OKXExecutionAdapter(config).readiness("BTC")

    assert report.ready is False
    assert {check.name: check.status for check in report.checks}["credentials"] == "FAIL"


def test_okx_demo_readiness_passes_account_and_execution_checks():
    report = OKXExecutionAdapter(
        _config(),
        client=_FakeOKXExecutionClient(),
    ).readiness("BTC")

    assert report.ready is True
    statuses = {check.name: check.status for check in report.checks}
    assert statuses["trade_permission"] == "PASS"
    assert statuses["no_withdraw_permission"] == "PASS"
    assert statuses["position_mode"] == "PASS"
    assert statuses["leverage"] == "PASS"


def _config() -> CryptoTradingConfig:
    return replace(
        CryptoTradingConfig(),
        exchange_provider="okx",
        okx_demo=True,
        okx_demo_execution_enabled=True,
        okx_api_key="key",
        okx_api_secret="secret",
        okx_api_passphrase="passphrase",
        okx_inst_type="SWAP",
        okx_max_leverage=1,
        okx_td_mode="cross",
        okx_require_net_mode=True,
        okx_require_protective_orders=True,
        protective_oco_enabled=True,
        emergency_stop_file=Path("tests/.okx-test-emergency-stop"),
    )


def _intent() -> OrderIntent:
    return OrderIntent(
        symbol="BTC",
        side="BUY",
        quantity=0.1,
        notional_usdt=10.0,
        entry_price=100.0,
        stop_loss=95.03,
        take_profit=110.09,
        reason="test entry",
    )


class _FakeOKXExecutionClient:
    def __init__(
        self,
        *,
        position_contracts: str = "10",
        leverage: str = "1",
        permission: str = "read_only,trade",
        transport_error_on_place: bool = False,
        order_state: str = "filled",
    ):
        self.position_contracts = position_contracts
        self.leverage = leverage
        self.permission = permission
        self.transport_error_on_place = transport_error_on_place
        self.order_state = order_state
        self.orders: list[dict] = []

    @property
    def has_credentials(self) -> bool:
        return True

    def instrument_id(self, _symbol: str) -> str:
        return "BTC-USDT-SWAP"

    def get_instrument(self, _symbol: str) -> OKXInstrument:
        return OKXInstrument(
            inst_id="BTC-USDT-SWAP",
            inst_type="SWAP",
            base_ccy="BTC",
            quote_ccy="USDT",
            settle_ccy="USDT",
            min_size=1.0,
            lot_size=1.0,
            tick_size=0.1,
            contract_value=0.01,
            contract_type="linear",
            contract_value_ccy="BTC",
            state="live",
        )

    def get_account_config(self) -> dict:
        return {"posMode": "net_mode", "perm": self.permission}

    def get_leverage_info(self, _symbol: str, _margin_mode: str) -> list[dict]:
        return [{"instId": "BTC-USDT-SWAP", "lever": self.leverage}]

    def get_positions(self, _symbol: str) -> list[dict]:
        return [
            {
                "instId": "BTC-USDT-SWAP",
                "posSide": "net",
                "pos": self.position_contracts,
            }
        ]

    def place_order(self, params: dict) -> dict:
        self.orders.append(params)
        if self.transport_error_on_place:
            raise OKXTransportError("simulated timeout")
        return {"ordId": "okx-1", "clOrdId": params["clOrdId"], "sCode": "0"}

    def get_order(
        self,
        _symbol: str,
        *,
        order_id: str = "",
        client_order_id: str = "",
    ) -> dict:
        assert order_id == "okx-1" or client_order_id
        return {
            "ordId": order_id or "okx-1",
            "clOrdId": client_order_id or self.orders[-1]["clOrdId"],
            "state": self.order_state,
            "accFillSz": self.orders[-1]["sz"],
            "avgPx": "100.2" if self.order_state == "filled" else "",
        }

    def cancel_order(self, _symbol: str, *, order_id: str = "") -> dict:
        return {"ordId": order_id, "sCode": "0"}
