"""Autopilot runner for repeated crypto workflow cycles."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .circuit_breaker import DailyLossCircuitBreaker
from .config import CryptoTradingConfig
from .decision_journal import DecisionJournalWrite, write_workflow_report
from .engine import CryptoTradingEngine
from .models import ExecutionMode
from .position_guardian import PositionGuardian, PositionGuardResult
from .stream_status import StreamStatusSummary, summarize_stream_status
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
    position_guard: PositionGuardResult | None = None
    stream_status: StreamStatusSummary | None = None

    @property
    def final_action(self) -> str:
        if self.position_guard and self.position_guard.close_signals:
            return "CLOSE" if self.position_guard.accepted_closes else "CLOSE_SIGNAL"
        if self.report is None:
            return "STOP"
        approved = self.report.approved
        if not approved:
            return "REJECT"
        if self.report.execution_requested and self.report.execution_gate_reason:
            return "HOLD"
        if self.report.ai_review and self.report.ai_review.action != "BUY":
            return "HOLD"
        return "BUY"

    @property
    def top_symbol(self) -> str:
        if self.report is None or not self.report.approved:
            return "-"
        if self.report.ai_review and self.report.ai_review.symbol:
            return self.report.ai_review.symbol
        return self.report.approved[0].signal.symbol

    @property
    def execution_message(self) -> str:
        if self.position_guard and self.position_guard.close_attempts:
            return " | ".join(
                item.execution.message
                for item in self.position_guard.close_attempts
                if item.execution is not None
            )
        if self.position_guard and self.position_guard.close_signals:
            return self.position_guard.summary
        if self.report is None:
            return self.reason
        if self.report.execution_gate_reason:
            return self.report.execution_gate_reason
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
        guard_positions: bool = True,
        auto_close: bool = False,
        require_fresh_stream: bool = True,
        stream_max_age_seconds: int = 600,
        stream_archive_path: Path | None = None,
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
                guard_positions=guard_positions,
                auto_close=auto_close,
                require_fresh_stream=require_fresh_stream,
                stream_max_age_seconds=stream_max_age_seconds,
                stream_archive_path=stream_archive_path,
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
        guard_positions: bool = True,
        auto_close: bool = False,
        require_fresh_stream: bool = True,
        stream_max_age_seconds: int = 600,
        stream_archive_path: Path | None = None,
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
        engine: CryptoTradingEngine | None = None
        position_guard = None
        if guard_positions:
            engine = CryptoTradingEngine(self.config)
            position_guard = PositionGuardian(engine.client, self.config).run(
                mode=mode,
                live_confirmation=live_confirmation,
                execute=auto_close,
            )
        stream_status = None
        if require_fresh_stream:
            stream_status = summarize_stream_status(
                self.config,
                symbols=symbols or self.config.symbols,
                archive_path=stream_archive_path,
                max_age_seconds=stream_max_age_seconds,
            )
            if not stream_status.fresh:
                return AutoPilotCycleResult(
                    cycle=cycle,
                    report=None,
                    saved=None,
                    stopped=True,
                    reason=_stream_stop_reason(stream_status),
                    position_guard=position_guard,
                    stream_status=stream_status,
                )
        if engine is None:
            engine = CryptoTradingEngine(self.config)
        skip_entries = (
            position_guard is not None
            and bool(position_guard.close_signals)
            and self.config.position_guardian_skip_entries_after_close
        )
        report = CryptoTradingAgentsWorkflow(config=self.config, engine=engine).run(
            symbols=symbols,
            execute_top=execute_top and not skip_entries,
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
                "execute_top_skipped_by_position_guardian": skip_entries,
                "auto_close_requested": auto_close,
                "position_guardian": (
                    position_guard.to_dict() if position_guard is not None else None
                ),
                "stream_freshness": (
                    _stream_context(stream_status) if stream_status is not None else None
                ),
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
            position_guard=position_guard,
            stream_status=stream_status,
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


def _stream_stop_reason(summary: StreamStatusSummary) -> str:
    missing = ", ".join(
        f"{row.symbol}:{row.channel}" for row in summary.missing_or_stale[:5]
    )
    suffix = f"; first missing/stale={missing}" if missing else ""
    provider = summary.provider.strip().lower()
    command = "crypto-okx-stream" if provider == "okx" else "crypto-hyperliquid-stream"
    return (
        f"{provider.upper()} WebSocket stream is stale or missing; "
        f"run {command} before autopilot entries"
        f"{suffix}."
    )


def _stream_context(summary: StreamStatusSummary) -> dict[str, object]:
    return {
        "fresh": summary.fresh,
        "events_read": summary.events_read,
        "max_age_seconds": summary.max_age_seconds,
        "latest_event_at": summary.latest_event_at,
        "provider": summary.provider,
        "archive_paths": [str(path) for path in summary.archive_paths],
        "missing_or_stale": [
            {
                "symbol": row.symbol,
                "channel": row.channel,
                "age_seconds": row.age_seconds,
                "message": row.message,
            }
            for row in summary.missing_or_stale
        ],
    }
