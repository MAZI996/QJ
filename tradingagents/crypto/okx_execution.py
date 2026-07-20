"""Guarded OKX demo execution for linear perpetual contracts."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any, Callable

from .config import CryptoTradingConfig
from .models import ExecutionMode, OrderIntent, OrderResult
from .okx_client import OKXAPIError, OKXClient, OKXInstrument, OKXTransportError


class OKXExecutionError(RuntimeError):
    """Raised when an OKX demo order cannot pass local or exchange checks."""


@dataclass(frozen=True)
class OKXExecutionCheck:
    name: str
    status: str
    message: str


@dataclass(frozen=True)
class OKXExecutionReadiness:
    symbol: str
    ready: bool
    checks: tuple[OKXExecutionCheck, ...]


@dataclass(frozen=True)
class _Preflight:
    instrument: OKXInstrument
    contracts: Decimal


class OKXExecutionAdapter:
    """Submit demo orders after deterministic account and position checks."""

    def __init__(
        self,
        config: CryptoTradingConfig,
        client: OKXClient | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        client_order_id_factory: Callable[[], str] | None = None,
    ):
        self.config = config
        self.client = client or OKXClient(config)
        self.sleep = sleep
        self.client_order_id_factory = client_order_id_factory or _client_order_id

    def execute(
        self,
        intent: OrderIntent,
        mode: ExecutionMode,
        live_confirmation: str = "",
    ) -> OrderResult:
        reason = self._blocking_reason(intent, mode, live_confirmation)
        if reason:
            return _blocked(intent, mode, reason)

        order_id = ""
        try:
            preflight = self._preflight(intent)
            client_order_id = self.client_order_id_factory()
            params = self._order_params(intent, preflight, client_order_id)
            try:
                ack = self.client.place_order(params)
            except OKXTransportError as exc:
                try:
                    recovered = self.client.get_order(
                        intent.symbol,
                        client_order_id=client_order_id,
                    )
                except Exception:
                    self._activate_emergency_stop(
                        f"Ambiguous OKX order submission for {client_order_id}: {exc}"
                    )
                    return _blocked(
                        intent,
                        mode,
                        "OKX order submission outcome is unknown; emergency stop activated.",
                    )
                order_id = str(recovered.get("ordId", ""))
                if not order_id:
                    self._activate_emergency_stop(
                        f"Recovered OKX order has no order ID: {client_order_id}"
                    )
                    return _blocked(
                        intent,
                        mode,
                        "OKX recovered an order without an ID; emergency stop activated.",
                    )
                ack = {
                    "ordId": order_id,
                    "clOrdId": client_order_id,
                    "sCode": "0",
                    "sMsg": "Recovered by client order ID after transport error.",
                }
            if str(ack.get("sCode", "")) != "0":
                message = ack.get("sMsg") or ack.get("subCode") or "unknown rejection"
                return _blocked(intent, mode, f"OKX rejected demo order: {message}")
            order_id = order_id or str(ack.get("ordId", ""))
            if not order_id:
                self._activate_emergency_stop(
                    f"OKX accepted {client_order_id} without returning an order ID."
                )
                return _blocked(
                    intent,
                    mode,
                    "OKX accepted a request without an order ID; emergency stop activated.",
                )
            order = self._wait_for_order(intent.symbol, order_id)
        except (OKXAPIError, OKXExecutionError) as exc:
            suffix = self._stop_after_ambiguous_order(order_id, str(exc))
            return _blocked(intent, mode, f"{exc}{suffix}")
        except Exception as exc:
            suffix = self._stop_after_ambiguous_order(order_id, str(exc))
            return _blocked(intent, mode, f"OKX demo order failed: {exc}{suffix}")

        result = self._result_from_order(intent, mode, preflight, ack, order)
        if str(order.get("state", "")) not in {"filled", "canceled", "mmp_canceled"}:
            self._activate_emergency_stop(
                f"OKX order {order_id} remained unresolved after cancellation polling."
            )
            result = OrderResult(
                mode=result.mode,
                accepted=result.accepted,
                symbol=result.symbol,
                side=result.side,
                quantity=result.quantity,
                message=f"{result.message} Emergency stop activated.",
                exchange_payload=result.exchange_payload,
            )
        return result

    def readiness(self, symbol: str) -> OKXExecutionReadiness:
        checks = list(self._local_readiness_checks())
        normalized = OKXClient.normalize_symbol(symbol)
        if not self.client.has_credentials:
            return OKXExecutionReadiness(normalized, False, tuple(checks))

        try:
            instrument = self.client.get_instrument(symbol)
            checks.append(
                OKXExecutionCheck(
                    "linear_swap",
                    "PASS" if _is_supported_instrument(instrument) else "FAIL",
                    (
                        f"{instrument.inst_id} is a live linear USDT swap."
                        if _is_supported_instrument(instrument)
                        else f"Unsupported instrument contract: {instrument.inst_id}."
                    ),
                )
            )
            account = self.client.get_account_config()
            permissions = _permissions(account)
            checks.append(
                OKXExecutionCheck(
                    "trade_permission",
                    "PASS" if "trade" in permissions else "FAIL",
                    "The OKX demo API key has trade permission."
                    if "trade" in permissions
                    else "The OKX demo API key does not have trade permission.",
                )
            )
            checks.append(
                OKXExecutionCheck(
                    "no_withdraw_permission",
                    "PASS" if "withdraw" not in permissions else "FAIL",
                    "The OKX demo API key has no withdrawal permission."
                    if "withdraw" not in permissions
                    else "Use a dedicated demo key without withdrawal permission.",
                )
            )
            position_mode = str(account.get("posMode", ""))
            checks.append(
                OKXExecutionCheck(
                    "position_mode",
                    "PASS" if position_mode == "net_mode" else "FAIL",
                    f"OKX position mode is {position_mode or 'unknown'}; net_mode is required.",
                )
            )
            leverage = self._current_leverage(symbol)
            checks.append(
                OKXExecutionCheck(
                    "leverage",
                    "PASS" if 0 < leverage <= 1 else "FAIL",
                    f"OKX configured leverage is {leverage:g}; maximum allowed is 1.",
                )
            )
        except (OKXAPIError, OKXExecutionError) as exc:
            checks.append(OKXExecutionCheck("signed_diagnostics", "FAIL", str(exc)))

        return OKXExecutionReadiness(
            symbol=normalized,
            ready=all(check.status == "PASS" for check in checks),
            checks=tuple(checks),
        )

    def _local_readiness_checks(self) -> tuple[OKXExecutionCheck, ...]:
        return (
            OKXExecutionCheck(
                "demo_mode",
                "PASS" if self.config.okx_demo else "FAIL",
                "OKX simulated trading header is enabled."
                if self.config.okx_demo
                else "OKX demo mode is disabled; real execution remains blocked.",
            ),
            OKXExecutionCheck(
                "execution_switch",
                "PASS" if self.config.okx_demo_execution_enabled else "FAIL",
                "OKX demo execution switch is enabled."
                if self.config.okx_demo_execution_enabled
                else "Set TRADINGAGENTS_CRYPTO_OKX_DEMO_EXECUTION_ENABLED=true.",
            ),
            OKXExecutionCheck(
                "credentials",
                "PASS" if self.client.has_credentials else "FAIL",
                "OKX API credentials are present."
                if self.client.has_credentials
                else "OKX demo API key, secret, and passphrase are missing.",
            ),
            OKXExecutionCheck(
                "configured_leverage_cap",
                "PASS" if self.config.okx_max_leverage == 1 else "FAIL",
                f"Configured OKX leverage cap is {self.config.okx_max_leverage}.",
            ),
            OKXExecutionCheck(
                "emergency_stop",
                "PASS"
                if self.config.emergency_stop_file is not None
                and not self.config.emergency_stop_file.exists()
                else "FAIL",
                f"Emergency stop is configured and inactive: {self.config.emergency_stop_file}"
                if self.config.emergency_stop_file is not None
                and not self.config.emergency_stop_file.exists()
                else f"Emergency stop is active: {self.config.emergency_stop_file}"
                if self.config.emergency_stop_file is not None
                else "Configure TRADINGAGENTS_CRYPTO_EMERGENCY_STOP_FILE.",
            ),
            OKXExecutionCheck(
                "net_mode_policy",
                "PASS" if self.config.okx_require_net_mode else "FAIL",
                "The net position-mode requirement is enabled."
                if self.config.okx_require_net_mode
                else "OKX demo execution does not allow disabling the net-mode requirement.",
            ),
            OKXExecutionCheck(
                "protective_orders",
                "PASS"
                if self.config.okx_require_protective_orders
                and self.config.protective_oco_enabled
                else "FAIL",
                "Exchange-side attached TP/SL is enabled."
                if self.config.okx_require_protective_orders
                and self.config.protective_oco_enabled
                else "OKX demo BUY execution requires the protection policy and TP/SL switch.",
            ),
        )

    def _blocking_reason(
        self,
        intent: OrderIntent,
        mode: ExecutionMode,
        _live_confirmation: str,
    ) -> str:
        if mode == "live":
            return "OKX live execution is not implemented; only demo testnet mode is allowed."
        if mode != "testnet":
            return f"OKX execution is only for demo testnet mode, got {mode}."
        if not self.config.okx_demo:
            return "OKX demo execution requires TRADINGAGENTS_CRYPTO_OKX_DEMO=true."
        if not self.config.okx_demo_execution_enabled:
            return (
                "OKX demo execution is disabled: set "
                "TRADINGAGENTS_CRYPTO_OKX_DEMO_EXECUTION_ENABLED=true."
            )
        if self.config.okx_inst_type.strip().upper() != "SWAP":
            return "OKX demo execution currently supports SWAP instruments only."
        if self.config.okx_td_mode not in {"cross", "isolated"}:
            return "OKX trade mode must be cross or isolated."
        if self.config.okx_max_leverage != 1:
            return "OKX leverage cap must stay at exactly 1."
        if self.config.emergency_stop_file is None:
            return "OKX demo execution requires a configured emergency stop file path."
        if self.config.emergency_stop_file.exists():
            return f"Emergency stop file exists: {self.config.emergency_stop_file}"
        if not self.config.okx_require_net_mode:
            return "OKX demo execution does not allow disabling the net-mode requirement."
        if not self.config.okx_require_protective_orders:
            return "OKX demo execution does not allow disabling attached TP/SL protection."
        if not self.client.has_credentials:
            return "OKX demo API key, secret, and passphrase are required for execution."
        if intent.side == "SELL" and not intent.reduce_only:
            return "OKX short selling is disabled; SELL must be reduce-only."
        if intent.side == "BUY" and intent.reduce_only:
            return "OKX BUY entries cannot be reduce-only."
        if intent.side not in {"BUY", "SELL"}:
            return "OKX execution only accepts BUY entries or reduce-only SELL exits."
        if intent.side == "BUY":
            if not self.config.protective_oco_enabled:
                return "OKX demo BUY entries require exchange-side attached TP/SL."
            if not _valid_protection(intent):
                return "OKX demo BUY entry has invalid stop-loss or take-profit prices."
        return ""

    def _preflight(self, intent: OrderIntent) -> _Preflight:
        instrument = self.client.get_instrument(intent.symbol)
        if not _is_supported_instrument(instrument):
            raise OKXExecutionError(
                "OKX demo execution requires a live linear USDT perpetual contract."
            )

        account = self.client.get_account_config()
        permissions = _permissions(account)
        if "trade" not in permissions:
            raise OKXExecutionError("OKX demo API key does not have trade permission.")
        if "withdraw" in permissions:
            raise OKXExecutionError(
                "OKX demo execution requires a dedicated API key without withdrawal permission."
            )
        position_mode = str(account.get("posMode", ""))
        if position_mode != "net_mode":
            raise OKXExecutionError(
                f"OKX position mode is {position_mode or 'unknown'}; net_mode is required."
            )

        leverage = self._current_leverage(intent.symbol)
        if leverage <= 0 or leverage > 1:
            raise OKXExecutionError(
                f"OKX configured leverage is {leverage:g}; set account leverage to 1 first."
            )

        contracts = _base_quantity_to_contracts(intent.quantity, instrument)
        if intent.side == "SELL":
            position_contracts = self._long_position_contracts(intent.symbol)
            if position_contracts <= 0:
                raise OKXExecutionError("No positive OKX net long position exists to reduce.")
            contracts = min(contracts, position_contracts)
            contracts = _floor_to_step(contracts, Decimal(str(instrument.lot_size)))
        if contracts < Decimal(str(instrument.min_size)):
            raise OKXExecutionError(
                f"OKX order size {contracts} contracts is below minimum {instrument.min_size}."
            )
        return _Preflight(instrument=instrument, contracts=contracts)

    def _current_leverage(self, symbol: str) -> float:
        rows = self.client.get_leverage_info(symbol, self.config.okx_td_mode)
        values = [
            _safe_float(row.get("lever"))
            for row in rows
            if str(row.get("instId", "")).upper() == self.client.instrument_id(symbol)
        ]
        values = [value for value in values if value > 0]
        if not values:
            raise OKXExecutionError("No OKX leverage information returned for the instrument.")
        return max(values)

    def _long_position_contracts(self, symbol: str) -> Decimal:
        inst_id = self.client.instrument_id(symbol)
        total = Decimal("0")
        for row in self.client.get_positions(symbol):
            if str(row.get("instId", "")).upper() != inst_id:
                continue
            if str(row.get("posSide", "net")) not in {"", "net"}:
                continue
            position = Decimal(str(row.get("pos", "0") or "0"))
            if position > 0:
                total += position
        return total

    def _order_params(
        self,
        intent: OrderIntent,
        preflight: _Preflight,
        client_order_id: str,
    ) -> dict[str, Any]:
        if (
            not client_order_id
            or len(client_order_id) > 31
            or not client_order_id.isascii()
            or not client_order_id.isalnum()
        ):
            raise OKXExecutionError(
                "OKX client order ID must be 1-31 ASCII alphanumeric characters."
            )
        params: dict[str, Any] = {
            "instId": preflight.instrument.inst_id,
            "tdMode": self.config.okx_td_mode,
            "clOrdId": client_order_id,
            "tag": "tradingagents",
            "side": intent.side.lower(),
            "ordType": "market",
            "sz": _decimal_text(preflight.contracts),
            "reduceOnly": intent.reduce_only,
        }
        if intent.side == "BUY" and self.config.protective_oco_enabled:
            assert intent.stop_loss is not None
            assert intent.take_profit is not None
            params["attachAlgoOrds"] = [
                {
                    "attachAlgoClOrdId": f"{client_order_id}p"[:32],
                    "tpTriggerPx": _price_text(intent.take_profit, preflight.instrument.tick_size),
                    "tpOrdPx": "-1",
                    "tpTriggerPxType": "mark",
                    "slTriggerPx": _price_text(intent.stop_loss, preflight.instrument.tick_size),
                    "slOrdPx": "-1",
                    "slTriggerPxType": "mark",
                }
            ]
        return params

    def _wait_for_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        attempts = max(1, self.config.okx_order_poll_attempts)
        last: dict[str, Any] = {}
        for attempt in range(attempts):
            last = self.client.get_order(symbol, order_id=order_id)
            if str(last.get("state", "")) in {"filled", "canceled", "mmp_canceled"}:
                return last
            if attempt + 1 < attempts:
                self.sleep(max(0.0, self.config.okx_order_poll_interval_seconds))

        self.client.cancel_order(symbol, order_id=order_id)
        for attempt in range(attempts):
            last = self.client.get_order(symbol, order_id=order_id)
            if str(last.get("state", "")) in {"filled", "canceled", "mmp_canceled"}:
                return last
            if attempt + 1 < attempts:
                self.sleep(max(0.0, self.config.okx_order_poll_interval_seconds))
        return last

    def _stop_after_ambiguous_order(self, order_id: str, reason: str) -> str:
        if not order_id:
            return ""
        self._activate_emergency_stop(f"OKX order {order_id} could not be reconciled: {reason}")
        return " Emergency stop activated."

    def _activate_emergency_stop(self, reason: str) -> None:
        path = self.config.emergency_stop_file
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{reason}\n", encoding="utf-8")

    def _result_from_order(
        self,
        intent: OrderIntent,
        mode: ExecutionMode,
        preflight: _Preflight,
        ack: dict[str, Any],
        order: dict[str, Any],
    ) -> OrderResult:
        filled_contracts = Decimal(str(order.get("accFillSz", "0") or "0"))
        average_price = _safe_float(order.get("avgPx") or order.get("fillPx"))
        filled_base = filled_contracts * Decimal(str(preflight.instrument.contract_value))
        accepted = filled_contracts > 0 and average_price > 0
        state = str(order.get("state", "unknown"))
        payload: dict[str, Any] = {
            "orderId": str(ack.get("ordId", "")),
            "clientOrderId": str(ack.get("clOrdId", "")),
            "submitted_contracts": _decimal_text(preflight.contracts),
            "filled_contracts": _decimal_text(filled_contracts),
            "okx_ack": ack,
            "okx_order": order,
        }
        if accepted:
            payload["fills"] = [
                {
                    "qty": float(filled_base),
                    "price": average_price,
                }
            ]
        message = (
            f"OKX demo order filled: state={state}."
            if accepted and state == "filled"
            else f"OKX demo order partially filled or canceled: state={state}; reconcile position."
            if accepted
            else f"OKX demo order has no confirmed fill: state={state}; inspect exchange payload."
        )
        return OrderResult(
            mode=mode,
            accepted=accepted,
            symbol=intent.symbol,
            side=intent.side,
            quantity=float(filled_base) if accepted else intent.quantity,
            message=message,
            exchange_payload=payload,
        )


def _is_supported_instrument(instrument: OKXInstrument) -> bool:
    return (
        instrument.inst_type == "SWAP"
        and instrument.contract_type == "linear"
        and instrument.contract_value > 0
        and instrument.settle_ccy == "USDT"
        and instrument.state == "live"
    )


def _base_quantity_to_contracts(quantity: float, instrument: OKXInstrument) -> Decimal:
    contract_value = Decimal(str(instrument.contract_value))
    if contract_value <= 0:
        raise OKXExecutionError("OKX contract value must be positive.")
    raw = Decimal(str(quantity)) / contract_value
    return _floor_to_step(raw, Decimal(str(instrument.lot_size)))


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


def _price_text(value: float, tick_size: float) -> str:
    price = _floor_to_step(Decimal(str(value)), Decimal(str(tick_size)))
    return _decimal_text(price)


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _valid_protection(intent: OrderIntent) -> bool:
    return (
        intent.stop_loss is not None
        and intent.take_profit is not None
        and 0 < intent.stop_loss < intent.entry_price < intent.take_profit
    )


def _client_order_id() -> str:
    return f"ta{int(time.time() * 1000)}{secrets.token_hex(4)}"[:32]


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _permissions(account: dict[str, Any]) -> set[str]:
    return {
        item.strip().lower()
        for item in str(account.get("perm", "")).split(",")
        if item.strip()
    }


def _blocked(intent: OrderIntent, mode: ExecutionMode, reason: str) -> OrderResult:
    return OrderResult(
        mode=mode,
        accepted=False,
        symbol=intent.symbol,
        side=intent.side,
        quantity=intent.quantity,
        message=reason,
    )
