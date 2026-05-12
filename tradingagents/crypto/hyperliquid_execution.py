"""Official SDK execution adapter for Hyperliquid."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import CryptoTradingConfig
from .hyperliquid_client import HyperliquidClient
from .models import ExecutionMode, OrderIntent, OrderResult


class HyperliquidExecutionError(RuntimeError):
    """Raised when the optional Hyperliquid SDK execution path cannot run."""


@dataclass(frozen=True)
class HyperliquidSignerStatus:
    sdk_available: bool
    sdk_enabled: bool
    private_key_present: bool
    wallet_address_present: bool
    api_wallet_address_present: bool
    signer_address: str = ""
    reason: str = ""


class HyperliquidExecutionAdapter:
    """Submit Hyperliquid orders only after deterministic local guards pass."""

    def __init__(self, config: CryptoTradingConfig):
        self.config = config

    def signer_status(self) -> HyperliquidSignerStatus:
        sdk_available = _sdk_available()
        signer_address = ""
        reason = ""
        if sdk_available and self.config.hyperliquid_private_key:
            try:
                signer_address = self._account().address
            except Exception as exc:
                reason = str(exc)
        elif not sdk_available:
            reason = "hyperliquid-python-sdk is not installed."
        return HyperliquidSignerStatus(
            sdk_available=sdk_available,
            sdk_enabled=self.config.hyperliquid_sdk_execution_enabled,
            private_key_present=bool(self.config.hyperliquid_private_key),
            wallet_address_present=bool(self.config.hyperliquid_wallet_address),
            api_wallet_address_present=bool(self.config.hyperliquid_api_wallet_address),
            signer_address=signer_address,
            reason=reason,
        )

    def execute(
        self,
        intent: OrderIntent,
        mode: ExecutionMode,
        live_confirmation: str = "",
    ) -> OrderResult:
        reason = self._blocking_reason(intent, mode, live_confirmation)
        if reason:
            return _blocked(intent, mode, reason)

        try:
            payload = self._submit(intent)
        except HyperliquidExecutionError as exc:
            return _blocked(intent, mode, str(exc))
        except Exception as exc:
            return _blocked(intent, mode, f"Hyperliquid SDK order failed: {exc}")

        accepted = isinstance(payload, dict) and payload.get("status") == "ok"
        return OrderResult(
            mode=mode,
            accepted=accepted,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            message=(
                "Hyperliquid SDK order accepted."
                if accepted
                else "Hyperliquid SDK rejected order; inspect exchange_payload."
            ),
            exchange_payload=payload if isinstance(payload, dict) else {"raw": payload},
        )

    def _blocking_reason(
        self,
        intent: OrderIntent,
        mode: ExecutionMode,
        live_confirmation: str,
    ) -> str:
        if mode not in {"testnet", "live"}:
            return f"Hyperliquid SDK execution is only for testnet/live, got {mode}."
        if not self.config.hyperliquid_sdk_execution_enabled:
            return (
                "Hyperliquid SDK execution is disabled: set "
                "TRADINGAGENTS_CRYPTO_HYPERLIQUID_SDK_EXECUTION_ENABLED=true."
            )
        if intent.side != "BUY":
            return "Hyperliquid execution is currently long-only."
        if self.config.hyperliquid_max_leverage > 1:
            return "Hyperliquid leverage must stay at 1 during this validation phase."
        if not self.config.hyperliquid_wallet_address:
            return "TRADINGAGENTS_CRYPTO_HYPERLIQUID_WALLET_ADDRESS is required for SDK execution."
        if not self.config.hyperliquid_private_key:
            return "TRADINGAGENTS_CRYPTO_HYPERLIQUID_PRIVATE_KEY is required for SDK execution."
        if mode == "testnet" and not self.config.hyperliquid_testnet:
            return "Testnet mode requires TRADINGAGENTS_CRYPTO_HYPERLIQUID_TESTNET=true."
        if mode == "live":
            if not self.config.enable_live_orders:
                return "Live switch is disabled: set TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS=true."
            if live_confirmation != self.config.live_confirm_phrase:
                return "Missing live confirmation phrase; refusing real order."
            if self.config.hyperliquid_testnet:
                return "Hyperliquid config is still testnet; refusing live order."
            if (
                self.config.hyperliquid_require_protective_orders
                and not self._can_place_protection(intent)
            ):
                return (
                    "Live Hyperliquid entries require protective stop/take-profit orders. "
                    "Set TRADINGAGENTS_CRYPTO_PROTECTIVE_OCO_ENABLED=true and keep "
                    "valid stop_loss/take_profit on the signal."
                )
        if not _sdk_available():
            return "Install the official SDK first: pip install hyperliquid-python-sdk."
        if self.config.hyperliquid_api_wallet_address:
            signer = self._account().address.lower()
            expected = self.config.hyperliquid_api_wallet_address.lower()
            if signer != expected:
                return (
                    "Configured API wallet address does not match "
                    "TRADINGAGENTS_CRYPTO_HYPERLIQUID_PRIVATE_KEY."
                )
        return ""

    def _submit(self, intent: OrderIntent) -> Any:
        exchange = self._exchange()
        coin = HyperliquidClient.normalize_symbol(intent.symbol)
        if self._can_place_protection(intent):
            return exchange.bulk_orders(
                self._bracket_orders(coin, intent),
                grouping="normalTpsl",
            )
        return exchange.market_open(
            coin,
            True,
            intent.quantity,
            px=intent.entry_price,
            slippage=self.config.hyperliquid_market_slippage,
        )

    def _exchange(self) -> Any:
        _, exchange_cls = _load_sdk()
        return exchange_cls(
            self._account(),
            base_url=self.config.resolved_hyperliquid_base_url,
            account_address=self.config.hyperliquid_wallet_address,
        )

    def _account(self) -> Any:
        account_mod, _ = _load_sdk()
        try:
            return account_mod.Account.from_key(self.config.hyperliquid_private_key)
        except Exception as exc:
            raise HyperliquidExecutionError("Invalid Hyperliquid private key.") from exc

    def _can_place_protection(self, intent: OrderIntent) -> bool:
        return (
            self.config.protective_oco_enabled
            and intent.stop_loss is not None
            and intent.take_profit is not None
            and intent.stop_loss < intent.entry_price < intent.take_profit
        )

    def _bracket_orders(self, coin: str, intent: OrderIntent) -> list[dict[str, Any]]:
        assert intent.stop_loss is not None
        assert intent.take_profit is not None
        slippage = max(0.0, self.config.hyperliquid_market_slippage)
        return [
            {
                "coin": coin,
                "is_buy": True,
                "sz": intent.quantity,
                "limit_px": _slippage_price(intent.entry_price, is_buy=True, slippage=slippage),
                "order_type": {"limit": {"tif": "Ioc"}},
                "reduce_only": False,
            },
            {
                "coin": coin,
                "is_buy": False,
                "sz": intent.quantity,
                "limit_px": _slippage_price(intent.take_profit, is_buy=False, slippage=slippage),
                "order_type": {
                    "trigger": {
                        "isMarket": True,
                        "triggerPx": intent.take_profit,
                        "tpsl": "tp",
                    }
                },
                "reduce_only": True,
            },
            {
                "coin": coin,
                "is_buy": False,
                "sz": intent.quantity,
                "limit_px": _slippage_price(intent.stop_loss, is_buy=False, slippage=slippage),
                "order_type": {
                    "trigger": {
                        "isMarket": True,
                        "triggerPx": intent.stop_loss,
                        "tpsl": "sl",
                    }
                },
                "reduce_only": True,
            },
        ]


def _load_sdk() -> tuple[Any, Any]:
    try:
        import eth_account
        from hyperliquid.exchange import Exchange
    except ImportError as exc:
        raise HyperliquidExecutionError(
            "Install the official SDK first: pip install hyperliquid-python-sdk."
        ) from exc
    return eth_account, Exchange


def _sdk_available() -> bool:
    try:
        _load_sdk()
    except HyperliquidExecutionError:
        return False
    return True


def _slippage_price(price: float, is_buy: bool, slippage: float) -> float:
    multiplier = (1 + slippage) if is_buy else (1 - slippage)
    return max(0.0, price * multiplier)


def _blocked(intent: OrderIntent, mode: ExecutionMode, reason: str) -> OrderResult:
    return OrderResult(
        mode=mode,
        accepted=False,
        symbol=intent.symbol,
        side=intent.side,
        quantity=intent.quantity,
        message=reason,
    )
