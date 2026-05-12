"""Paper execution journal for strategy dry-runs."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .config import CryptoTradingConfig
from .models import OrderIntent, OrderResult


class PaperBroker:
    def __init__(self, config: CryptoTradingConfig):
        self.config = config

    def execute(self, intent: OrderIntent) -> OrderResult:
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        path = Path(self.config.state_dir) / "paper_orders.jsonl"
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "intent": asdict(intent),
            "fill_price": intent.entry_price,
            "status": "FILLED_SIMULATED",
        }
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

        return OrderResult(
            mode="paper",
            accepted=True,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            message=f"纸面成交已记录：{path}",
            exchange_payload=payload,
        )
