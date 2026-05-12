"""Safe Hyperliquid diagnostics for the trading center."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from typing import Any, Literal

from .config import CryptoTradingConfig
from .hyperliquid_client import HyperliquidAPIError, HyperliquidClient
from .hyperliquid_execution import HyperliquidExecutionAdapter


DiagnosticStatus = Literal["PASS", "WARN", "FAIL", "SKIP"]


@dataclass(frozen=True)
class HyperliquidDiagnosticStep:
    name: str
    status: DiagnosticStatus
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HyperliquidDiagnosticReport:
    base_url: str
    testnet: bool
    wallet_address_present: bool
    api_wallet_present: bool
    private_key_present: bool
    symbol: str
    steps: tuple[HyperliquidDiagnosticStep, ...]

    @property
    def ok(self) -> bool:
        return all(step.status in {"PASS", "WARN", "SKIP"} for step in self.steps)


class HyperliquidDiagnostics:
    def __init__(
        self,
        config: CryptoTradingConfig,
        client: HyperliquidClient | None = None,
    ):
        self.config = config
        self.client = client or HyperliquidClient(config)

    def run(self, symbol: str = "BTC") -> HyperliquidDiagnosticReport:
        coin = HyperliquidClient.normalize_symbol(symbol)
        steps = [
            self._check_sdk(),
            self._check_execution_config(),
            self._check_meta(),
            self._check_all_mids(coin),
            self._check_l2_book(coin),
            self._check_asset_context(coin),
            self._check_candles(coin),
            self._check_account(),
        ]
        return HyperliquidDiagnosticReport(
            base_url=self.config.resolved_hyperliquid_base_url,
            testnet=self.config.hyperliquid_testnet,
            wallet_address_present=bool(self.config.hyperliquid_wallet_address),
            api_wallet_present=bool(self.config.hyperliquid_api_wallet_address),
            private_key_present=bool(self.config.hyperliquid_private_key),
            symbol=coin,
            steps=tuple(steps),
        )

    def _check_sdk(self) -> HyperliquidDiagnosticStep:
        if importlib.util.find_spec("hyperliquid") is None:
            return HyperliquidDiagnosticStep(
                "python_sdk",
                "WARN",
                "hyperliquid-python-sdk is not installed; public checks work, live signing is disabled.",
            )
        return HyperliquidDiagnosticStep(
            "python_sdk",
            "PASS",
            "hyperliquid-python-sdk is installed.",
        )

    def _check_execution_config(self) -> HyperliquidDiagnosticStep:
        status = HyperliquidExecutionAdapter(self.config).signer_status()
        details = {
            "sdk_enabled": status.sdk_enabled,
            "private_key": status.private_key_present,
            "wallet_address": status.wallet_address_present,
            "api_wallet_address": status.api_wallet_address_present,
            "signer_address": status.signer_address,
        }
        if not status.sdk_enabled:
            return HyperliquidDiagnosticStep(
                "sdk_execution_config",
                "SKIP",
                "SDK execution is disabled by default.",
                details,
            )
        if not status.sdk_available:
            return HyperliquidDiagnosticStep(
                "sdk_execution_config",
                "FAIL",
                status.reason or "hyperliquid-python-sdk is not installed.",
                details,
            )
        if not status.private_key_present or not status.wallet_address_present:
            return HyperliquidDiagnosticStep(
                "sdk_execution_config",
                "FAIL",
                "SDK execution requires wallet address and private key env vars.",
                details,
            )
        if status.reason:
            return HyperliquidDiagnosticStep(
                "sdk_execution_config",
                "FAIL",
                status.reason,
                details,
            )
        expected = self.config.hyperliquid_api_wallet_address.lower()
        if expected and status.signer_address.lower() != expected:
            return HyperliquidDiagnosticStep(
                "sdk_execution_config",
                "FAIL",
                "API wallet address does not match the private key signer.",
                details,
            )
        return HyperliquidDiagnosticStep(
            "sdk_execution_config",
            "PASS",
            "SDK execution config is internally consistent.",
            details,
        )

    def _check_meta(self) -> HyperliquidDiagnosticStep:
        try:
            markets = self.client.get_markets()
        except HyperliquidAPIError as exc:
            return HyperliquidDiagnosticStep("meta", "FAIL", str(exc))
        return HyperliquidDiagnosticStep(
            "meta",
            "PASS",
            "Market metadata loaded.",
            {"markets": len(markets), "sample": [market.name for market in markets[:8]]},
        )

    def _check_all_mids(self, coin: str) -> HyperliquidDiagnosticStep:
        try:
            mids = self.client.get_all_mids()
        except HyperliquidAPIError as exc:
            return HyperliquidDiagnosticStep("all_mids", "FAIL", str(exc))
        if coin not in mids:
            return HyperliquidDiagnosticStep(
                "all_mids",
                "FAIL",
                f"{coin} is not present in allMids.",
                {"available_sample": list(mids.keys())[:12]},
            )
        return HyperliquidDiagnosticStep(
            "all_mids",
            "PASS",
            "Mid prices loaded.",
            {"symbol": coin, "mid": mids[coin]},
        )

    def _check_l2_book(self, coin: str) -> HyperliquidDiagnosticStep:
        try:
            book = self.client.get_l2_book(coin)
        except HyperliquidAPIError as exc:
            return HyperliquidDiagnosticStep("l2_book", "FAIL", str(exc))
        if not book.bids or not book.asks:
            return HyperliquidDiagnosticStep("l2_book", "FAIL", "No bid/ask levels returned.")
        return HyperliquidDiagnosticStep(
            "l2_book",
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

    def _check_asset_context(self, coin: str) -> HyperliquidDiagnosticStep:
        try:
            context = self.client.get_asset_context(coin)
        except HyperliquidAPIError as exc:
            return HyperliquidDiagnosticStep("asset_context", "FAIL", str(exc))
        if not context:
            return HyperliquidDiagnosticStep(
                "asset_context",
                "WARN",
                f"No asset context returned for {coin}.",
            )
        return HyperliquidDiagnosticStep(
            "asset_context",
            "PASS",
            "Asset context loaded.",
            {
                "funding": context.get("funding"),
                "open_interest": context.get("openInterest"),
                "mark_price": context.get("markPx"),
            },
        )

    def _check_candles(self, coin: str) -> HyperliquidDiagnosticStep:
        try:
            candles = self.client.get_klines(coin, self.config.interval, 5)
        except HyperliquidAPIError as exc:
            return HyperliquidDiagnosticStep("candles", "FAIL", str(exc))
        if not candles:
            return HyperliquidDiagnosticStep("candles", "FAIL", "No candles returned.")
        return HyperliquidDiagnosticStep(
            "candles",
            "PASS",
            "Candle snapshot loaded.",
            {"count": len(candles), "last_close": candles[-1].close},
        )

    def _check_account(self) -> HyperliquidDiagnosticStep:
        if not self.config.hyperliquid_wallet_address:
            return HyperliquidDiagnosticStep(
                "account",
                "SKIP",
                "TRADINGAGENTS_CRYPTO_HYPERLIQUID_WALLET_ADDRESS is empty.",
            )
        try:
            state = self.client.get_user_state()
        except HyperliquidAPIError as exc:
            return HyperliquidDiagnosticStep("account", "FAIL", str(exc))
        margin = state.get("marginSummary", {})
        return HyperliquidDiagnosticStep(
            "account",
            "PASS",
            "Clearinghouse state loaded.",
            {
                "account_value": margin.get("accountValue", "0"),
                "margin_used": margin.get("totalMarginUsed", "0"),
                "withdrawable": state.get("withdrawable", "0"),
                "asset_positions": len(state.get("assetPositions", [])),
            },
        )
