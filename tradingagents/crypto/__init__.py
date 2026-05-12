"""Crypto trading automation primitives for TradingAgents.

This package is intentionally separate from the stock-oriented graph so the
Binance trading workflow can mature behind its own risk gates before any live
order path is enabled.
"""

from .config import CryptoTradingConfig
from .autopilot import AutoPilotCycleResult, CryptoAutoPilot, CryptoAutoPilotSafetyError
from .decision_journal import DecisionJournalWrite, write_workflow_report
from .engine import CryptoTradingEngine
from .models import OpportunitySignal, OrderIntent, RiskDecision
from .strategy_fusion import HIGH_STAR_STRATEGY_REFERENCES, StrategyFusionEngine
from .workflow_report import CryptoTradingAgentsWorkflow, CryptoWorkflowReport

__all__ = [
    "CryptoTradingConfig",
    "CryptoTradingEngine",
    "CryptoTradingAgentsWorkflow",
    "CryptoWorkflowReport",
    "DecisionJournalWrite",
    "AutoPilotCycleResult",
    "CryptoAutoPilot",
    "CryptoAutoPilotSafetyError",
    "HIGH_STAR_STRATEGY_REFERENCES",
    "OpportunitySignal",
    "OrderIntent",
    "RiskDecision",
    "StrategyFusionEngine",
    "write_workflow_report",
]
