"""Guarded account preparation for OKX demo execution."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .config import CryptoTradingConfig
from .okx_client import OKXClient
from .okx_execution import OKXExecutionAdapter, OKXExecutionReadiness


class OKXDemoAccountSetupError(RuntimeError):
    """Raised when the demo account cannot be prepared safely."""


@dataclass(frozen=True)
class OKXDemoAccountSetupResult:
    symbol: str
    position_mode_changed: bool
    leverage_set: bool
    readiness: OKXExecutionReadiness


class OKXDemoAccountPreparer:
    """Prepare an empty OKX demo account for guarded long-only execution."""

    def __init__(self, config: CryptoTradingConfig, client: OKXClient | None = None):
        self.config = config
        self.client = client or OKXClient(config)

    def prepare(self, symbol: str) -> OKXDemoAccountSetupResult:
        self._validate_local_policy()
        account = self.client.get_account_config()
        permissions = _permissions(account)
        if "trade" not in permissions:
            raise OKXDemoAccountSetupError("OKX demo API key lacks trade permission.")
        if "withdraw" in permissions:
            raise OKXDemoAccountSetupError(
                "Use a dedicated OKX demo API key without withdrawal permission."
            )

        instrument = self.client.get_instrument(symbol)
        if not (
            instrument.inst_type == "SWAP"
            and instrument.contract_type == "linear"
            and instrument.settle_ccy == "USDT"
            and instrument.state == "live"
        ):
            raise OKXDemoAccountSetupError(
                "OKX demo preparation requires a live linear USDT perpetual contract."
            )

        active_positions = [row for row in self.client.get_positions() if _position_size(row)]
        if active_positions:
            instruments = ", ".join(
                sorted({str(row.get("instId", "unknown")) for row in active_positions})
            )
            raise OKXDemoAccountSetupError(
                f"Close all OKX positions before changing position mode: {instruments}."
            )

        pending_orders = self.client.get_pending_orders()
        if pending_orders:
            instruments = ", ".join(
                sorted({str(row.get("instId", "unknown")) for row in pending_orders})
            )
            raise OKXDemoAccountSetupError(
                f"Cancel all OKX pending orders before changing position mode: {instruments}."
            )

        position_mode_changed = str(account.get("posMode", "")) != "net_mode"
        if position_mode_changed:
            self.client.set_position_mode("net_mode")

        self.client.set_leverage(symbol, 1, self.config.okx_td_mode)
        readiness = OKXExecutionAdapter(self.config, client=self.client).readiness(symbol)
        if not readiness.ready:
            failures = "; ".join(
                check.message for check in readiness.checks if check.status == "FAIL"
            )
            raise OKXDemoAccountSetupError(
                f"OKX demo account settings were applied but readiness still failed: {failures}"
            )
        return OKXDemoAccountSetupResult(
            symbol=OKXClient.normalize_symbol(symbol),
            position_mode_changed=position_mode_changed,
            leverage_set=True,
            readiness=readiness,
        )

    def _validate_local_policy(self) -> None:
        if not self.config.okx_demo:
            raise OKXDemoAccountSetupError("Real OKX account preparation is blocked.")
        if not self.config.okx_demo_execution_enabled:
            raise OKXDemoAccountSetupError("Enable the OKX demo execution switch first.")
        if not self.client.has_credentials:
            raise OKXDemoAccountSetupError("OKX demo credentials are missing.")
        if self.config.okx_inst_type.strip().upper() != "SWAP":
            raise OKXDemoAccountSetupError("OKX demo preparation supports SWAP only.")
        if self.config.okx_max_leverage != 1:
            raise OKXDemoAccountSetupError("The configured OKX leverage cap must be 1.")
        if self.config.okx_td_mode != "cross":
            raise OKXDemoAccountSetupError("Initial OKX demo preparation requires cross mode.")
        if self.config.emergency_stop_file is None:
            raise OKXDemoAccountSetupError("Configure the emergency stop file path first.")
        if self.config.emergency_stop_file.exists():
            raise OKXDemoAccountSetupError(
                f"Emergency stop file exists: {self.config.emergency_stop_file}"
            )


def _permissions(account: dict) -> set[str]:
    return {
        item.strip().lower()
        for item in str(account.get("perm", "")).split(",")
        if item.strip()
    }


def _position_size(row: dict) -> Decimal:
    try:
        return abs(Decimal(str(row.get("pos", "0") or "0")))
    except InvalidOperation:
        return Decimal("1")
