"""LLM router boundary for crypto decision review."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .config import CryptoTradingConfig


class CryptoLLMRouterNotReady(RuntimeError):
    """Raised when a configured LLM router has no adapter yet."""


def create_crypto_review_llm(config: CryptoTradingConfig):
    router = config.ai_router.strip().lower()
    if router in {"", "tradingagents", "default"}:
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.llm_clients.factory import create_llm_client

        model = config.ai_model or DEFAULT_CONFIG["quick_think_llm"]
        return create_llm_client(
            provider=DEFAULT_CONFIG["llm_provider"],
            model=model,
            base_url=DEFAULT_CONFIG.get("backend_url"),
        )

    if router == "hermes":
        if not config.hermes_base_url:
            raise CryptoLLMRouterNotReady(
                "Hermes router is selected, but TRADINGAGENTS_CRYPTO_HERMES_BASE_URL is empty."
            )
        if not config.ai_model:
            raise CryptoLLMRouterNotReady(
                "Hermes router is selected, but TRADINGAGENTS_CRYPTO_AI_MODEL is empty."
            )
        return HermesReviewLLM(config)

    raise CryptoLLMRouterNotReady(f"Unsupported crypto AI router: {config.ai_router}")


class HermesReviewLLM:
    """Small OpenAI-compatible Hermes adapter.

    This assumes Hermes exposes a chat-completions style endpoint. If the real
    Hermes service uses a different contract, only this adapter should change.
    """

    def __init__(self, config: CryptoTradingConfig):
        self.config = config
        self.model = config.ai_model

    def invoke(self, prompt: str):
        payload = {
            "model": self.config.ai_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a crypto trading review model. Return concise, "
                        "structured Chinese output and never bypass risk controls."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        data = json.dumps(payload).encode("utf-8")
        url = self._chat_url()
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.config.hermes_timeout_seconds,
            ) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise CryptoLLMRouterNotReady(f"Hermes HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise CryptoLLMRouterNotReady(f"Hermes request failed: {exc.reason}") from exc

        content = self._extract_content(json.loads(raw))
        return _LLMResponse(content=content)

    def _chat_url(self) -> str:
        base = self.config.hermes_base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.hermes_api_key:
            headers["Authorization"] = f"Bearer {self.config.hermes_api_key}"
        return headers

    @staticmethod
    def _extract_content(payload: dict[str, Any]) -> str:
        choices = payload.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                return content
        if isinstance(payload.get("content"), str):
            return payload["content"]
        return json.dumps(payload, ensure_ascii=False)


class _LLMResponse:
    def __init__(self, content: str):
        self.content = content
