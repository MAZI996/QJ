"""Hyperliquid public/account client for the crypto trading center."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import CryptoTradingConfig
from .models import AccountBalance, Candle, OpenInterestPoint, SymbolRules, TickerSnapshot


class HyperliquidAPIError(RuntimeError):
    """Raised when Hyperliquid returns a non-2xx response or invalid payload."""


@dataclass(frozen=True)
class HyperliquidMarket:
    name: str
    sz_decimals: int
    max_leverage: int
    only_isolated: bool


class HyperliquidClient:
    def __init__(self, config: CryptoTradingConfig):
        self.config = config

    def ping(self) -> dict[str, Any]:
        payload = self.get_meta()
        return {"universe": len(payload.get("universe", []))}

    def get_meta(self) -> dict[str, Any]:
        payload = self._info({"type": "meta"})
        return payload if isinstance(payload, dict) else {}

    def get_all_mids(self) -> dict[str, float]:
        payload = self._info({"type": "allMids"})
        if not isinstance(payload, dict):
            return {}
        return {str(key).upper(): float(value) for key, value in payload.items()}

    def get_user_state(self, wallet_address: str | None = None) -> dict[str, Any]:
        address = wallet_address or self.config.hyperliquid_wallet_address
        if not address:
            raise HyperliquidAPIError("TRADINGAGENTS_CRYPTO_HYPERLIQUID_WALLET_ADDRESS is empty.")
        payload = self._info({"type": "clearinghouseState", "user": address})
        return payload if isinstance(payload, dict) else {}

    def get_account_balances(self) -> list[AccountBalance]:
        state = self.get_user_state()
        margin = state.get("marginSummary", {})
        account_value = float(margin.get("accountValue", 0.0) or 0.0)
        margin_used = float(margin.get("totalMarginUsed", 0.0) or 0.0)
        withdrawable = float(state.get("withdrawable", account_value) or 0.0)
        return [
            AccountBalance(
                asset="USDC",
                free=withdrawable,
                locked=max(0.0, margin_used),
            )
        ]

    def get_markets(self) -> list[HyperliquidMarket]:
        markets: list[HyperliquidMarket] = []
        for row in self.get_meta().get("universe", []):
            if not isinstance(row, dict):
                continue
            markets.append(
                HyperliquidMarket(
                    name=str(row.get("name", "")).upper(),
                    sz_decimals=int(row.get("szDecimals", 4)),
                    max_leverage=int(row.get("maxLeverage", 1)),
                    only_isolated=bool(row.get("onlyIsolated", False)),
                )
            )
        return markets

    def get_symbol_rules(self, symbol: str) -> SymbolRules:
        coin = self.normalize_symbol(symbol)
        for market in self.get_markets():
            if market.name == coin:
                step = 10 ** (-market.sz_decimals)
                return SymbolRules(
                    symbol=coin,
                    base_asset=coin,
                    quote_asset="USDC",
                    min_qty=step,
                    step_size=step,
                    min_notional=self.config.min_order_notional_usdt,
                )
        raise HyperliquidAPIError(f"No Hyperliquid market metadata returned for {coin}.")

    def get_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        coin = self.normalize_symbol(symbol)
        interval_ms = _interval_to_ms(interval)
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (interval_ms * max(limit, 1))
        payload = self._info(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": start_ms,
                    "endTime": end_ms,
                },
            }
        )
        if not isinstance(payload, list):
            raise HyperliquidAPIError(f"Invalid candleSnapshot payload for {coin}.")
        candles: list[Candle] = []
        for row in payload[-limit:]:
            if not isinstance(row, dict):
                continue
            candles.append(
                Candle(
                    open_time_ms=int(row.get("t", 0)),
                    open=float(row.get("o", 0.0)),
                    high=float(row.get("h", 0.0)),
                    low=float(row.get("l", 0.0)),
                    close=float(row.get("c", 0.0)),
                    volume=float(row.get("v", 0.0)),
                    close_time_ms=int(row.get("T", row.get("t", 0))),
                )
            )
        return candles

    def get_24h_ticker(self, symbol: str) -> TickerSnapshot:
        coin = self.normalize_symbol(symbol)
        mids = self.get_all_mids()
        candles = self.get_klines(coin, "1h", 25)
        last_price = mids.get(coin) or (candles[-1].close if candles else 0.0)
        first_price = candles[0].close if candles else last_price
        change_pct = ((last_price - first_price) / first_price) * 100 if first_price else 0.0
        quote_volume = sum(candle.volume * candle.close for candle in candles[-24:])
        return TickerSnapshot(
            symbol=coin,
            last_price=last_price,
            price_change_pct_24h=change_pct,
            quote_volume_24h=quote_volume,
        )

    def get_open_interest_history(
        self,
        symbol: str,
        period: str,
        limit: int,
    ) -> list[OpenInterestPoint]:
        return []

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        clean = symbol.strip().upper()
        for suffix in ("USDT", "USDC", "-PERP", "PERP"):
            if clean.endswith(suffix):
                clean = clean[: -len(suffix)]
        return clean

    def _info(self, payload: dict[str, Any]) -> Any:
        url = f"{self.config.resolved_hyperliquid_base_url}/info"
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "TradingAgents-Crypto/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise HyperliquidAPIError(f"Hyperliquid HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise HyperliquidAPIError(f"Hyperliquid request failed: {exc.reason}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HyperliquidAPIError(
                f"Hyperliquid returned invalid JSON: {raw[:200]}"
            ) from exc


def _interval_to_ms(interval: str) -> int:
    unit = interval[-1]
    try:
        amount = int(interval[:-1])
    except ValueError:
        amount = 1
    multipliers = {
        "m": 60_000,
        "h": 3_600_000,
        "d": 86_400_000,
    }
    return amount * multipliers.get(unit, 3_600_000)
