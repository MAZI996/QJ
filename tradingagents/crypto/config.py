"""Configuration for the Binance crypto trading workflow."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .models import ExecutionMode


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _list_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return tuple(item.strip().upper() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class CryptoTradingConfig:
    """Runtime knobs for scan, risk, and execution.

    Live trading is disabled by default. A live order requires both CLI intent
    and ``enable_live_orders=True`` so a configuration mistake does not place
    real orders.
    """

    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True
    base_url: str | None = None
    recv_window_ms: int = 5000

    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")
    interval: str = "15m"
    lookback_limit: int = 120

    account_equity_usdt: float = 10_000.0
    risk_per_trade_pct: float = 0.005
    max_position_pct: float = 0.10
    daily_loss_limit_pct: float = 0.03
    min_confidence: float = 0.62
    min_order_notional_usdt: float = 10.0

    ai_router: str = "tradingagents"
    ai_model: str = ""
    ai_decision_policy: str = "advisory_only"
    hermes_base_url: str = ""
    hermes_api_key: str = ""
    hermes_timeout_seconds: int = 45

    execution_mode: ExecutionMode = "analysis"
    enable_live_orders: bool = False
    live_confirm_phrase: str = "I_UNDERSTAND_THIS_PLACES_REAL_BINANCE_ORDERS"
    state_dir: Path = Path.home() / ".tradingagents" / "crypto"
    emergency_stop_file: Path | None = None

    @property
    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        if self.testnet:
            return "https://testnet.binance.vision"
        return "https://api.binance.com"

    @classmethod
    def from_env(cls) -> "CryptoTradingConfig":
        prefix = "TRADINGAGENTS_CRYPTO_"
        return cls(
            api_key=os.getenv("BINANCE_API_KEY", os.getenv(prefix + "BINANCE_API_KEY", "")),
            api_secret=os.getenv(
                "BINANCE_API_SECRET",
                os.getenv(prefix + "BINANCE_API_SECRET", ""),
            ),
            testnet=_bool_env(prefix + "BINANCE_TESTNET", True),
            base_url=os.getenv(prefix + "BINANCE_BASE_URL") or None,
            recv_window_ms=_int_env(prefix + "BINANCE_RECV_WINDOW_MS", 5000),
            symbols=_list_env(prefix + "SYMBOLS", ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")),
            interval=os.getenv(prefix + "INTERVAL", "15m"),
            lookback_limit=_int_env(prefix + "LOOKBACK_LIMIT", 120),
            account_equity_usdt=_float_env(prefix + "ACCOUNT_EQUITY_USDT", 10_000.0),
            risk_per_trade_pct=_float_env(prefix + "RISK_PER_TRADE_PCT", 0.005),
            max_position_pct=_float_env(prefix + "MAX_POSITION_PCT", 0.10),
            daily_loss_limit_pct=_float_env(prefix + "DAILY_LOSS_LIMIT_PCT", 0.03),
            min_confidence=_float_env(prefix + "MIN_CONFIDENCE", 0.62),
            min_order_notional_usdt=_float_env(prefix + "MIN_ORDER_NOTIONAL_USDT", 10.0),
            ai_router=os.getenv(prefix + "AI_ROUTER", "tradingagents"),
            ai_model=os.getenv(prefix + "AI_MODEL", ""),
            ai_decision_policy=os.getenv(prefix + "AI_DECISION_POLICY", "advisory_only"),
            hermes_base_url=os.getenv(prefix + "HERMES_BASE_URL", ""),
            hermes_api_key=os.getenv(prefix + "HERMES_API_KEY", ""),
            hermes_timeout_seconds=_int_env(prefix + "HERMES_TIMEOUT_SECONDS", 45),
            execution_mode=os.getenv(prefix + "EXECUTION_MODE", "analysis"),  # type: ignore[arg-type]
            enable_live_orders=_bool_env(prefix + "ENABLE_LIVE_ORDERS", False),
            live_confirm_phrase=os.getenv(
                prefix + "LIVE_CONFIRM_PHRASE",
                "I_UNDERSTAND_THIS_PLACES_REAL_BINANCE_ORDERS",
            ),
            state_dir=Path(
                os.getenv(
                    prefix + "STATE_DIR",
                    str(Path.home() / ".tradingagents" / "crypto"),
                )
            ),
            emergency_stop_file=(
                Path(os.getenv(prefix + "EMERGENCY_STOP_FILE", ""))
                if os.getenv(prefix + "EMERGENCY_STOP_FILE")
                else None
            ),
        )
