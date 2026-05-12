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
    futures_base_url: str | None = None
    recv_window_ms: int = 5000

    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")
    interval: str = "15m"
    lookback_limit: int = 120

    account_equity_usdt: float = 10_000.0
    risk_per_trade_pct: float = 0.005
    max_loss_per_trade_usdt: float = 0.0
    max_position_pct: float = 0.10
    daily_loss_limit_pct: float = 0.03
    daily_loss_limit_usdt: float = 0.0
    min_confidence: float = 0.62
    min_order_notional_usdt: float = 10.0
    protective_oco_enabled: bool = False
    protective_stop_limit_slippage_pct: float = 0.003

    lana_strategy_enabled: bool = True
    lana_hot_symbols: tuple[str, ...] = ()
    hotlist_enabled: bool = True
    hotlist_path: Path = Path.home() / ".tradingagents" / "crypto" / "hotlist.json"
    attention_source_dir: Path = Path.home() / ".tradingagents" / "crypto" / "attention_sources"
    hotlist_max_age_hours: float = 24.0
    hotlist_min_score: float = 0.0
    lana_min_price_change_pct: float = 3.0
    lana_max_price_change_pct: float = 18.0
    lana_min_quote_volume_usdt: float = 20_000_000.0
    lana_min_volume_ratio: float = 1.4
    lana_oi_lookback: str = "4h"
    lana_oi_limit: int = 12
    lana_min_oi_change_pct: float = 8.0
    lana_fixed_stop_loss_pct: float = 0.025
    lana_take_profit_r_multiple: float = 2.0
    strategy_fusion_enabled: bool = True
    strategy_fusion_min_score: float = 0.45

    ai_router: str = "tradingagents"
    ai_model: str = ""
    ai_decision_policy: str = "advisory_only"
    ai_agent_style: str = "tradingagents_crypto"
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

    @property
    def resolved_futures_base_url(self) -> str:
        if self.futures_base_url:
            return self.futures_base_url.rstrip("/")
        if self.testnet:
            return "https://testnet.binancefuture.com"
        return "https://fapi.binance.com"

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
            futures_base_url=os.getenv(prefix + "BINANCE_FUTURES_BASE_URL") or None,
            recv_window_ms=_int_env(prefix + "BINANCE_RECV_WINDOW_MS", 5000),
            symbols=_list_env(prefix + "SYMBOLS", ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")),
            interval=os.getenv(prefix + "INTERVAL", "15m"),
            lookback_limit=_int_env(prefix + "LOOKBACK_LIMIT", 120),
            account_equity_usdt=_float_env(prefix + "ACCOUNT_EQUITY_USDT", 10_000.0),
            risk_per_trade_pct=_float_env(prefix + "RISK_PER_TRADE_PCT", 0.005),
            max_loss_per_trade_usdt=_float_env(prefix + "MAX_LOSS_PER_TRADE_USDT", 0.0),
            max_position_pct=_float_env(prefix + "MAX_POSITION_PCT", 0.10),
            daily_loss_limit_pct=_float_env(prefix + "DAILY_LOSS_LIMIT_PCT", 0.03),
            daily_loss_limit_usdt=_float_env(prefix + "DAILY_LOSS_LIMIT_USDT", 0.0),
            min_confidence=_float_env(prefix + "MIN_CONFIDENCE", 0.62),
            min_order_notional_usdt=_float_env(prefix + "MIN_ORDER_NOTIONAL_USDT", 10.0),
            protective_oco_enabled=_bool_env(prefix + "PROTECTIVE_OCO_ENABLED", False),
            protective_stop_limit_slippage_pct=_float_env(
                prefix + "PROTECTIVE_STOP_LIMIT_SLIPPAGE_PCT",
                0.003,
            ),
            lana_strategy_enabled=_bool_env(prefix + "LANA_STRATEGY_ENABLED", True),
            lana_hot_symbols=_list_env(prefix + "LANA_HOT_SYMBOLS", ()),
            hotlist_enabled=_bool_env(prefix + "HOTLIST_ENABLED", True),
            hotlist_path=Path(
                os.getenv(
                    prefix + "HOTLIST_PATH",
                    str(Path.home() / ".tradingagents" / "crypto" / "hotlist.json"),
                )
            ),
            attention_source_dir=Path(
                os.getenv(
                    prefix + "ATTENTION_SOURCE_DIR",
                    str(Path.home() / ".tradingagents" / "crypto" / "attention_sources"),
                )
            ),
            hotlist_max_age_hours=_float_env(prefix + "HOTLIST_MAX_AGE_HOURS", 24.0),
            hotlist_min_score=_float_env(prefix + "HOTLIST_MIN_SCORE", 0.0),
            lana_min_price_change_pct=_float_env(prefix + "LANA_MIN_PRICE_CHANGE_PCT", 3.0),
            lana_max_price_change_pct=_float_env(prefix + "LANA_MAX_PRICE_CHANGE_PCT", 18.0),
            lana_min_quote_volume_usdt=_float_env(
                prefix + "LANA_MIN_QUOTE_VOLUME_USDT",
                20_000_000.0,
            ),
            lana_min_volume_ratio=_float_env(prefix + "LANA_MIN_VOLUME_RATIO", 1.4),
            lana_oi_lookback=os.getenv(prefix + "LANA_OI_LOOKBACK", "4h"),
            lana_oi_limit=_int_env(prefix + "LANA_OI_LIMIT", 12),
            lana_min_oi_change_pct=_float_env(prefix + "LANA_MIN_OI_CHANGE_PCT", 8.0),
            lana_fixed_stop_loss_pct=_float_env(prefix + "LANA_FIXED_STOP_LOSS_PCT", 0.025),
            lana_take_profit_r_multiple=_float_env(
                prefix + "LANA_TAKE_PROFIT_R_MULTIPLE",
                2.0,
            ),
            strategy_fusion_enabled=_bool_env(prefix + "STRATEGY_FUSION_ENABLED", True),
            strategy_fusion_min_score=_float_env(prefix + "STRATEGY_FUSION_MIN_SCORE", 0.45),
            ai_router=os.getenv(prefix + "AI_ROUTER", "tradingagents"),
            ai_model=os.getenv(prefix + "AI_MODEL", ""),
            ai_decision_policy=os.getenv(prefix + "AI_DECISION_POLICY", "advisory_only"),
            ai_agent_style=os.getenv(prefix + "AI_AGENT_STYLE", "tradingagents_crypto"),
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
