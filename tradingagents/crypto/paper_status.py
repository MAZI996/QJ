"""Read-only status summary for paper validation runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CryptoTradingConfig
from .stream_status import summarize_stream_status


@dataclass(frozen=True)
class PaperStreamEvidence:
    archive_fresh: bool
    archive_events: int
    archive_latest_event_at: str
    archive_paths: tuple[Path, ...]
    required_channels: int
    fresh_channels: int
    missing_or_stale: tuple[str, ...]
    autopilot_stream_cycles: int
    autopilot_fresh_stream_cycles: int
    autopilot_stale_stream_cycles: int

    @property
    def fresh_cycle_ratio(self) -> float:
        if self.autopilot_stream_cycles <= 0:
            return 0.0
        return self.autopilot_fresh_stream_cycles / self.autopilot_stream_cycles


@dataclass(frozen=True)
class PaperStatusSummary:
    decision_runs: int
    paper_orders: int
    last_action: str
    last_top_symbol: str
    last_run_at: str
    last_report_path: Path | None
    queue_ready_count: int
    queue_top_command: str
    queue_top_note: str
    stream_evidence: PaperStreamEvidence


def summarize_paper_status(
    config: CryptoTradingConfig,
    *,
    stream_max_age_seconds: int = 600,
) -> PaperStatusSummary:
    state_dir = Path(config.state_dir)
    decision_journal = state_dir / "decision_journal.jsonl"
    paper_orders = state_dir / "paper_orders.jsonl"
    queue_json = state_dir / "paper_queue.json"

    decision_entries = _read_jsonl(decision_journal)
    paper_order_entries = _read_jsonl(paper_orders)
    last = decision_entries[-1] if decision_entries else {}
    summary = last.get("summary", {}) if isinstance(last, dict) else {}
    run_id = str(last.get("run_id", "")) if isinstance(last, dict) else ""
    created_at = str(last.get("created_at", "")) if isinstance(last, dict) else ""

    queue = _read_json(queue_json)
    items = queue.get("items", []) if isinstance(queue, dict) else []
    top = items[0] if items and isinstance(items[0], dict) else {}
    stream_evidence = _stream_evidence(
        config,
        decision_entries=decision_entries,
        stream_max_age_seconds=stream_max_age_seconds,
    )

    return PaperStatusSummary(
        decision_runs=len(decision_entries),
        paper_orders=len(paper_order_entries),
        last_action=str(summary.get("final_action", "-")),
        last_top_symbol=str(summary.get("top_symbol") or "-"),
        last_run_at=created_at or "-",
        last_report_path=_last_report_path(state_dir, created_at, run_id),
        queue_ready_count=int(queue.get("ready_count", 0)) if isinstance(queue, dict) else 0,
        queue_top_command=str(top.get("command", "")),
        queue_top_note=str(top.get("review_note", "")),
        stream_evidence=stream_evidence,
    )


def _stream_evidence(
    config: CryptoTradingConfig,
    *,
    decision_entries: list[dict[str, Any]],
    stream_max_age_seconds: int,
) -> PaperStreamEvidence:
    stream_status = summarize_stream_status(
        config,
        max_age_seconds=stream_max_age_seconds,
    )
    cycles = [_stream_context(entry) for entry in decision_entries]
    cycles = [cycle for cycle in cycles if cycle is not None]
    fresh_cycles = sum(1 for cycle in cycles if bool(cycle.get("fresh")))
    stale_cycles = len(cycles) - fresh_cycles
    return PaperStreamEvidence(
        archive_fresh=stream_status.fresh,
        archive_events=stream_status.events_read,
        archive_latest_event_at=stream_status.latest_event_at or "-",
        archive_paths=stream_status.archive_paths,
        required_channels=len(stream_status.rows),
        fresh_channels=sum(1 for row in stream_status.rows if row.fresh),
        missing_or_stale=tuple(
            f"{row.symbol}:{row.channel}" for row in stream_status.missing_or_stale
        ),
        autopilot_stream_cycles=len(cycles),
        autopilot_fresh_stream_cycles=fresh_cycles,
        autopilot_stale_stream_cycles=stale_cycles,
    )


def _stream_context(entry: dict[str, Any]) -> dict[str, Any] | None:
    context = entry.get("context")
    if not isinstance(context, dict):
        return None
    if context.get("command") != "crypto-autopilot":
        return None
    stream = context.get("stream_freshness")
    return stream if isinstance(stream, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _last_report_path(state_dir: Path, created_at: str, run_id: str) -> Path | None:
    if not created_at or not run_id:
        return None
    report_dir = state_dir / "reports"
    if not report_dir.exists():
        return None
    matches = sorted(report_dir.glob(f"workflow-*-{run_id}.md"))
    return matches[-1] if matches else None
