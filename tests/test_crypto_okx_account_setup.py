from __future__ import annotations

from dataclasses import replace

import pytest

from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.okx_account_setup import (
    OKXDemoAccountPreparer,
    OKXDemoAccountSetupError,
)
from tradingagents.crypto.okx_client import OKXInstrument


def test_okx_demo_account_preparer_sets_net_mode_and_one_x(tmp_path):
    client = _FakeSetupClient()
    result = OKXDemoAccountPreparer(_config(tmp_path), client=client).prepare("BTC")

    assert result.readiness.ready is True
    assert result.position_mode_changed is True
    assert result.leverage_set is True
    assert client.position_mode_updates == ["net_mode"]
    assert client.leverage_updates == [("BTC", 1, "cross")]


def test_okx_demo_account_preparer_blocks_existing_position(tmp_path):
    client = _FakeSetupClient(positions=[{"instId": "ETH-USDT-SWAP", "pos": "2"}])

    with pytest.raises(OKXDemoAccountSetupError, match="Close all OKX positions"):
        OKXDemoAccountPreparer(_config(tmp_path), client=client).prepare("BTC")

    assert client.position_mode_updates == []
    assert client.leverage_updates == []


def test_okx_demo_account_preparer_blocks_pending_order(tmp_path):
    client = _FakeSetupClient(pending_orders=[{"instId": "SOL-USDT-SWAP"}])

    with pytest.raises(OKXDemoAccountSetupError, match="Cancel all OKX pending orders"):
        OKXDemoAccountPreparer(_config(tmp_path), client=client).prepare("BTC")

    assert client.position_mode_updates == []
    assert client.leverage_updates == []


def test_okx_demo_account_preparer_blocks_real_api_mode(tmp_path):
    config = replace(_config(tmp_path), okx_demo=False)

    with pytest.raises(OKXDemoAccountSetupError, match="Real OKX account preparation"):
        OKXDemoAccountPreparer(config, client=_FakeSetupClient()).prepare("BTC")


def _config(tmp_path) -> CryptoTradingConfig:
    return replace(
        CryptoTradingConfig(),
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
        emergency_stop_file=tmp_path / "STOP",
    )


class _FakeSetupClient:
    def __init__(self, *, positions=None, pending_orders=None):
        self.position_mode = "long_short_mode"
        self.leverage = "3"
        self.positions = positions or []
        self.pending_orders = pending_orders or []
        self.position_mode_updates = []
        self.leverage_updates = []

    @property
    def has_credentials(self):
        return True

    def get_account_config(self):
        return {
            "posMode": self.position_mode,
            "perm": "read_only,trade",
        }

    def get_positions(self, symbol=None):
        return self.positions

    def get_pending_orders(self):
        return self.pending_orders

    def set_position_mode(self, position_mode):
        self.position_mode = position_mode
        self.position_mode_updates.append(position_mode)
        return {"posMode": position_mode}

    def set_leverage(self, symbol, leverage, margin_mode):
        self.leverage = str(leverage)
        self.leverage_updates.append((symbol, leverage, margin_mode))
        return {"instId": "BTC-USDT-SWAP", "lever": str(leverage), "mgnMode": margin_mode}

    def get_instrument(self, _symbol):
        return OKXInstrument(
            inst_id="BTC-USDT-SWAP",
            inst_type="SWAP",
            base_ccy="BTC",
            quote_ccy="USDT",
            settle_ccy="USDT",
            min_size=1,
            lot_size=1,
            tick_size=0.1,
            contract_value=0.01,
            contract_type="linear",
            contract_value_ccy="BTC",
            state="live",
        )

    def get_leverage_info(self, _symbol, _margin_mode):
        return [{"instId": "BTC-USDT-SWAP", "lever": self.leverage}]

    def instrument_id(self, _symbol):
        return "BTC-USDT-SWAP"
