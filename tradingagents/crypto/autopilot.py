"""Autopilot runner for repeated crypto workflow cycles."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .circuit_breaker import DailyLossCircuitBreaker
from .config import CryptoTradingConfig
from .decision_journal import DecisionJournalWrite, write_workflow_report
from .models import ExecutionMode
from .workflow_report import CryptoTradingAgentsWorkflow, CryptoWorkflowReport


class CryptoAutoPilotSafetyError(RuntimeError):
    """Raised when an autopilot run asks for unsafe execution settings."""


@dataclass(frozen=True)
class AutoPilotCycleResult:
    cycle: int
    report: CryptoWorkflowReport | None
    saved: DecisionJournalWrite | None
    stopped: bool
    reason: str

    @property
    def final_action(self) -> str:
        if self.report is None:
            return "STOP"
        approved = self.report.approved
        if not approved:
            return "REJECT"
        if self.report.ai_review and self.report.ai_review.action != "BUY":
            return "HOLD"
        return "BUY"

    @property
    def top_symbol(self) -> str:
        if self.report is None or not self.report.approved:
            return "-"
        return self.report.approved[0].signal.symbol

    @property
    def execution_message(self) -> str:
        if self.report is None:
            return self.reason
        for item in self.report.reviewed:
            if item.execution:
                return item.execution.message
        return "No execution attempted."


class CryptoAutoPilot:
    """Run TradingAgents crypto workflow cycles until stopped or exhausted."""

    def __init__(self, config: CryptoTradingConfig | None = None):
        self.config = config or CryptoTradingConfig.from_env()

    def run_loop(
        self,
        symbols: tuple[str, ...] | None = None,
        interval_seconds: int = 300,
        cycles: int = 1,
        execution_mode: ExecutionMode | None = None,
        execute_top: bool = False,
        live_confirmation: str = "",
        ai_review_enabled: bool = False,
        journal_dir: Path | None = None,
        allow_live: bool = False,
    ) -> Iterator[AutoPilotCycleResult]:
        mode = execution_mode or self.config.execution_mode
        self._validate_execution(mode, allow_live)

        cycle = 0
        while cycles <= 0 or cycle < cycles:
            cycle += 1
            result = self.run_once(
                symbols=symbols,
                cycle=cycle,
                execution_mode=mode,
                execute_top=execute_top,
                live_confirmation=live_confirmation,
                ai_review_enabled=ai_review_enabled,
                journal_dir=journal_dir,
            )
            yield result
            if result.stopped or (cycles > 0 and cycle >= cycles):
                break
            time.sleep(max(1, interval_seconds))

    def run_once(
        self,
        symbols: tuple[str, ...] | None = None,
        cycle: int = 1,
        execution_mode: ExecutionMode | None = None,
        execute_top: bool = False,
        live_confirmation: str = "",
        ai_review_enabled: bool = False,
        journal_dir: Path | None = None,
    ) -> AutoPilotCycleResult:
        stop_reason = self._emergency_stop_reason()
        if stop_reason:
            return AutoPilotCycleResult(
                cycle=cycle,
                report=None,
                saved=None,
                stopped=True,
                reason=stop_reason,
            )
        breaker = DailyLossCircuitBreaker(self.config).evaluate()
        if breaker.blocked:
            return AutoPilotCycleResult(
                cycle=cycle,
                report=None,
                saved=None,
                stopped=True,
                reason=breaker.reason,
            )

        mode = execution_mode or self.config.execution_mode
        report = CryptoTradingAgentsWorkflow(config=self.config).run(
            symbols=symbols,
            execute_top=execute_top,
            execution_mode=mode,
            live_confirmation=live_confirmation,
            ai_review_enabled=ai_review_enabled,
        )
        saved = write_workflow_report(
            report,
            state_dir=journal_dir or self.config.state_dir,
            context={
                "command": "crypto-autopilot",
                "cycle": cycle,
                "symbols": symbols or self.config.symbols,
                "interval": self.config.interval,
                "execution_mode": mode,
                "execute_top": execute_top,
                "ai_review_requested": ai_review_enabled,
                "strategy_fusion_enabled": self.config.strategy_fusion_enabled,
            },
        )
        return AutoPilotCycleResult(
            cycle=cycle,
            report=report,
            saved=saved,
            stopped=False,
            reason="cycle completed",
        )

    def _validate_execution(self, mode: ExecutionMode, allow_live: bool) -> None:
        if mode == "live" and not allow_live:
            raise CryptoAutoPilotSafetyError(
                "Autopilot live mode requires --allow-live in addition to "
                "live config and confirmation."
            )

    def _emergency_stop_reason(self) -> str:
        stop_file = self.config.emergency_stop_file
        if stop_file and stop_file.exists():
            return f"Emergency stop file exists: {stop_file}"
        return ""
