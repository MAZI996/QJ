"""Persistent decision journal for crypto workflow runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .engine import ReviewedSignal
from .models import AITradeReview
from .workflow_report import CryptoRoleReport, CryptoWorkflowReport


JOURNAL_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DecisionJournalWrite:
    run_id: str
    jsonl_path: Path
    json_path: Path
    markdown_path: Path


def write_workflow_report(
    report: CryptoWorkflowReport,
    state_dir: Path,
    context: dict[str, Any] | None = None,
) -> DecisionJournalWrite:
    """Write a structured JSONL entry plus per-run JSON and Markdown files."""

    run_id = uuid4().hex
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    report_dir = state_dir / "reports"
    state_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    entry = workflow_report_to_dict(
        report,
        run_id=run_id,
        created_at=created_at,
        context=context or {},
    )
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"workflow-{timestamp}-{run_id}.json"
    markdown_path = report_dir / f"workflow-{timestamp}-{run_id}.md"
    jsonl_path = state_dir / "decision_journal.jsonl"

    payload = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    json_path.write_text(payload + "\n", encoding="utf-8")
    markdown_path.write_text(report.render_markdown() + "\n", encoding="utf-8")
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(payload + "\n")

    return DecisionJournalWrite(
        run_id=run_id,
        jsonl_path=jsonl_path,
        json_path=json_path,
        markdown_path=markdown_path,
    )


def workflow_report_to_dict(
    report: CryptoWorkflowReport,
    run_id: str,
    created_at: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    approved = tuple(item for item in report.reviewed if item.risk.approved)
    return {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": created_at,
        "execution_mode": report.execution_mode,
        "context": _json_safe(context),
        "summary": {
            "candidates_scanned": len(report.reviewed),
            "risk_approved_candidates": len(approved),
            "final_action": _final_action(report, approved),
            "top_symbol": _top_symbol(report, approved),
            "ai_error": report.ai_error,
            "execution_requested": report.execution_requested,
            "execution_gate_reason": report.execution_gate_reason,
        },
        "ai_review": _ai_review(report.ai_review),
        "roles": [_role_report(item) for item in report.role_reports],
        "candidates": [_reviewed_signal(item) for item in report.reviewed],
    }


def _final_action(
    report: CryptoWorkflowReport,
    approved: tuple[ReviewedSignal, ...],
) -> str:
    if not approved:
        return "REJECT"
    if report.execution_requested and report.execution_gate_reason:
        return "HOLD"
    if report.ai_review and report.ai_review.action != "BUY":
        return "HOLD"
    return "BUY"


def _top_symbol(
    report: CryptoWorkflowReport,
    approved: tuple[ReviewedSignal, ...],
) -> str | None:
    if report.ai_review and report.ai_review.symbol:
        return report.ai_review.symbol
    return approved[0].signal.symbol if approved else None


def _ai_review(review: AITradeReview | None) -> dict[str, Any] | None:
    if review is None:
        return None
    return {
        "action": review.action,
        "symbol": review.symbol,
        "confidence": review.confidence,
        "summary": review.summary,
        "main_risk": review.main_risk,
        "invalidation": review.invalidation,
        "role_notes": review.role_notes,
        "model": review.model,
        "router": review.router,
        "raw_response": review.raw_response,
    }


def _role_report(report: CryptoRoleReport) -> dict[str, str]:
    return {"role": report.role, "content": report.content}


def _reviewed_signal(item: ReviewedSignal) -> dict[str, Any]:
    signal = item.signal
    risk = item.risk
    intent = risk.intent
    execution = item.execution
    rules = item.rules
    return {
        "signal": {
            "symbol": signal.symbol,
            "side": signal.side,
            "confidence": signal.confidence,
            "entry_price": signal.entry_price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "timeframe": signal.timeframe,
            "strategy": signal.strategy,
            "reasons": list(signal.reasons),
            "metrics": signal.metrics,
            "risk_reward": signal.risk_reward,
        },
        "risk": {
            "approved": risk.approved,
            "reason": risk.reason,
            "rejected_rules": list(risk.rejected_rules),
            "intent": (
                {
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "quantity": intent.quantity,
                    "notional_usdt": intent.notional_usdt,
                    "entry_price": intent.entry_price,
                    "stop_loss": intent.stop_loss,
                    "take_profit": intent.take_profit,
                    "reason": intent.reason,
                    "reduce_only": intent.reduce_only,
                }
                if intent
                else None
            ),
        },
        "rules": (
            {
                "symbol": rules.symbol,
                "base_asset": rules.base_asset,
                "quote_asset": rules.quote_asset,
                "min_qty": rules.min_qty,
                "step_size": rules.step_size,
                "min_notional": rules.min_notional,
            }
            if rules
            else None
        ),
        "execution": (
            {
                "mode": execution.mode,
                "accepted": execution.accepted,
                "symbol": execution.symbol,
                "side": execution.side,
                "quantity": execution.quantity,
                "message": execution.message,
                "exchange_payload": execution.exchange_payload,
            }
            if execution
            else None
        ),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value
