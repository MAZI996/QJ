"""Crypto trading automation primitives for TradingAgents.

This package is intentionally separate from the stock-oriented graph so the
Binance trading workflow can mature behind its own risk gates before any live
order path is enabled.
"""

from .config import CryptoTradingConfig
from .decision_journal import DecisionJournalWrite, write_workflow_report
from .engine import CryptoTradingEngine
from .models import OpportunitySignal, OrderIntent, RiskDecision
from .workflow_report import CryptoTradingAgentsWorkflow, CryptoWorkflowReport

__all__ = [
    "CryptoTradingConfig",
    "CryptoTradingEngine",
    "CryptoTradingAgentsWorkflow",
    "CryptoWorkflowReport",
    "DecisionJournalWrite",
    "OpportunitySignal",
    "OrderIntent",
    "RiskDecision",
    "write_workflow_report",
]
