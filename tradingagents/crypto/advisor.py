"""Optional LLM review for crypto opportunities.

The advisor is deliberately read-only. It can explain or challenge a signal,
but execution still has to pass the deterministic risk gate.
"""

from __future__ import annotations

from .engine import ReviewedSignal
from .models import AITradeReview


class CryptoAIAdvisor:
    def __init__(self, llm, router: str = "tradingagents", model: str = ""):
        self.llm = llm
        self.router = router
        self.model = model

    def review(self, reviewed: list[ReviewedSignal]) -> str:
        return self.review_structured(reviewed).raw_response

    def review_structured(self, reviewed: list[ReviewedSignal]) -> AITradeReview:
        top = reviewed[:5]
        blocks = []
        for item in top:
            signal = item.signal
            risk = item.risk
            blocks.append(
                "\n".join(
                    [
                        f"Symbol: {signal.symbol}",
                        f"Side: {signal.side}",
                        f"Confidence: {signal.confidence:.2f}",
                        f"Entry: {signal.entry_price}",
                        f"Stop: {signal.stop_loss}",
                        f"Take profit: {signal.take_profit}",
                        f"Risk approved: {risk.approved}",
                        f"Reasons: {'; '.join(signal.reasons)}",
                        f"Rejected rules: {'; '.join(risk.rejected_rules)}",
                    ]
                )
            )

        prompt = f"""
You are reviewing crypto spot trading candidates for a personal Binance account.
Do not promise profit. Do not recommend leverage. Do not bypass risk controls.

Return Chinese output with exactly these labels:
Action: BUY, HOLD, or REJECT
Confidence: a decimal between 0 and 1
Summary: best candidate, or say none
Main risk: the most important risk
Invalidation: market condition that invalidates the setup

Never suggest leverage. Never bypass deterministic risk controls.

Candidates:
{chr(10).join(blocks)}
"""
        response = self.llm.invoke(prompt)
        text = getattr(response, "content", str(response))
        return _parse_text_review(text, router=self.router, model=self.model)


def _parse_text_review(text: str, router: str, model: str) -> AITradeReview:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip().lower()] = value.strip()

    action = fields.get("action", "HOLD").upper()
    if action not in {"BUY", "HOLD", "REJECT"}:
        action = "HOLD"

    try:
        confidence = float(fields.get("confidence", "0"))
    except ValueError:
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))

    return AITradeReview(
        action=action,  # type: ignore[arg-type]
        confidence=confidence,
        summary=fields.get("summary", ""),
        main_risk=fields.get("main risk", ""),
        invalidation=fields.get("invalidation", ""),
        model=model,
        router=router,
        raw_response=text,
    )
