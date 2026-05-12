"""Execution adapters with live-trading guardrails."""

from __future__ import annotations

from .binance_client import BinanceClient
from .config import CryptoTradingConfig
from .models import ExecutionMode, OrderIntent, OrderResult
from .paper import PaperBroker


class ExecutionRouter:
    def __init__(self, client: BinanceClient, config: CryptoTradingConfig):
        self.client = client
        self.config = config
        self.paper = PaperBroker(config)

    def execute(
        self,
        intent: OrderIntent,
        mode: ExecutionMode | None = None,
        live_confirmation: str = "",
    ) -> OrderResult:
        selected_mode = mode or self.config.execution_mode
        if self.config.emergency_stop_file and self.config.emergency_stop_file.exists():
            return self._blocked(intent, f"急停文件存在，拒绝执行：{self.config.emergency_stop_file}")

        if selected_mode == "analysis":
            return OrderResult(
                mode="analysis",
                accepted=False,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                message="分析模式只生成交易意图，不提交订单",
            )

        if selected_mode == "paper":
            return self.paper.execute(intent)

        if selected_mode == "testnet":
            payload = self.client.test_market_order(intent.symbol, intent.side, intent.quantity)
            return OrderResult(
                mode="testnet",
                accepted=True,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                message="Binance test order 已通过，未产生真实成交",
                exchange_payload=payload,
            )

        if selected_mode == "live":
            if not self.config.enable_live_orders:
                return self._blocked(intent, "实盘开关未开启：TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS=true")
            if live_confirmation != self.config.live_confirm_phrase:
                return self._blocked(intent, "缺少实盘确认短语，拒绝提交真实订单")
            if self.config.testnet:
                return self._blocked(intent, "当前仍是 Testnet 配置，不能当作实盘提交")
            payload = self.client.create_market_order(intent.symbol, intent.side, intent.quantity)
            return OrderResult(
                mode="live",
                accepted=True,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                message="真实 Binance 现货市价单已提交，必须立刻用订单查询或用户数据流确认最终状态",
                exchange_payload=payload,
            )

        return self._blocked(intent, f"未知执行模式：{selected_mode}")

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
