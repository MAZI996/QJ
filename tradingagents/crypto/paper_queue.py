"""Paper-trading entry queue built from deterministic backtest candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backtest import BacktestCandidateRules, BacktestSweepReport, BacktestSweepResult


@dataclass(frozen=True)
class PaperQueueItem:
    rank: int
    symbols: tuple[str, ...]
    interval: str
    lookback_limit: int
    max_holding_bars: int
    bars_requested: int
    trades: int
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float
    max_consecutive_losses: int
    total_pnl_usdt: float
    risk_adjusted_score: float
    command: str
    review_note: str


@dataclass(frozen=True)
class PaperQueuePlan:
    created_at: str
    candidate_rules: BacktestCandidateRules
    items: tuple[PaperQueueItem, ...]

    @property
    def ready_count(self) -> int:
        return len(self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "candidate_rules": asdict(self.candidate_rules),
            "ready_count": self.ready_count,
            "items": [asdict(item) for item in self.items],
            "safety": {
                "mode": "paper",
                "places_live_orders": False,
                "requires_backtest_candidate": True,
            },
        }

    def render_markdown(self) -> str:
        lines = [
            "# Hyperliquid Paper Entry Queue",
            "",
            f"- Created at: {self.created_at}",
            f"- Ready candidates: {self.ready_count}",
            "- Mode: paper only",
            "- Live orders: disabled",
            "",
            "| Rank | Symbols | Interval | Lookback | Hold bars | Trades | Win rate | Return | Max DD | Loss streak | Score |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for item in self.items:
            lines.append(
                "| {rank} | {symbols} | {interval} | {lookback} | {hold} | {trades} | "
                "{win_rate:.2%} | {ret:.2f}% | {drawdown:.2f}% | {losses} | {score:.4f} |".format(
                    rank=item.rank,
                    symbols=",".join(item.symbols),
                    interval=item.interval,
                    lookback=item.lookback_limit,
                    hold=item.max_holding_bars,
                    trades=item.trades,
                    win_rate=item.win_rate,
                    ret=item.total_return_pct,
                    drawdown=item.max_drawdown_pct,
                    losses=item.max_consecutive_losses,
                    score=item.risk_adjusted_score,
                )
            )
        lines.extend(["", "## Commands", ""])
        if not self.items:
            lines.append("No parameter set passed the paper-candidate filters.")
        for item in self.items:
            lines.extend(
                [
                    f"### Rank {item.rank}",
                    "",
                    "```powershell",
                    item.command,
                    "```",
                    "",
                    item.review_note,
                    "",
                ]
            )
        lines.extend(
            [
                "## Safety",
                "",
                "- This queue is generated only from deterministic backtest candidates.",
                "- Each command uses `--mode paper`; it is not a live-order approval.",
                "- The execution router and risk manager still run before any paper fill is recorded.",
            ]
        )
        return "\n".join(lines).strip() + "\n"


def build_paper_queue_plan(
    sweep_report: BacktestSweepReport,
    rules: BacktestCandidateRules,
    top: int = 3,
    interval_seconds: int = 300,
    cycles: int = 0,
    ai_review: bool = False,
) -> PaperQueuePlan:
    items: list[PaperQueueItem] = []
    seen_execution_keys: set[tuple[tuple[str, ...], str, int, bool, bool]] = set()
    for result in sweep_report.candidate_results(rules):
        execution_key = (
            result.report.symbols,
            result.case.interval,
            result.case.lookback_limit,
            result.case.fusion_enabled,
            result.case.lana_enabled,
        )
        if execution_key in seen_execution_keys:
            continue
        seen_execution_keys.add(execution_key)
        items.append(
            _queue_item_from_result(
                rank=len(items) + 1,
                result=result,
                interval_seconds=interval_seconds,
                cycles=cycles,
                ai_review=ai_review,
            )
        )
        if len(items) >= top:
            break
    return PaperQueuePlan(
        created_at=datetime.now(timezone.utc).isoformat(),
        candidate_rules=rules,
        items=tuple(items),
    )


def paper_queue_output_paths(base_path: Path) -> tuple[Path, Path]:
    return base_path.with_suffix(".json"), base_path.with_suffix(".md")


def _queue_item_from_result(
    rank: int,
    result: BacktestSweepResult,
    interval_seconds: int,
    cycles: int,
    ai_review: bool,
) -> PaperQueueItem:
    report = result.report
    case = result.case
    symbols = ",".join(report.symbols)
    command = (
        "python -m cli.main crypto-autopilot "
        f"--symbols {symbols} "
        f"--interval {case.interval} "
        f"--lookback {case.lookback_limit} "
        "--mode paper --execute-top --auto-close "
        f"--cycles {cycles} "
        f"--interval-seconds {interval_seconds} "
        f"{'--ai-review ' if ai_review else ''}"
        f"{'--fusion' if case.fusion_enabled else '--no-fusion'} "
        f"{'--lana' if case.lana_enabled else '--no-lana'}"
    ).strip()
    return PaperQueueItem(
        rank=rank,
        symbols=report.symbols,
        interval=case.interval,
        lookback_limit=case.lookback_limit,
        max_holding_bars=case.max_holding_bars,
        bars_requested=report.bars_requested,
        trades=len(report.trades),
        win_rate=report.win_rate,
        total_return_pct=report.total_return_pct,
        max_drawdown_pct=report.max_drawdown_pct,
        max_consecutive_losses=report.max_consecutive_losses,
        total_pnl_usdt=report.total_pnl_usdt,
        risk_adjusted_score=result.risk_adjusted_score,
        command=command,
        review_note=(
            "Review this paper candidate after the configured holding window. "
            f"Historical max holding was {case.max_holding_bars} bars; do not promote "
            "without paper journal evidence."
        ),
    )
