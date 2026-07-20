"""Safe OKX diagnostics for the trading center."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .config import CryptoTradingConfig
from .okx_client import OKXAPIError, OKXClient


DiagnosticStatus = Literal["PASS", "WARN", "FAIL", "SKIP"]


@dataclass(frozen=True)
class OKXDiagnosticStep:
    name: str
    status: DiagnosticStatus
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OKXDiagnosticReport:
    base_url: str
    demo: bool
    inst_type: str
    api_key_present: bool
    api_secret_present: bool
    api_passphrase_present: bool
    symbol: str
    steps: tuple[OKXDiagnosticStep, ...]

    @property
    def ok(self) -> bool:
        return all(step.status in {"PASS", "WARN", "SKIP"} for step in self.steps)


class OKXDiagnostics:
    def __init__(
        self,
        config: CryptoTradingConfig,
        client: OKXClient | None = None,
    ):
        self.config = config
        self.client = client or OKXClient(config)

    def run(self, symbol: str = "BTC", include_balance: bool = False) -> OKXDiagnosticReport:
        instrument = self.client.instrument_id(symbol)
        steps = [
            self._check_credentials(),
            self._check_time(),
            self._check_ticker(symbol),
            self._check_order_book(symbol),
            self._check_candles(symbol),
            self._check_symbol_rules(symbol),
        ]
        steps.append(
            self._check_balance()
            if include_balance
            else OKXDiagnosticStep(
                "account_balance",
                "SKIP",
                "Skipped by default; pass --include-balance for signed read-only balance.",
            )
        )
        return OKXDiagnosticReport(
            base_url=self.config.resolved_okx_base_url,
            demo=self.config.okx_demo,
            inst_type=self.config.okx_inst_type,
            api_key_present=bool(self.config.okx_api_key),
            api_secret_present=bool(self.config.okx_api_secret),
            api_passphrase_present=bool(self.config.okx_api_passphrase),
            symbol=instrument,
            steps=tuple(steps),
        )

    def _check_credentials(self) -> OKXDiagnosticStep:
        missing = []
        if not self.config.okx_api_key:
            missing.append("OKX_API_KEY")
        if not self.config.okx_api_secret:
            missing.append("OKX_API_SECRET")
        if not self.config.okx_api_passphrase:
            missing.append("OKX_API_PASSPHRASE")
        if missing:
            return OKXDiagnosticStep(
                "credentials",
                "WARN",
                "Signed OKX reads are unavailable until credentials are configured.",
                {"missing": ", ".join(missing)},
            )
        return OKXDiagnosticStep(
            "credentials",
            "PASS",
            "OKX API key, secret, and passphrase are present in environment variables.",
            {"api_key_suffix": self.config.okx_api_key[-4:]},
        )

    def _check_time(self) -> OKXDiagnosticStep:
        try:
            server_ms = self.client.get_server_time()
        except OKXAPIError as exc:
            return OKXDiagnosticStep("server_time", "FAIL", str(exc))
        return OKXDiagnosticStep(
            "server_time",
            "PASS",
            "OKX public REST connectivity is OK.",
            {"server_time_ms": server_ms},
        )

    def _check_ticker(self, symbol: str) -> OKXDiagnosticStep:
        try:
            ticker = self.client.get_24h_ticker(symbol)
        except OKXAPIError as exc:
            return OKXDiagnosticStep("ticker", "FAIL", str(exc))
        return OKXDiagnosticStep(
            "ticker",
            "PASS",
            "Ticker snapshot loaded.",
            {
                "symbol": ticker.symbol,
                "last": ticker.last_price,
                "change_pct_24h": ticker.price_change_pct_24h,
                "quote_volume_24h": ticker.quote_volume_24h,
            },
        )

    def _check_order_book(self, symbol: str) -> OKXDiagnosticStep:
        try:
            book = self.client.get_order_book(symbol, depth=20)
        except OKXAPIError as exc:
            return OKXDiagnosticStep("order_book", "FAIL", str(exc))
        if not book.bids or not book.asks:
            return OKXDiagnosticStep("order_book", "FAIL", "No OKX bid/ask levels returned.")
        return OKXDiagnosticStep(
            "order_book",
            "PASS",
            "Order book loaded.",
            {
                "best_bid": book.best_bid,
                "best_ask": book.best_ask,
                "spread_bps": book.spread_bps,
                "bid_levels": len(book.bids),
                "ask_levels": len(book.asks),
            },
        )

    def _check_candles(self, symbol: str) -> OKXDiagnosticStep:
        try:
            candles = self.client.get_klines(symbol, self.config.interval, 5)
        except OKXAPIError as exc:
            return OKXDiagnosticStep("candles", "FAIL", str(exc))
        if not candles:
            return OKXDiagnosticStep("candles", "FAIL", "No OKX candles returned.")
        return OKXDiagnosticStep(
            "candles",
            "PASS",
            "Candles loaded.",
            {"count": len(candles), "last_close": candles[-1].close},
        )

    def _check_symbol_rules(self, symbol: str) -> OKXDiagnosticStep:
        try:
            rules = self.client.get_symbol_rules(symbol)
        except OKXAPIError as exc:
            return OKXDiagnosticStep("symbol_rules", "FAIL", str(exc))
        return OKXDiagnosticStep(
            "symbol_rules",
            "PASS",
            "Instrument trading rules loaded.",
            {
                "symbol": rules.symbol,
                "base_asset": rules.base_asset,
                "quote_asset": rules.quote_asset,
                "min_qty": rules.min_qty,
                "step_size": rules.step_size,
                "min_notional": rules.min_notional,
            },
        )

    def _check_balance(self) -> OKXDiagnosticStep:
        try:
            balances = self.client.get_account_balances()
        except OKXAPIError as exc:
            return OKXDiagnosticStep("account_balance", "FAIL", str(exc))
        return OKXDiagnosticStep(
            "account_balance",
            "PASS",
            "Signed read-only balance endpoint works.",
            {"nonzero_assets": [balance.asset for balance in balances[:20]]},
        )
