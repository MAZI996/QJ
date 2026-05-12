"""TradingAgents-style report layer for the crypto workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .advisor import CryptoAIAdvisor
from .agent_workflow import CRYPTO_AGENT_ROLES
from .base_contract import TRADINGAGENTS_ROLE_CHAIN
from .config import CryptoTradingConfig
from .engine import CryptoTradingEngine, ReviewedSignal
from .llm_router import CryptoLLMRouterNotReady, create_crypto_review_llm
from .models import AITradeReview, ExecutionMode


@dataclass(frozen=True)
class CryptoRoleReport:
    role: str
    content: str


@dataclass(frozen=True)
class CryptoWorkflowReport:
    reviewed: tuple[ReviewedSignal, ...]
    ai_review: AITradeReview | None
    role_reports: tuple[CryptoRoleReport, ...]
    execution_mode: ExecutionMode
    ai_error: str = ""

    @property
    def approved(self) -> tuple[ReviewedSignal, ...]:
        return tuple(item for item in self.reviewed if item.risk.approved)

    def render_markdown(self) -> str:
        parts = [
            "# Crypto TradingAgents Workflow Report",
            "",
            f"Role chain: {' -> '.join(TRADINGAGENTS_ROLE_CHAIN)}",
            f"Execution mode: {self.execution_mode}",
            f"Candidates scanned: {len(self.reviewed)}",
            f"Risk-approved candidates: {len(self.approved)}",
            "",
        ]
        if self.ai_review:
            parts.extend(
                [
                    "## AI Review",
                    (
                        f"Action: {self.ai_review.action} | "
                        f"Confidence: {self.ai_review.confidence:.2f} | "
                        f"Router: {self.ai_review.router} | "
                        f"Model: {self.ai_review.model or '-'}"
                    ),
                    f"Summary: {self.ai_review.summary or '-'}",
                    f"Main risk: {self.ai_review.main_risk or '-'}",
                    f"Invalidation: {self.ai_review.invalidation or '-'}",
                    "",
                ]
            )
        elif self.ai_error:
            parts.extend(["## AI Review", f"Skipped: {self.ai_error}", ""])

        for report in self.role_reports:
            parts.extend([f"## {report.role}", report.content, ""])
        return "\n".join(parts).strip()


class CryptoTradingAgentsWorkflow:
    """Run scan/risk/AI review and render it as a TradingAgents role hand-off."""

    def __init__(
        self,
        config: CryptoTradingConfig | None = None,
        engine: CryptoTradingEngine | None = None,
    ):
        self.config = config or CryptoTradingConfig.from_env()
        self.engine = engine or CryptoTradingEngine(self.config)

    def run(
        self,
        symbols: tuple[str, ...] | None = None,
        execute_top: bool = False,
        execution_mode: ExecutionMode | None = None,
        live_confirmation: str = "",
        ai_review_enabled: bool = False,
    ) -> CryptoWorkflowReport:
        mode = execution_mode or self.config.execution_mode
        reviewed = tuple(
            self.engine.scan_and_review(
                symbols=symbols,
                execute_top=execute_top,
                execution_mode=mode,
                live_confirmation=live_confirmation,
            )
        )
        ai_review, ai_error = self._optional_ai_review(reviewed, ai_review_enabled)
        return CryptoWorkflowReport(
            reviewed=reviewed,
            ai_review=ai_review,
            role_reports=tuple(self._build_role_reports(reviewed, ai_review)),
            execution_mode=mode,
            ai_error=ai_error,
        )

    def _optional_ai_review(
        self,
        reviewed: tuple[ReviewedSignal, ...],
        enabled: bool,
    ) -> tuple[AITradeReview | None, str]:
        if not enabled:
            return None, ""
        try:
            llm = create_crypto_review_llm(self.config)
            model_name = self.config.ai_model or getattr(
                llm,
                "model_name",
                getattr(llm, "model", ""),
            )
            review = CryptoAIAdvisor(
                llm,
                router=self.config.ai_router,
                model=model_name,
            ).review_structured(list(reviewed))
            return review, ""
        except CryptoLLMRouterNotReady as exc:
            return None, str(exc)
        except Exception as exc:  # Keep scan output available when the AI layer fails.
            return None, f"AI review failed: {exc}"

    def _build_role_reports(
        self,
        reviewed: tuple[ReviewedSignal, ...],
        ai_review: AITradeReview | None,
    ) -> Iterable[CryptoRoleReport]:
        approved = tuple(item for item in reviewed if item.risk.approved)
        rejected = tuple(item for item in reviewed if not item.risk.approved)
        best = approved[0] if approved else (reviewed[0] if reviewed else None)

        builders = {
            "Market Analyst": lambda: self._market_report(reviewed),
            "Sentiment Analyst": lambda: self._attention_report(reviewed),
            "Bull Researcher": lambda: self._bull_report(approved, best),
            "Bear Researcher": lambda: self._bear_report(rejected, best),
            "Research Manager": lambda: self._manager_report(approved, rejected),
            "Trader": lambda: self._trader_report(best),
            "Risk Analysts": lambda: self._risk_report(reviewed),
            "Portfolio Manager": lambda: self._portfolio_report(approved, ai_review),
        }
        for role in CRYPTO_AGENT_ROLES:
            builder = builders.get(role.name)
            if builder:
                yield CryptoRoleReport(role=role.name, content=builder())

    def _market_report(self, reviewed: tuple[ReviewedSignal, ...]) -> str:
        if not reviewed:
            return "No Binance spot candidates were produced by the scanner."
        lines = [
            "Top scanner candidates:",
            *(_candidate_line(item) for item in reviewed[:5]),
        ]
        return "\n".join(lines)

    def _attention_report(self, reviewed: tuple[ReviewedSignal, ...]) -> str:
        if not reviewed:
            return "No attention evidence is available."
        hot = [
            item
            for item in reviewed
            if item.signal.strategy.startswith("lana-inspired")
            or "hotlist_score" in item.signal.metrics
        ]
        if not hot:
            return (
                "No Lana/hotlist candidate dominated this scan. Treat social "
                "attention as unknown until X, Binance Square, or forum text is ingested."
            )
        return "\n".join(
            ["Attention-linked candidates:", *(_candidate_line(item) for item in hot[:5])]
        )

    def _bull_report(
        self,
        approved: tuple[ReviewedSignal, ...],
        best: ReviewedSignal | None,
    ) -> str:
        if approved:
            return "\n".join(
                [
                    "Strongest long-only spot case:",
                    _candidate_line(approved[0]),
                    _reasons(approved[0]),
                ]
            )
        if best:
            return (
                "No candidate passed the hard risk gate. The strongest raw setup was "
                f"{best.signal.symbol}, but it remains HOLD/REJECT until risk approves it."
            )
        return "No long case is available."

    def _bear_report(
        self,
        rejected: tuple[ReviewedSignal, ...],
        best: ReviewedSignal | None,
    ) -> str:
        if rejected:
            lines = ["Primary rejection and invalidation evidence:"]
            for item in rejected[:5]:
                rejected_rules = "; ".join(item.risk.rejected_rules) or item.risk.reason
                lines.append(f"- {item.signal.symbol}: {rejected_rules}")
            return "\n".join(lines)
        if best:
            return (
                f"{best.signal.symbol} passed deterministic risk, but the setup still "
                "fails if price loses the stop, liquidity thins, or momentum stalls."
            )
        return "No bear case is available because no candidates were scanned."

    def _manager_report(
        self,
        approved: tuple[ReviewedSignal, ...],
        rejected: tuple[ReviewedSignal, ...],
    ) -> str:
        if not approved:
            return (
                f"Research decision: no trade. {len(rejected)} candidate(s) were rejected "
                "or lacked enough evidence."
            )
        best = approved[0]
        return (
            "Research decision: continue with the top approved candidate only. "
            f"{best.signal.symbol} has confidence {best.signal.confidence:.2f}, "
            f"risk/reward {_format_optional(best.signal.risk_reward)}, and a valid spot BUY intent."
        )

    def _trader_report(self, best: ReviewedSignal | None) -> str:
        if best is None:
            return "Trader action: HOLD. No order intent exists."
        intent = best.risk.intent
        if not best.risk.approved or intent is None:
            return (
                f"Trader action: HOLD/REJECT for {best.signal.symbol}. "
                "No execution should be attempted without a risk-approved order intent."
            )
        return (
            f"Trader action: BUY {intent.symbol} spot only, quantity {intent.quantity:.8f}, "
            f"notional {intent.notional_usdt:.2f} USDT, entry {intent.entry_price:.4f}, "
            f"stop {_format_optional(intent.stop_loss)}, "
            f"take-profit {_format_optional(intent.take_profit)}."
        )

    def _risk_report(self, reviewed: tuple[ReviewedSignal, ...]) -> str:
        if not reviewed:
            return "Risk gate did not evaluate any candidates."
        lines = ["Hard risk gate results:"]
        for item in reviewed[:5]:
            status = "APPROVED" if item.risk.approved else "REJECTED"
            detail = item.risk.reason
            if item.risk.rejected_rules:
                detail = "; ".join(item.risk.rejected_rules)
            lines.append(f"- {item.signal.symbol}: {status} - {detail}")
        lines.append(
            "Live Binance orders remain disabled unless live mode and explicit confirmation are set."
        )
        return "\n".join(lines)

    def _portfolio_report(
        self,
        approved: tuple[ReviewedSignal, ...],
        ai_review: AITradeReview | None,
    ) -> str:
        if not approved:
            return (
                "Final decision: no trade. Capital preservation wins when the hard "
                "risk gate rejects all candidates."
            )
        best = approved[0]
        if ai_review:
            if ai_review.action != "BUY":
                return (
                    f"Final decision: HOLD despite {best.signal.symbol} passing risk, "
                    f"because AI review action is {ai_review.action}."
                )
            return (
                f"Final decision: {best.signal.symbol} remains eligible for the selected "
                f"execution mode. AI confidence {ai_review.confidence:.2f}; deterministic "
                "risk still controls position size and order permission."
            )
        return (
            f"Final decision: {best.signal.symbol} is the top deterministic candidate. "
            "AI review was not requested, so keep this as rule-based analysis."
        )


def _candidate_line(item: ReviewedSignal) -> str:
    signal = item.signal
    metrics = signal.metrics
    return (
        f"- {signal.symbol}: {signal.side} via {signal.strategy}, "
        f"confidence {signal.confidence:.2f}, entry {signal.entry_price:.4f}, "
        f"stop {_format_optional(signal.stop_loss)}, "
        f"take-profit {_format_optional(signal.take_profit)}, "
        f"24h change {_format_optional(metrics.get('change_pct_24h'))}%, "
        f"volume ratio {_format_optional(metrics.get('volume_ratio_20'))}, "
        f"quote volume {_format_optional(metrics.get('quote_volume_24h'))} USDT, "
        f"fusion score {_format_optional(metrics.get('fusion_score'))}, "
        f"fusion delta {_format_optional(metrics.get('fusion_delta'))}"
    )


def _reasons(item: ReviewedSignal) -> str:
    if not item.signal.reasons:
        return "No scanner reasons were recorded."
    return "\n".join(f"- {reason}" for reason in item.signal.reasons[:5])


def _format_optional(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.4f}"
