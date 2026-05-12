"""Crypto trading automation primitives for TradingAgents.

This package is intentionally separate from the stock-oriented graph so crypto
venue adapters can mature behind their own risk gates before any live order
path is enabled.
"""

from .config import CryptoTradingConfig
from .autopilot import AutoPilotCycleResult, CryptoAutoPilot, CryptoAutoPilotSafetyError
from .attention_harvester import AttentionHarvester, AttentionHarvestResult
from .binance_diagnostics import BinanceDiagnosticReport, BinanceDiagnostics
from .circuit_breaker import CircuitBreakerState, DailyLossCircuitBreaker
from .decision_journal import DecisionJournalWrite, write_workflow_report
from .engine import CryptoTradingEngine
from .hyperliquid_client import (
    HyperliquidAPIError,
    HyperliquidBookLevel,
    HyperliquidClient,
    HyperliquidMarket,
    HyperliquidOrderBook,
)
from .hyperliquid_diagnostics import HyperliquidDiagnosticReport, HyperliquidDiagnostics
from .market_quality import MarketQualityDecision, MarketQualityGate
from .models import OpportunitySignal, OrderIntent, RiskDecision
from .order_recovery import OrderRecoveryResult, OrderRecoveryService
from .performance import PerformanceSummary, summarize_performance
from .positions import PositionRecord, PositionStore
from .protective_orders import ProtectiveOrderPlan
from .strategy_fusion import HIGH_STAR_STRATEGY_REFERENCES, StrategyFusionEngine
from .workflow_report import CryptoTradingAgentsWorkflow, CryptoWorkflowReport

__all__ = [
    "CryptoTradingConfig",
    "CryptoTradingEngine",
    "CryptoTradingAgentsWorkflow",
    "CryptoWorkflowReport",
    "DecisionJournalWrite",
    "AutoPilotCycleResult",
    "AttentionHarvester",
    "AttentionHarvestResult",
    "BinanceDiagnosticReport",
    "BinanceDiagnostics",
    "CircuitBreakerState",
    "CryptoAutoPilot",
    "CryptoAutoPilotSafetyError",
    "DailyLossCircuitBreaker",
    "HIGH_STAR_STRATEGY_REFERENCES",
    "HyperliquidAPIError",
    "HyperliquidBookLevel",
    "HyperliquidClient",
    "HyperliquidDiagnosticReport",
    "HyperliquidDiagnostics",
    "HyperliquidMarket",
    "HyperliquidOrderBook",
    "MarketQualityDecision",
    "MarketQualityGate",
    "OpportunitySignal",
    "OrderIntent",
    "OrderRecoveryResult",
    "OrderRecoveryService",
    "PerformanceSummary",
    "PositionRecord",
    "PositionStore",
    "ProtectiveOrderPlan",
    "RiskDecision",
    "StrategyFusionEngine",
    "summarize_performance",
    "write_workflow_report",
]
