"""Optional LLM review for crypto opportunities.

The advisor is deliberately read-only. It can explain or challenge a signal,
but execution still has to pass the deterministic risk gate.
"""

from __future__ import annotations

from .agent_workflow import build_tradingagents_crypto_prompt
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
        prompt = build_tradingagents_crypto_prompt(reviewed)
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
        role_notes=fields.get("role notes", ""),
        model=model,
        router=router,
        raw_response=text,
    )
