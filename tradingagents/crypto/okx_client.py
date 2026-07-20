"""OKX REST client for the crypto trading center.

This client exposes public market data plus the signed account and trade
endpoints used by the separately guarded demo execution adapter.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .config import CryptoTradingConfig
from .models import AccountBalance, Candle, OpenInterestPoint, SymbolRules, TickerSnapshot


class OKXAPIError(RuntimeError):
    """Raised when OKX returns a non-OK response or invalid payload."""


class OKXTransportError(OKXAPIError):
    """Raised when an OKX request has an ambiguous transport outcome."""


@dataclass(frozen=True)
class OKXInstrument:
    inst_id: str
    inst_type: str
    base_ccy: str
    quote_ccy: str
    settle_ccy: str
    min_size: float
    lot_size: float
    tick_size: float
    contract_value: float
    contract_type: str = ""
    contract_value_ccy: str = ""
    state: str = ""

    def notional_usd(self, *, price: float, size: float) -> float:
        if self.contract_type == "inverse" and self.contract_value > 0:
            return size * self.contract_value
        if self.contract_type == "linear" and self.contract_value > 0:
            return price * size * self.contract_value
        return price * size


@dataclass(frozen=True)
class OKXFundingRate:
    inst_id: str
    funding_rate: float
    realized_rate: float | None
    funding_time_ms: int

    @property
    def effective_rate(self) -> float:
        return self.realized_rate if self.realized_rate is not None else self.funding_rate


@dataclass(frozen=True)
class OKXOpenInterest:
    inst_id: str
    contracts: float
    currency: float
    usd: float
    time_ms: int


@dataclass(frozen=True)
class OKXBookLevel:
    price: float
    size: float
    order_count: int = 0

    @property
    def notional_usdt(self) -> float:
        return self.price * self.size


@dataclass(frozen=True)
class OKXOrderBook:
    inst_id: str
    time_ms: int
    bids: tuple[OKXBookLevel, ...]
    asks: tuple[OKXBookLevel, ...]

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread_bps(self) -> float | None:
        mid = self.mid_price
        if mid is None or mid <= 0 or self.best_bid is None or self.best_ask is None:
            return None
        return ((self.best_ask - self.best_bid) / mid) * 10_000


class OKXClient:
    def __init__(self, config: CryptoTradingConfig):
        self.config = config
        self._instrument_cache: dict[str, tuple[OKXInstrument, ...]] = {}

    def ping(self) -> dict[str, Any]:
        return {"server_time_ms": self.get_server_time()}

    def get_server_time(self) -> int:
        rows = self._public_get("/api/v5/public/time", {})
        if not rows:
            raise OKXAPIError("OKX public time endpoint returned no data.")
        return int(rows[0].get("ts", 0))

    def get_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        inst_id = self.instrument_id(symbol)
        rows = self._public_get(
            "/api/v5/market/candles",
            {"instId": inst_id, "bar": _okx_interval(interval), "limit": max(1, limit)},
        )
        interval_ms = _interval_to_ms(interval)
        candles: list[Candle] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            open_time = int(row[0])
            candles.append(
                Candle(
                    open_time_ms=open_time,
                    open=_safe_float(row[1]),
                    high=_safe_float(row[2]),
                    low=_safe_float(row[3]),
                    close=_safe_float(row[4]),
                    volume=_safe_float(row[5]),
                    close_time_ms=open_time + interval_ms - 1,
                )
            )
        return sorted(candles, key=lambda candle: candle.open_time_ms)[-limit:]

    def get_24h_ticker(self, symbol: str) -> TickerSnapshot:
        row = self.get_ticker(symbol)
        last = _safe_float(row.get("last"))
        open_24h = _safe_float(row.get("open24h"))
        change_pct = ((last - open_24h) / open_24h) * 100 if open_24h else 0.0
        quote_volume = _safe_float(row.get("volCcyQuote24h"))
        if quote_volume <= 0:
            base_volume = _safe_float(row.get("volCcy24h") or row.get("vol24h"))
            quote_volume = base_volume * last
        return TickerSnapshot(
            symbol=self.normalize_symbol(row.get("instId", symbol)),
            last_price=last,
            price_change_pct_24h=change_pct,
            quote_volume_24h=quote_volume,
        )

    def get_ticker(self, symbol: str) -> dict[str, Any]:
        inst_id = self.instrument_id(symbol)
        rows = self._public_get("/api/v5/market/ticker", {"instId": inst_id})
        if not rows:
            raise OKXAPIError(f"No OKX ticker returned for {inst_id}.")
        row = rows[0]
        if not isinstance(row, dict):
            raise OKXAPIError(f"Invalid OKX ticker payload for {inst_id}.")
        return row

    def get_order_book(self, symbol: str, depth: int = 20) -> OKXOrderBook:
        inst_id = self.instrument_id(symbol)
        rows = self._public_get(
            "/api/v5/market/books",
            {"instId": inst_id, "sz": max(1, min(depth, 400))},
        )
        if not rows:
            raise OKXAPIError(f"No OKX order book returned for {inst_id}.")
        row = rows[0]
        if not isinstance(row, dict):
            raise OKXAPIError(f"Invalid OKX order book payload for {inst_id}.")
        return OKXOrderBook(
            inst_id=inst_id,
            time_ms=int(row.get("ts", 0) or 0),
            bids=_parse_book_side(row.get("bids")),
            asks=_parse_book_side(row.get("asks")),
        )

    def get_instruments(self, inst_type: str | None = None) -> list[OKXInstrument]:
        selected_type = (inst_type or self.config.okx_inst_type).strip().upper()
        cached = self._instrument_cache.get(selected_type)
        if cached is not None:
            return list(cached)
        rows = self._public_get("/api/v5/public/instruments", {"instType": selected_type})
        instruments: list[OKXInstrument] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            inst_id = str(row.get("instId", "")).upper()
            if not inst_id:
                continue
            base, quote = _split_inst_id(inst_id)
            instruments.append(
                OKXInstrument(
                    inst_id=inst_id,
                    inst_type=str(row.get("instType", selected_type)).upper(),
                    base_ccy=str(row.get("baseCcy") or base).upper(),
                    quote_ccy=str(row.get("quoteCcy") or quote).upper(),
                    settle_ccy=str(row.get("settleCcy") or quote).upper(),
                    min_size=_safe_float(row.get("minSz")),
                    lot_size=_safe_float(row.get("lotSz")),
                    tick_size=_safe_float(row.get("tickSz")),
                    contract_value=_safe_float(row.get("ctVal")),
                    contract_type=str(row.get("ctType", "")).lower(),
                    contract_value_ccy=str(row.get("ctValCcy", "")).upper(),
                    state=str(row.get("state", "")).lower(),
                )
            )
        self._instrument_cache[selected_type] = tuple(instruments)
        return list(instruments)

    def get_instrument(self, symbol: str) -> OKXInstrument:
        inst_id = self.instrument_id(symbol)
        for instrument in self.get_instruments(self.config.okx_inst_type):
            if instrument.inst_id == inst_id:
                return instrument
        raise OKXAPIError(f"No OKX instrument metadata returned for {inst_id}.")

    def get_symbol_rules(self, symbol: str) -> SymbolRules:
        instrument = self.get_instrument(symbol)
        min_qty = instrument.min_size
        step_size = instrument.lot_size
        if instrument.contract_type == "linear" and instrument.contract_value > 0:
            min_qty *= instrument.contract_value
            step_size *= instrument.contract_value
        return SymbolRules(
            symbol=self.normalize_symbol(instrument.inst_id),
            base_asset=instrument.base_ccy,
            quote_asset=instrument.quote_ccy
            or instrument.settle_ccy
            or self.config.okx_quote_ccy,
            min_qty=min_qty,
            step_size=step_size,
            min_notional=self.config.min_order_notional_usdt,
        )

    def get_latest_funding_rate(self, symbol: str) -> OKXFundingRate:
        inst_id = self.instrument_id(symbol)
        rows = self._public_get(
            "/api/v5/public/funding-rate-history",
            {"instId": inst_id, "limit": 1},
        )
        if not rows or not isinstance(rows[0], dict):
            raise OKXAPIError(f"No OKX funding rate returned for {inst_id}.")
        row = rows[0]
        return OKXFundingRate(
            inst_id=str(row.get("instId") or inst_id).upper(),
            funding_rate=_safe_float(row.get("fundingRate")),
            realized_rate=_safe_float_or_none(row.get("realizedRate")),
            funding_time_ms=int(row.get("fundingTime", 0) or 0),
        )

    def get_open_interest(self, symbol: str) -> OKXOpenInterest:
        inst_id = self.instrument_id(symbol)
        rows = self._public_get(
            "/api/v5/public/open-interest",
            {"instType": self.config.okx_inst_type.strip().upper(), "instId": inst_id},
        )
        if not rows or not isinstance(rows[0], dict):
            raise OKXAPIError(f"No OKX open interest returned for {inst_id}.")
        row = rows[0]
        return OKXOpenInterest(
            inst_id=str(row.get("instId") or inst_id).upper(),
            contracts=_safe_float(row.get("oi")),
            currency=_safe_float(row.get("oiCcy")),
            usd=_safe_float(row.get("oiUsd")),
            time_ms=int(row.get("ts", 0) or 0),
        )

    def get_market_context(self, symbol: str) -> dict[str, Any]:
        funding = self.get_latest_funding_rate(symbol)
        open_interest = self.get_open_interest(symbol)
        return {
            "funding": funding.effective_rate,
            "fundingTime": funding.funding_time_ms,
            "openInterest": open_interest.contracts,
            "openInterestCcy": open_interest.currency,
            "openInterestUsd": open_interest.usd,
            "openInterestTime": open_interest.time_ms,
        }

    def get_open_interest_history(
        self,
        symbol: str,
        period: str,
        limit: int,
    ) -> list[OpenInterestPoint]:
        return []

    def get_all_mids(self) -> dict[str, float]:
        return {}

    def get_account_balances(self) -> list[AccountBalance]:
        payload = self._signed_get("/api/v5/account/balance", {})
        balances: list[AccountBalance] = []
        for account in payload:
            if not isinstance(account, dict):
                continue
            for row in account.get("details", []):
                if not isinstance(row, dict):
                    continue
                free = _safe_float(row.get("availBal") or row.get("availEq") or row.get("cashBal"))
                locked = _safe_float(row.get("frozenBal"))
                if free > 0 or locked > 0:
                    balances.append(
                        AccountBalance(
                            asset=str(row.get("ccy", "")).upper(),
                            free=free,
                            locked=locked,
                        )
                    )
        return balances

    @property
    def has_credentials(self) -> bool:
        return bool(
            self.config.okx_api_key
            and self.config.okx_api_secret
            and self.config.okx_api_passphrase
        )

    def get_account_config(self) -> dict[str, Any]:
        rows = self._signed_get("/api/v5/account/config", {})
        if not rows or not isinstance(rows[0], dict):
            raise OKXAPIError("No OKX account configuration returned.")
        return rows[0]

    def get_leverage_info(self, symbol: str, margin_mode: str) -> list[dict[str, Any]]:
        inst_id = self.instrument_id(symbol)
        rows = self._signed_get(
            "/api/v5/account/leverage-info",
            {"instId": inst_id, "mgnMode": margin_mode},
        )
        return [row for row in rows if isinstance(row, dict)]

    def get_positions(self, symbol: str) -> list[dict[str, Any]]:
        inst_id = self.instrument_id(symbol)
        rows = self._signed_get("/api/v5/account/positions", {"instId": inst_id})
        return [row for row in rows if isinstance(row, dict)]

    def place_order(self, params: Mapping[str, Any]) -> dict[str, Any]:
        rows = self._signed_post("/api/v5/trade/order", params)
        if not rows or not isinstance(rows[0], dict):
            raise OKXAPIError("No OKX order acknowledgement returned.")
        return rows[0]

    def get_order(
        self,
        symbol: str,
        *,
        order_id: str = "",
        client_order_id: str = "",
    ) -> dict[str, Any]:
        if not order_id and not client_order_id:
            raise OKXAPIError("OKX order query requires order_id or client_order_id.")
        params: dict[str, str] = {"instId": self.instrument_id(symbol)}
        if order_id:
            params["ordId"] = order_id
        else:
            params["clOrdId"] = client_order_id
        rows = self._signed_get("/api/v5/trade/order", params)
        if not rows or not isinstance(rows[0], dict):
            raise OKXAPIError("No OKX order details returned.")
        return rows[0]

    def cancel_order(
        self,
        symbol: str,
        *,
        order_id: str = "",
        client_order_id: str = "",
    ) -> dict[str, Any]:
        if not order_id and not client_order_id:
            raise OKXAPIError("OKX order cancellation requires order_id or client_order_id.")
        params: dict[str, str] = {"instId": self.instrument_id(symbol)}
        if order_id:
            params["ordId"] = order_id
        else:
            params["clOrdId"] = client_order_id
        rows = self._signed_post("/api/v5/trade/cancel-order", params)
        if not rows or not isinstance(rows[0], dict):
            raise OKXAPIError("No OKX cancellation acknowledgement returned.")
        return rows[0]

    @classmethod
    def normalize_symbol(cls, symbol: str) -> str:
        clean = str(symbol).strip().upper().replace("_", "-")
        if "-" in clean:
            return clean.split("-")[0]
        for suffix in ("USDT", "USDC", "USD", "PERP", "SWAP"):
            if clean.endswith(suffix):
                clean = clean[: -len(suffix)]
        return clean

    def instrument_id(self, symbol: str) -> str:
        return self.instrument_id_for(
            symbol,
            quote_ccy=self.config.okx_quote_ccy,
            inst_type=self.config.okx_inst_type,
        )

    @staticmethod
    def instrument_id_for(symbol: str, *, quote_ccy: str = "USDT", inst_type: str = "SWAP") -> str:
        clean = str(symbol).strip().upper().replace("_", "-")
        selected_type = inst_type.strip().upper()
        quote = quote_ccy.strip().upper() or "USDT"
        if "-" in clean:
            if selected_type == "SWAP" and clean.count("-") == 1:
                return f"{clean}-SWAP"
            return clean
        base = clean
        for suffix in (quote, "USDT", "USDC", "USD"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        if selected_type == "SPOT":
            return f"{base}-{quote}"
        if selected_type == "SWAP":
            return f"{base}-{quote}-SWAP"
        return f"{base}-{quote}"

    def _public_get(self, path: str, params: Mapping[str, Any]) -> list[Any]:
        payload = self._request("GET", path, params=params, signed=False)
        if not isinstance(payload, list):
            raise OKXAPIError(f"OKX {path} returned non-list data.")
        return payload

    def _signed_get(self, path: str, params: Mapping[str, Any]) -> list[Any]:
        self._require_credentials()
        payload = self._request("GET", path, params=params, signed=True)
        if not isinstance(payload, list):
            raise OKXAPIError(f"OKX {path} returned non-list data.")
        return payload

    def _signed_post(self, path: str, body: Mapping[str, Any]) -> list[Any]:
        self._require_credentials()
        payload = self._request("POST", path, body=body, signed=True)
        if not isinstance(payload, list):
            raise OKXAPIError(f"OKX {path} returned non-list data.")
        return payload

    def _require_credentials(self) -> None:
        if not self.has_credentials:
            raise OKXAPIError(
                "OKX API key, secret, and passphrase are required for signed requests."
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        params = params or {}
        encoded = urllib.parse.urlencode(params)
        request_path = path + (f"?{encoded}" if encoded else "")
        url = f"{self.config.resolved_okx_base_url}{request_path}"
        headers = {"User-Agent": "TradingAgents-Crypto/0.1"}
        body_text = ""
        request_data = None
        if body is not None:
            body_text = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
            request_data = body_text.encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.config.okx_demo:
            headers["x-simulated-trading"] = "1"
        if signed:
            headers.update(self._signed_headers(method, request_path, body=body_text))

        request = urllib.request.Request(
            url,
            data=request_data,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 408 or exc.code >= 500:
                raise OKXTransportError(f"OKX HTTP {exc.code}: {body}") from exc
            raise OKXAPIError(f"OKX HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise OKXTransportError(f"OKX request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise OKXTransportError("OKX request timed out.") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OKXAPIError(f"OKX returned invalid JSON: {raw[:200]}") from exc
        if not isinstance(payload, dict):
            raise OKXAPIError(f"OKX returned invalid payload: {raw[:200]}")
        code = str(payload.get("code", ""))
        if code != "0":
            message = payload.get("msg") or payload.get("data") or raw[:200]
            raise OKXAPIError(f"OKX API code {code}: {message}")
        return payload.get("data", [])

    def _signed_headers(self, method: str, request_path: str, *, body: str) -> dict[str, str]:
        timestamp = _okx_timestamp()
        prehash = f"{timestamp}{method.upper()}{request_path}{body}"
        digest = hmac.new(
            self.config.okx_api_secret.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return {
            "OK-ACCESS-KEY": self.config.okx_api_key,
            "OK-ACCESS-SIGN": base64.b64encode(digest).decode("ascii"),
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.config.okx_api_passphrase,
        }


def _okx_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _okx_interval(interval: str) -> str:
    clean = interval.strip()
    if not clean:
        return "15m"
    unit = clean[-1]
    amount = clean[:-1]
    if unit == "h":
        return f"{amount}H"
    if unit == "d":
        return f"{amount}D"
    if unit == "w":
        return f"{amount}W"
    return clean


def _interval_to_ms(interval: str) -> int:
    clean = interval.strip()
    if len(clean) < 2:
        return 60_000
    unit = clean[-1].lower()
    try:
        amount = int(clean[:-1])
    except ValueError:
        amount = 1
    multipliers = {
        "m": 60_000,
        "h": 3_600_000,
        "d": 86_400_000,
        "w": 604_800_000,
    }
    return amount * multipliers.get(unit, 60_000)


def _parse_book_side(rows: Any) -> tuple[OKXBookLevel, ...]:
    if not isinstance(rows, list):
        return ()
    levels: list[OKXBookLevel] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        levels.append(
            OKXBookLevel(
                price=_safe_float(row[0]),
                size=_safe_float(row[1]),
                order_count=int(_safe_float(row[3] if len(row) > 3 else 0)),
            )
        )
    return tuple(level for level in levels if level.price > 0 and level.size > 0)


def _split_inst_id(inst_id: str) -> tuple[str, str]:
    parts = inst_id.upper().split("-")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return inst_id.upper(), ""


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
