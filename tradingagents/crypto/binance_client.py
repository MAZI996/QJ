"""Minimal Binance Spot REST client.

The client uses only the Python standard library so the trading skeleton does
not add another dependency before the strategy proves useful.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

from .config import CryptoTradingConfig
from .models import AccountBalance, Candle, OpenInterestPoint, SymbolRules, TickerSnapshot


class BinanceAPIError(RuntimeError):
    """Raised when Binance returns a non-2xx response or invalid payload."""


class BinanceClient:
    def __init__(self, config: CryptoTradingConfig):
        self.config = config

    def get_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        payload = self._public_get(
            "/api/v3/klines",
            {"symbol": symbol.upper(), "interval": interval, "limit": limit},
        )
        candles: list[Candle] = []
        for row in payload:
            candles.append(
                Candle(
                    open_time_ms=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    close_time_ms=int(row[6]),
                )
            )
        return candles

    def get_24h_ticker(self, symbol: str) -> TickerSnapshot:
        payload = self._public_get("/api/v3/ticker/24hr", {"symbol": symbol.upper()})
        return TickerSnapshot(
            symbol=payload["symbol"],
            last_price=float(payload["lastPrice"]),
            price_change_pct_24h=float(payload["priceChangePercent"]),
            quote_volume_24h=float(payload["quoteVolume"]),
        )

    def get_open_interest_history(
        self,
        symbol: str,
        period: str,
        limit: int,
    ) -> list[OpenInterestPoint]:
        payload = self._futures_public_get(
            "/futures/data/openInterestHist",
            {
                "symbol": symbol.upper(),
                "period": period,
                "limit": limit,
            },
        )
        points: list[OpenInterestPoint] = []
        for row in payload:
            points.append(
                OpenInterestPoint(
                    symbol=row.get("symbol", symbol.upper()),
                    open_interest=float(row.get("sumOpenInterest", "0")),
                    timestamp_ms=int(row.get("timestamp", 0)),
                )
            )
        return points

    def get_symbol_rules(self, symbol: str) -> SymbolRules:
        payload = self._public_get("/api/v3/exchangeInfo", {"symbol": symbol.upper()})
        symbols = payload.get("symbols", [])
        if not symbols:
            raise BinanceAPIError(f"No exchangeInfo returned for {symbol}.")

        info = symbols[0]
        lot_size = self._find_filter(info, "LOT_SIZE")
        min_notional_filter = self._find_filter(info, "MIN_NOTIONAL") or self._find_filter(
            info,
            "NOTIONAL",
        )
        min_notional = float(
            (min_notional_filter or {}).get(
                "minNotional",
                self.config.min_order_notional_usdt,
            )
        )
        return SymbolRules(
            symbol=info["symbol"],
            base_asset=info["baseAsset"],
            quote_asset=info["quoteAsset"],
            min_qty=float((lot_size or {}).get("minQty", "0")),
            step_size=float((lot_size or {}).get("stepSize", "0")),
            min_notional=min_notional,
        )

    def get_account_balances(self) -> list[AccountBalance]:
        payload = self._signed_get("/api/v3/account", {})
        balances = []
        for row in payload.get("balances", []):
            free = float(row.get("free", "0"))
            locked = float(row.get("locked", "0"))
            if free > 0 or locked > 0:
                balances.append(AccountBalance(asset=row["asset"], free=free, locked=locked))
        return balances

    def test_market_order(self, symbol: str, side: str, quantity: float) -> dict[str, Any]:
        return self._signed_post(
            "/api/v3/order/test",
            {
                "symbol": symbol.upper(),
                "side": side.upper(),
                "type": "MARKET",
                "quantity": self._format_quantity(quantity),
            },
        )

    def create_market_order(self, symbol: str, side: str, quantity: float) -> dict[str, Any]:
        return self._signed_post(
            "/api/v3/order",
            {
                "symbol": symbol.upper(),
                "side": side.upper(),
                "type": "MARKET",
                "quantity": self._format_quantity(quantity),
            },
        )

    def _public_get(self, path: str, params: Mapping[str, Any]) -> Any:
        return self._request("GET", path, params=params, signed=False)

    def _futures_public_get(self, path: str, params: Mapping[str, Any]) -> Any:
        return self._request(
            "GET",
            path,
            params=params,
            signed=False,
            base_url=self.config.resolved_futures_base_url,
        )

    def _signed_get(self, path: str, params: Mapping[str, Any]) -> Any:
        if not self.config.api_key or not self.config.api_secret:
            raise BinanceAPIError("Binance API key and secret are required for signed requests.")
        signed_params = dict(params)
        signed_params["timestamp"] = int(time.time() * 1000)
        signed_params["recvWindow"] = self.config.recv_window_ms
        query = urllib.parse.urlencode(signed_params)
        signature = hmac.new(
            self.config.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signed_params["signature"] = signature
        return self._request("GET", path, params=signed_params, signed=True)

    def _signed_post(self, path: str, params: Mapping[str, Any]) -> Any:
        if not self.config.api_key or not self.config.api_secret:
            raise BinanceAPIError("Binance API key and secret are required for signed requests.")
        signed_params = dict(params)
        signed_params["timestamp"] = int(time.time() * 1000)
        signed_params["recvWindow"] = self.config.recv_window_ms
        query = urllib.parse.urlencode(signed_params)
        signature = hmac.new(
            self.config.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signed_params["signature"] = signature
        return self._request("POST", path, params=signed_params, signed=True)

    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        signed: bool = False,
        base_url: str | None = None,
    ) -> Any:
        params = params or {}
        encoded = urllib.parse.urlencode(params)
        url = f"{base_url or self.config.resolved_base_url}{path}"
        data = None
        if method == "GET" and encoded:
            url = f"{url}?{encoded}"
        elif encoded:
            data = encoded.encode("utf-8")

        headers = {"User-Agent": "TradingAgents-Crypto/0.1"}
        if signed:
            headers["X-MBX-APIKEY"] = self.config.api_key

        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        if data is not None:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise BinanceAPIError(f"Binance HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise BinanceAPIError(f"Binance request failed: {exc.reason}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BinanceAPIError(f"Binance returned invalid JSON: {raw[:200]}") from exc

    @staticmethod
    def _format_quantity(quantity: float) -> str:
        text = f"{quantity:.8f}".rstrip("0").rstrip(".")
        return text or "0"

    @staticmethod
    def _find_filter(symbol_info: Mapping[str, Any], filter_type: str) -> dict[str, Any] | None:
        for item in symbol_info.get("filters", []):
            if item.get("filterType") == filter_type:
                return item
        return None
