"""Deterministic market-quality gates for crypto execution candidates."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .config import CryptoTradingConfig
from .hyperliquid_client import HyperliquidAPIError, HyperliquidClient, HyperliquidOrderBook
from .models import OpportunitySignal
from .okx_client import OKXAPIError, OKXClient, OKXInstrument, OKXOrderBook


@dataclass(frozen=True)
class MarketQualityDecision:
    symbol: str
    approved: bool
    score: float
    spread_bps: float | None = None
    bid_depth_usdc: float = 0.0
    ask_depth_usdc: float = 0.0
    imbalance: float | None = None
    funding_rate: float | None = None
    open_interest: float | None = None
    open_interest_usd: float | None = None
    reasons: tuple[str, ...] = ()


class MarketQualityGate:
    """Reject fragile setups before risk sizing or execution can occur."""

    def __init__(self, config: CryptoTradingConfig, client: Any):
        self.config = config
        self.client = client

    def evaluate(self, symbol: str) -> MarketQualityDecision:
        if not self.config.market_quality_enabled:
            return MarketQualityDecision(
                symbol=symbol,
                approved=True,
                score=1.0,
                reasons=("market quality gate disabled",),
            )

        provider = self.config.exchange_provider.strip().lower()
        if provider == "okx":
            return self._evaluate_okx(symbol)
        if provider == "hyperliquid":
            return self._evaluate_hyperliquid(symbol)
        return MarketQualityDecision(
            symbol=symbol,
            approved=True,
            score=1.0,
            reasons=(f"market quality gate is not implemented for provider {provider}",),
        )

    def _evaluate_hyperliquid(self, symbol: str) -> MarketQualityDecision:
        if not isinstance(self.client, HyperliquidClient):
            return MarketQualityDecision(
                symbol=symbol,
                approved=False,
                score=0.0,
                reasons=("Hyperliquid market quality requires HyperliquidClient",),
            )

        try:
            book = self.client.get_l2_book(symbol)
            context = self.client.get_asset_context(symbol)
        except HyperliquidAPIError as exc:
            return MarketQualityDecision(
                symbol=HyperliquidClient.normalize_symbol(symbol),
                approved=False,
                score=0.0,
                reasons=(f"market quality data unavailable: {exc}",),
            )

        return self._judge(
            symbol=book.coin,
            book=book,
            context=context,
        )

    def _evaluate_okx(self, symbol: str) -> MarketQualityDecision:
        normalized = OKXClient.normalize_symbol(symbol)
        if not isinstance(self.client, OKXClient):
            return MarketQualityDecision(
                symbol=normalized,
                approved=False,
                score=0.0,
                reasons=("OKX market quality requires OKXClient",),
            )
        if self.config.okx_inst_type.strip().upper() != "SWAP":
            return MarketQualityDecision(
                symbol=normalized,
                approved=False,
                score=0.0,
                reasons=("OKX market quality currently requires SWAP instruments",),
            )
        try:
            book = self.client.get_order_book(
                symbol,
                depth=self.config.market_quality_depth_levels,
            )
            instrument = self.client.get_instrument(symbol)
            context = self.client.get_market_context(symbol)
        except OKXAPIError as exc:
            return MarketQualityDecision(
                symbol=normalized,
                approved=False,
                score=0.0,
                reasons=(f"market quality data unavailable: {exc}",),
            )

        return self._judge(
            symbol=normalized,
            book=book,
            context=context,
            instrument=instrument,
            require_open_interest_usd=True,
        )

    def apply(self, signal: OpportunitySignal) -> OpportunitySignal:
        decision = self.evaluate(signal.symbol)
        metrics = dict(signal.metrics)
        if decision.spread_bps is not None:
            metrics["market_spread_bps"] = decision.spread_bps
        metrics["market_quality_score"] = decision.score
        metrics["market_bid_depth_usdc"] = decision.bid_depth_usdc
        metrics["market_ask_depth_usdc"] = decision.ask_depth_usdc
        if decision.imbalance is not None:
            metrics["market_orderbook_imbalance"] = decision.imbalance
        if decision.funding_rate is not None:
            metrics["market_funding_rate"] = decision.funding_rate
        if decision.open_interest is not None:
            metrics["market_open_interest"] = decision.open_interest
        if decision.open_interest_usd is not None:
            metrics["market_open_interest_usd"] = decision.open_interest_usd

        reasons = tuple(
            list(signal.reasons)
            + ["market quality: " + "; ".join(decision.reasons)]
        )
        if decision.approved:
            return replace(signal, metrics=metrics, reasons=reasons)

        side = "HOLD" if signal.side == "BUY" else signal.side
        confidence = signal.confidence
        if signal.side == "BUY":
            confidence = min(signal.confidence, max(0.0, self.config.min_confidence - 0.02))
        return replace(signal, side=side, confidence=confidence, metrics=metrics, reasons=reasons)

    def _judge(
        self,
        *,
        symbol: str,
        book: HyperliquidOrderBook | OKXOrderBook,
        context: dict[str, Any],
        instrument: OKXInstrument | None = None,
        require_open_interest_usd: bool = False,
    ) -> MarketQualityDecision:
        spread_bps = book.spread_bps
        bid_depth = _depth_usdc(
            book.bids,
            self.config.market_quality_depth_levels,
            instrument=instrument,
        )
        ask_depth = _depth_usdc(
            book.asks,
            self.config.market_quality_depth_levels,
            instrument=instrument,
        )
        total_depth = bid_depth + ask_depth
        imbalance = ((bid_depth - ask_depth) / total_depth) if total_depth > 0 else None
        funding_rate = _safe_float_or_none(context.get("funding"))
        open_interest = _safe_float_or_none(context.get("openInterest"))
        open_interest_usd = _safe_float_or_none(context.get("openInterestUsd"))

        rejected: list[str] = []
        notes: list[str] = []
        if spread_bps is None:
            rejected.append("missing best bid/ask spread")
        elif spread_bps > self.config.market_quality_max_spread_bps:
            rejected.append(
                f"spread {spread_bps:.2f}bps > {self.config.market_quality_max_spread_bps:.2f}bps"
            )
        else:
            notes.append(f"spread {spread_bps:.2f}bps")

        min_depth = self.config.market_quality_min_depth_usdc
        if bid_depth < min_depth or ask_depth < min_depth:
            rejected.append(
                f"top depth bid/ask {bid_depth:.0f}/{ask_depth:.0f} USD < {min_depth:.0f}"
            )
        else:
            notes.append(f"top depth bid/ask {bid_depth:.0f}/{ask_depth:.0f} USD")

        if (
            imbalance is not None
            and abs(imbalance) > self.config.market_quality_max_abs_imbalance
        ):
            rejected.append(
                f"book imbalance {imbalance:+.2f} exceeds "
                f"{self.config.market_quality_max_abs_imbalance:.2f}"
            )
        elif imbalance is not None:
            notes.append(f"book imbalance {imbalance:+.2f}")

        if (
            funding_rate is not None
            and abs(funding_rate) > self.config.market_quality_max_abs_funding_rate
        ):
            rejected.append(
                f"funding {funding_rate:+.5f} exceeds "
                f"{self.config.market_quality_max_abs_funding_rate:.5f}"
            )
        elif funding_rate is not None:
            notes.append(f"funding {funding_rate:+.5f}")

        if require_open_interest_usd:
            min_open_interest = self.config.market_quality_min_open_interest_usd
            if open_interest_usd is None:
                rejected.append("missing open interest USD")
            elif open_interest_usd < min_open_interest:
                rejected.append(
                    f"open interest {open_interest_usd:.0f} USD < {min_open_interest:.0f}"
                )
            else:
                notes.append(f"open interest {open_interest_usd:.0f} USD")

        score = _quality_score(
            spread_bps,
            bid_depth,
            ask_depth,
            imbalance,
            funding_rate,
            open_interest_usd,
            require_open_interest_usd,
            self.config,
        )
        reasons = tuple(rejected or notes or ("market quality data loaded",))
        return MarketQualityDecision(
            symbol=symbol,
            approved=not rejected,
            score=score,
            spread_bps=spread_bps,
            bid_depth_usdc=bid_depth,
            ask_depth_usdc=ask_depth,
            imbalance=imbalance,
            funding_rate=funding_rate,
            open_interest=open_interest,
            open_interest_usd=open_interest_usd,
            reasons=reasons,
        )


def _depth_usdc(
    levels: tuple[Any, ...],
    limit: int,
    *,
    instrument: OKXInstrument | None = None,
) -> float:
    selected = levels[: max(1, limit)]
    if instrument is not None:
        return sum(
            instrument.notional_usd(price=level.price, size=level.size)
            for level in selected
        )
    return sum(level.notional_usdc for level in selected)


def _safe_float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quality_score(
    spread_bps: float | None,
    bid_depth: float,
    ask_depth: float,
    imbalance: float | None,
    funding_rate: float | None,
    open_interest_usd: float | None,
    require_open_interest_usd: bool,
    config: CryptoTradingConfig,
) -> float:
    score = 1.0
    if spread_bps is None:
        score -= 0.40
    elif config.market_quality_max_spread_bps > 0:
        score -= min(0.35, (spread_bps / config.market_quality_max_spread_bps) * 0.25)

    min_depth = max(config.market_quality_min_depth_usdc, 1.0)
    thin_depth = min(bid_depth, ask_depth)
    if thin_depth < min_depth:
        score -= min(0.35, ((min_depth - thin_depth) / min_depth) * 0.35)

    if imbalance is not None:
        score -= min(0.20, abs(imbalance) * 0.15)

    max_funding = max(config.market_quality_max_abs_funding_rate, 0.000001)
    if funding_rate is not None:
        score -= min(0.20, (abs(funding_rate) / max_funding) * 0.15)

    if require_open_interest_usd:
        min_open_interest = max(config.market_quality_min_open_interest_usd, 1.0)
        if open_interest_usd is None:
            score -= 0.20
        elif open_interest_usd < min_open_interest:
            score -= min(
                0.20,
                ((min_open_interest - open_interest_usd) / min_open_interest) * 0.20,
            )
    return max(0.0, min(1.0, score))
