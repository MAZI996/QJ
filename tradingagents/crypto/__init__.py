"""Crypto trading automation primitives for TradingAgents.

This package is intentionally separate from the stock-oriented graph so the
Binance trading workflow can mature behind its own risk gates before any live
order path is enabled.
"""

from .config import CryptoTradingConfig
from .engine import CryptoTradingEngine
from .models import OpportunitySignal, OrderIntent, RiskDecision

__all__ = [
    "CryptoTradingConfig",
    "CryptoTradingEngine",
    "OpportunitySignal",
    "OrderIntent",
    "RiskDecision",
]
