"""Safe Binance account diagnostics for first-time integration."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .binance_client import BinanceAPIError, BinanceClient
from .config import CryptoTradingConfig


DiagnosticStatus = Literal["PASS", "WARN", "FAIL", "SKIP"]


@dataclass(frozen=True)
class BinanceDiagnosticStep:
    name: str
    status: DiagnosticStatus
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BinanceDiagnosticReport:
    base_url: str
    testnet: bool
    api_key_present: bool
    api_secret_present: bool
    symbol: str
    steps: tuple[BinanceDiagnosticStep, ...]

    @property
    def ok(self) -> bool:
        return all(step.status in {"PASS", "WARN", "SKIP"} for step in self.steps)


class BinanceDiagnostics:
    def __init__(
        self,
        config: CryptoTradingConfig,
        client: BinanceClient | None = None,
    ):
        self.config = config
        self.client = client or BinanceClient(config)

    def run(
        self,
        symbol: str = "BTCUSDT",
        quote_order_usdt: float = 11.0,
        include_order_test: bool = True,
    ) -> BinanceDiagnosticReport:
        symbol = symbol.upper()
        steps = [
            self._check_credentials(),
            self._check_ping(),
            self._check_time(),
            self._check_symbol_rules(symbol),
            self._check_account(),
        ]
        steps.append(
            self._check_order_test(symbol, quote_order_usdt)
            if include_order_test
            else BinanceDiagnosticStep(
                "order_test",
                "SKIP",
                "Skipped by --no-test-order.",
            )
        )
        return BinanceDiagnosticReport(
            base_url=self.config.resolved_base_url,
            testnet=self.config.testnet,
            api_key_present=bool(self.config.api_key),
            api_secret_present=bool(self.config.api_secret),
            symbol=symbol,
            steps=tuple(steps),
        )

    def _check_credentials(self) -> BinanceDiagnosticStep:
        missing = []
        if not self.config.api_key:
            missing.append("BINANCE_API_KEY")
        if not self.config.api_secret:
            missing.append("BINANCE_API_SECRET")
        if missing:
            return BinanceDiagnosticStep(
                "credentials",
                "FAIL",
                f"Missing environment variables: {', '.join(missing)}.",
            )
        return BinanceDiagnosticStep(
            "credentials",
            "PASS",
            "API key and secret are present in environment variables.",
            {"api_key_suffix": self.config.api_key[-4:]},
        )

    def _check_ping(self) -> BinanceDiagnosticStep:
        try:
            self.client.ping()
        except BinanceAPIError as exc:
            return BinanceDiagnosticStep("ping", "FAIL", str(exc))
        return BinanceDiagnosticStep("ping", "PASS", "Public REST connectivity is OK.")

    def _check_time(self) -> BinanceDiagnosticStep:
        local_ms = int(time.time() * 1000)
        try:
            server_ms = self.client.get_server_time()
        except BinanceAPIError as exc:
            return BinanceDiagnosticStep("server_time", "FAIL", str(exc))
        offset_ms = server_ms - local_ms
        status: DiagnosticStatus = "PASS" if abs(offset_ms) <= 1000 else "WARN"
        message = (
            "Server time offset is acceptable."
            if status == "PASS"
            else "Server time offset is high; signed requests may fail with timestamp errors."
        )
        return BinanceDiagnosticStep(
            "server_time",
            status,
            message,
            {"offset_ms": offset_ms, "recv_window_ms": self.config.recv_window_ms},
        )

    def _check_symbol_rules(self, symbol: str) -> BinanceDiagnosticStep:
        try:
            rules = self.client.get_symbol_rules(symbol)
        except BinanceAPIError as exc:
            return BinanceDiagnosticStep("symbol_rules", "FAIL", str(exc))
        return BinanceDiagnosticStep(
            "symbol_rules",
            "PASS",
            "Symbol trading rules loaded.",
            {
                "symbol": rules.symbol,
                "base_asset": rules.base_asset,
                "quote_asset": rules.quote_asset,
                "min_qty": rules.min_qty,
                "step_size": rules.step_size,
                "min_notional": rules.min_notional,
            },
        )

    def _check_account(self) -> BinanceDiagnosticStep:
        try:
            account = self.client.get_account_info()
        except BinanceAPIError as exc:
            return BinanceDiagnosticStep("account", "FAIL", str(exc))
        can_trade = bool(account.get("canTrade", False))
        balances = account.get("balances", [])
        nonzero = [
            row.get("asset")
            for row in balances
            if float(row.get("free", "0") or 0) > 0
            or float(row.get("locked", "0") or 0) > 0
        ]
        status: DiagnosticStatus = "PASS" if can_trade else "WARN"
        message = (
            "Signed account endpoint works and spot trading is enabled."
            if can_trade
            else "Signed account endpoint works, but canTrade is false."
        )
        return BinanceDiagnosticStep(
            "account",
            status,
            message,
            {
                "account_type": account.get("accountType", ""),
                "can_trade": can_trade,
                "can_deposit": bool(account.get("canDeposit", False)),
                "can_withdraw": bool(account.get("canWithdraw", False)),
                "permissions": account.get("permissions", []),
                "nonzero_assets": nonzero[:20],
            },
        )

    def _check_order_test(
        self,
        symbol: str,
        quote_order_usdt: float,
    ) -> BinanceDiagnosticStep:
        if quote_order_usdt <= 0:
            return BinanceDiagnosticStep(
                "order_test",
                "SKIP",
                "quote_order_usdt must be positive.",
            )
        try:
            self.client.test_market_order_quote(
                symbol=symbol,
                side="BUY",
                quote_order_qty=quote_order_usdt,
            )
        except BinanceAPIError as exc:
            return BinanceDiagnosticStep(
                "order_test",
                "FAIL",
                str(exc),
                {"symbol": symbol, "quote_order_usdt": quote_order_usdt},
            )
        return BinanceDiagnosticStep(
            "order_test",
            "PASS",
            "Binance order/test accepted. No real order was created.",
            {"symbol": symbol, "quote_order_usdt": quote_order_usdt},
        )
