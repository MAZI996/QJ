"""Read-only readiness checks before Hyperliquid execution phases."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .circuit_breaker import DailyLossCircuitBreaker
from .config import CryptoTradingConfig
from .hyperliquid_diagnostics import HyperliquidDiagnostics
from .hyperliquid_execution import HyperliquidExecutionAdapter
from .positions import PositionStore


ReadinessTarget = Literal["paper", "testnet", "live"]
ReadinessStatus = Literal["PASS", "WARN", "FAIL"]


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: ReadinessStatus
    message: str


@dataclass(frozen=True)
class LiveReadinessReport:
    target: ReadinessTarget
    checks: tuple[ReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        return all(check.status != "FAIL" for check in self.checks)

    @property
    def failures(self) -> tuple[ReadinessCheck, ...]:
        return tuple(check for check in self.checks if check.status == "FAIL")

    @property
    def warnings(self) -> tuple[ReadinessCheck, ...]:
        return tuple(check for check in self.checks if check.status == "WARN")


class LiveReadinessChecker:
    """Evaluate local and optional network prerequisites without submitting orders."""

    def __init__(self, config: CryptoTradingConfig):
        self.config = config

    def run(
        self,
        target: ReadinessTarget = "live",
        *,
        network: bool = False,
        symbol: str = "BTC",
    ) -> LiveReadinessReport:
        checks = [
            self._provider_check(),
            *self._mode_switch_checks(target),
            *self._signer_checks(target),
            *self._risk_checks(target),
            self._circuit_breaker_check(),
            self._position_state_check(target),
            self._paper_evidence_check(target),
        ]
        if network:
            checks.extend(self._network_checks(symbol))
        return LiveReadinessReport(target=target, checks=tuple(checks))

    def _provider_check(self) -> ReadinessCheck:
        provider = self.config.exchange_provider.strip().lower()
        if provider != "hyperliquid":
            return ReadinessCheck(
                "exchange_provider",
                "FAIL",
                f"Expected hyperliquid for this trading center, got {provider or '-'}",
            )
        return ReadinessCheck("exchange_provider", "PASS", "Provider is hyperliquid.")

    def _mode_switch_checks(self, target: ReadinessTarget) -> tuple[ReadinessCheck, ...]:
        checks: list[ReadinessCheck] = []
        if target == "paper":
            checks.append(
                ReadinessCheck(
                    "live_order_switch",
                    "WARN" if self.config.enable_live_orders else "PASS",
                    (
                        "Live order switch is enabled; turn it off for paper validation."
                        if self.config.enable_live_orders
                        else "Live order switch is off for paper validation."
                    ),
                )
            )
            return tuple(checks)

        expected_testnet = target == "testnet"
        checks.append(
            ReadinessCheck(
                "hyperliquid_testnet",
                "PASS" if self.config.hyperliquid_testnet == expected_testnet else "FAIL",
                (
                    f"Hyperliquid testnet flag is {self.config.hyperliquid_testnet}; "
                    f"{target} target expects {expected_testnet}."
                ),
            )
        )
        if target == "live":
            checks.append(
                ReadinessCheck(
                    "live_order_switch",
                    "PASS" if self.config.enable_live_orders else "FAIL",
                    (
                        "TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS=true is set."
                        if self.config.enable_live_orders
                        else "Live switch is disabled; real orders remain blocked."
                    ),
                )
            )
        return tuple(checks)

    def _signer_checks(self, target: ReadinessTarget) -> tuple[ReadinessCheck, ...]:
        status = HyperliquidExecutionAdapter(self.config).signer_status()
        requires_signer = target in {"testnet", "live"}
        checks = [
            ReadinessCheck(
                "python_sdk",
                "PASS" if status.sdk_available else ("FAIL" if requires_signer else "WARN"),
                (
                    "hyperliquid-python-sdk is installed."
                    if status.sdk_available
                    else "hyperliquid-python-sdk is not installed."
                ),
            ),
            ReadinessCheck(
                "sdk_execution_enabled",
                "PASS" if self.config.hyperliquid_sdk_execution_enabled else (
                    "FAIL" if requires_signer else "PASS"
                ),
                (
                    "SDK execution flag is enabled."
                    if self.config.hyperliquid_sdk_execution_enabled
                    else "SDK execution flag is disabled."
                ),
            ),
            ReadinessCheck(
                "wallet_address",
                "PASS" if status.wallet_address_present else ("FAIL" if requires_signer else "WARN"),
                (
                    "Main wallet address is configured."
                    if status.wallet_address_present
                    else "Main wallet address is missing."
                ),
            ),
            ReadinessCheck(
                "private_key",
                "PASS" if status.private_key_present else ("FAIL" if requires_signer else "PASS"),
                (
                    "Private key is present in environment; value is not displayed."
                    if status.private_key_present
                    else "Private key is not configured."
                ),
            ),
        ]
        if target == "live":
            checks.append(
                ReadinessCheck(
                    "api_wallet_address",
                    "PASS" if status.api_wallet_address_present else "FAIL",
                    (
                        "API wallet address is configured."
                        if status.api_wallet_address_present
                        else "API wallet address is missing; do not use the main wallet key for live automation."
                    ),
                )
            )
        elif status.api_wallet_address_present:
            checks.append(ReadinessCheck("api_wallet_address", "PASS", "API wallet address is configured."))

        if status.sdk_available and status.reason:
            checks.append(ReadinessCheck("signer_consistency", "FAIL", status.reason))
        elif (
            status.api_wallet_address_present
            and status.signer_address
            and status.signer_address.lower()
            != self.config.hyperliquid_api_wallet_address.lower()
        ):
            checks.append(
                ReadinessCheck(
                    "signer_consistency",
                    "FAIL",
                    "API wallet address does not match the configured private key.",
                )
            )
        elif requires_signer and status.sdk_available and status.private_key_present:
            checks.append(
                ReadinessCheck(
                    "signer_consistency",
                    "PASS",
                    "Signer key can be loaded locally.",
                )
            )
        return tuple(checks)

    def _risk_checks(self, target: ReadinessTarget) -> tuple[ReadinessCheck, ...]:
        checks = [
            ReadinessCheck(
                "max_leverage",
                "PASS" if self.config.hyperliquid_max_leverage <= 1 else "FAIL",
                f"Configured Hyperliquid max leverage is {self.config.hyperliquid_max_leverage}.",
            ),
            ReadinessCheck(
                "risk_per_trade",
                "PASS" if 0 < self.config.risk_per_trade_pct <= 0.01 else "WARN",
                f"Risk per trade is {self.config.risk_per_trade_pct:.2%}.",
            ),
            ReadinessCheck(
                "max_position",
                "PASS" if 0 < self.config.max_position_pct <= 0.10 else "WARN",
                f"Max position size is {self.config.max_position_pct:.2%} of configured equity.",
            ),
            ReadinessCheck(
                "daily_loss_limit",
                "PASS"
                if self.config.daily_loss_limit_usdt > 0 or self.config.daily_loss_limit_pct > 0
                else "FAIL",
                (
                    f"Daily loss limit is {self.config.daily_loss_limit_usdt:.2f} USDT."
                    if self.config.daily_loss_limit_usdt > 0
                    else f"Daily loss limit is {self.config.daily_loss_limit_pct:.2%} of equity."
                ),
            ),
        ]
        if target == "live":
            checks.extend(
                [
                    ReadinessCheck(
                        "protective_orders_required",
                        "PASS" if self.config.hyperliquid_require_protective_orders else "FAIL",
                        (
                            "Live entries require protective stop/take-profit orders."
                            if self.config.hyperliquid_require_protective_orders
                            else "Protective-order requirement is disabled."
                        ),
                    ),
                    ReadinessCheck(
                        "protective_orders_enabled",
                        "PASS" if self.config.protective_oco_enabled else "FAIL",
                        (
                            "Protective order submission is enabled."
                            if self.config.protective_oco_enabled
                            else "Protective order submission is disabled."
                        ),
                    ),
                    ReadinessCheck(
                        "emergency_stop_file",
                        "PASS" if self.config.emergency_stop_file else "FAIL",
                        (
                            f"Emergency stop file is configured: {self.config.emergency_stop_file}"
                            if self.config.emergency_stop_file
                            else "Emergency stop file is not configured."
                        ),
                    ),
                    ReadinessCheck(
                        "position_guardian_enabled",
                        "PASS" if self.config.position_guardian_enabled else "FAIL",
                        (
                            "Position guardian is enabled for automatic reduce-only exits."
                            if self.config.position_guardian_enabled
                            else "Position guardian is disabled; live automation cannot auto-close positions."
                        ),
                    ),
                    ReadinessCheck(
                        "position_guardian_skip_entries",
                        "PASS"
                        if self.config.position_guardian_skip_entries_after_close
                        else "WARN",
                        (
                            "Autopilot will skip new entries on cycles where a close signal appears."
                            if self.config.position_guardian_skip_entries_after_close
                            else "Autopilot may enter after a close signal in the same cycle."
                        ),
                    ),
                ]
            )
        return tuple(checks)

    def _circuit_breaker_check(self) -> ReadinessCheck:
        breaker = DailyLossCircuitBreaker(self.config).evaluate()
        return ReadinessCheck(
            "daily_loss_circuit_breaker",
            "FAIL" if breaker.blocked else "PASS",
            breaker.reason,
        )

    def _position_state_check(self, target: ReadinessTarget) -> ReadinessCheck:
        positions = PositionStore.from_state_dir(self.config.state_dir).active_positions()
        if not positions:
            return ReadinessCheck("local_positions", "PASS", "No locally tracked open positions.")
        status = "WARN" if target != "live" else "FAIL"
        return ReadinessCheck(
            "local_positions",
            status,
            f"{len(positions)} local open position(s) exist; recover/sync before live automation.",
        )

    def _paper_evidence_check(self, target: ReadinessTarget) -> ReadinessCheck:
        path = Path(self.config.state_dir) / "paper_orders.jsonl"
        if path.exists() and path.stat().st_size > 0:
            return ReadinessCheck("paper_evidence", "PASS", f"Paper order journal exists: {path}")
        status = "FAIL" if target == "live" else "WARN"
        return ReadinessCheck(
            "paper_evidence",
            status,
            "No paper order journal found; run paper autopilot before live.",
        )

    def _network_checks(self, symbol: str) -> list[ReadinessCheck]:
        report = HyperliquidDiagnostics(self.config).run(symbol=symbol)
        checks: list[ReadinessCheck] = []
        for step in report.steps:
            if step.status == "PASS":
                status: ReadinessStatus = "PASS"
            elif step.status == "FAIL":
                status = "FAIL"
            else:
                status = "WARN"
            checks.append(
                ReadinessCheck(
                    name=f"network_{step.name}",
                    status=status,
                    message=step.message,
                )
            )
        return checks
