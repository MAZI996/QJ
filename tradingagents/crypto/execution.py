"""Execution adapters with live-trading guardrails."""

from __future__ import annotations

from .config import CryptoTradingConfig
from .hyperliquid_execution import HyperliquidExecutionAdapter
from .models import ExecutionMode, OrderIntent, OrderResult
from .okx_client import OKXClient
from .okx_execution import OKXExecutionAdapter
from .paper import PaperBroker
from .positions import PositionStore
from .protective_orders import plan_from_intent


class ExecutionRouter:
    def __init__(self, client, config: CryptoTradingConfig):
        self.client = client
        self.config = config
        self.paper = PaperBroker(config)
        self.positions = PositionStore.from_state_dir(config.state_dir)
        self.hyperliquid_execution = HyperliquidExecutionAdapter(config)
        self.okx_execution = OKXExecutionAdapter(
            config,
            client=client if isinstance(client, OKXClient) else None,
        )

    def execute(
        self,
        intent: OrderIntent,
        mode: ExecutionMode | None = None,
        live_confirmation: str = "",
    ) -> OrderResult:
        selected_mode = mode or self.config.execution_mode
        if self.config.emergency_stop_file and self.config.emergency_stop_file.exists():
            return self._blocked(
                intent,
                f"Emergency stop file exists: {self.config.emergency_stop_file}",
            )

        if selected_mode == "analysis":
            return OrderResult(
                mode="analysis",
                accepted=False,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                message="Analysis mode only creates an order intent; no order is submitted.",
            )

        if selected_mode == "paper":
            result = self.paper.execute(intent)
            self.positions.apply_order_result(intent, result)
            return result

        provider = self.config.exchange_provider.strip().lower()
        if provider == "hyperliquid":
            result = self.hyperliquid_execution.execute(
                intent,
                selected_mode,
                live_confirmation=live_confirmation,
            )
            if selected_mode in {"testnet", "live"} and result.accepted:
                self.positions.apply_order_result(intent, result)
            return result

        if provider == "okx":
            result = self.okx_execution.execute(
                intent,
                selected_mode,
                live_confirmation=live_confirmation,
            )
            if selected_mode == "testnet" and result.accepted:
                self.positions.apply_order_result(intent, result)
            return result

        if selected_mode == "testnet":
            payload = self.client.test_market_order(intent.symbol, intent.side, intent.quantity)
            return OrderResult(
                mode="testnet",
                accepted=True,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                message="Binance test order accepted; no real fill was created.",
                exchange_payload=payload,
            )

        if selected_mode == "live":
            if not self.config.enable_live_orders:
                return self._blocked(
                    intent,
                    "Live switch is disabled: set TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS=true.",
            )
            if live_confirmation != self.config.live_confirm_phrase:
                return self._blocked(
                    intent,
                    "Missing live confirmation phrase; refusing real order.",
                )
            if self.config.testnet:
                return self._blocked(
                    intent,
                    "Current config is still testnet; refusing real order.",
                )
            payload = self.client.create_market_order(intent.symbol, intent.side, intent.quantity)
            result = OrderResult(
                mode="live",
                accepted=True,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                message=(
                    "Real Binance spot market order submitted; confirm final state "
                    "with order query or user data stream immediately."
                ),
                exchange_payload=payload,
            )
            self.positions.apply_order_result(intent, result)
            protected_payload = self._place_live_protection(intent)
            if protected_payload:
                result.exchange_payload["protective_order"] = protected_payload
            return result

        return self._blocked(intent, f"Unknown execution mode: {selected_mode}")

    def _place_live_protection(self, intent: OrderIntent) -> dict:
        if not self.config.protective_oco_enabled:
            return {}
        plan = plan_from_intent(intent, self.config)
        if plan is None:
            return {"skipped": "missing stop_loss or take_profit"}
        try:
            return self.client.create_oco_sell_order(plan.to_binance_params())
        except Exception as exc:
            return {"error": str(exc), "plan": plan.to_binance_params()}

    @staticmethod
    def _blocked(intent: OrderIntent, reason: str) -> OrderResult:
        return OrderResult(
            mode="analysis",
            accepted=False,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            message=reason,
        )
