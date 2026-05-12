"""Optional LLM review for crypto opportunities.

The advisor is deliberately read-only. It can explain or challenge a signal,
but execution still has to pass the deterministic risk gate.
"""

from __future__ import annotations

from .engine import ReviewedSignal


class CryptoAIAdvisor:
    def __init__(self, llm):
        self.llm = llm

    def review(self, reviewed: list[ReviewedSignal]) -> str:
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

Return Chinese output with:
1. Best candidate, or say none.
2. Main risk.
3. Whether the deterministic risk gate seems too loose or too strict.
4. What market condition would invalidate the setup.

Candidates:
{chr(10).join(blocks)}
"""
        response = self.llm.invoke(prompt)
        return getattr(response, "content", str(response))
