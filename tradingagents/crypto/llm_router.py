"""LLM router boundary for crypto decision review."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import CryptoTradingConfig


class CryptoLLMRouterNotReady(RuntimeError):
    """Raised when a configured LLM router has no adapter yet."""


@dataclass(frozen=True)
class HermesRouterStatus:
    ready: bool
    router: str
    model: str
    base_url: str
    message: str


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

    if router in {"hermes_cli", "hermes-cli"}:
        return HermesCLIReviewLLM(config)

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

    def healthcheck(self) -> HermesRouterStatus:
        if not self.config.hermes_base_url:
            return HermesRouterStatus(False, "hermes", self.config.ai_model, "", "missing base url")
        if not self.config.ai_model:
            return HermesRouterStatus(
                False,
                "hermes",
                "",
                self.config.hermes_base_url,
                "missing model",
            )
        url = f"{self.config.hermes_base_url.rstrip('/')}/models"
        request = urllib.request.Request(url, method="GET", headers=self._headers())
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.config.hermes_timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            return HermesRouterStatus(
                False,
                "hermes",
                self.config.ai_model,
                self.config.hermes_base_url,
                str(exc),
            )
        available = _models_from_payload(payload)
        if available and self.config.ai_model not in available:
            return HermesRouterStatus(
                False,
                "hermes",
                self.config.ai_model,
                self.config.hermes_base_url,
                f"model not listed by Hermes: {self.config.ai_model}",
            )
        return HermesRouterStatus(
            True,
            "hermes",
            self.config.ai_model,
            self.config.hermes_base_url,
            "Hermes router is reachable.",
        )

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


class HermesCLIReviewLLM:
    """Invoke the local Hermes CLI in single-query, tool-disabled mode."""

    def __init__(self, config: CryptoTradingConfig):
        self.config = config
        self.model = config.ai_model

    def invoke(self, prompt: str):
        command = self.config.hermes_cli_command.strip() or "hermes"
        if shutil.which(command) is None:
            raise CryptoLLMRouterNotReady(
                f"Hermes CLI executable was not found: {command}"
            )
        args = [
            command,
            "chat",
            "-Q",
            "--max-turns",
            "1",
            "--toolsets",
            "",
            "--source",
            "tradingagents",
        ]
        if self.config.ai_model:
            args.extend(("--model", self.config.ai_model))
        args.extend(("--query", prompt))
        env = dict(os.environ)
        env.update({"NO_COLOR": "1", "TERM": "dumb"})
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self.config.hermes_cli_timeout_seconds,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise CryptoLLMRouterNotReady(
                "Hermes CLI timed out after "
                f"{self.config.hermes_cli_timeout_seconds} seconds."
            ) from exc
        except OSError as exc:
            raise CryptoLLMRouterNotReady(
                f"Hermes CLI failed to start: {exc}"
            ) from exc

        if completed.returncode != 0:
            detail = _compact_cli_error(completed.stderr or completed.stdout)
            raise CryptoLLMRouterNotReady(
                f"Hermes CLI exited with code {completed.returncode}: {detail}"
            )
        content = _clean_hermes_cli_output(completed.stdout)
        if not content:
            raise CryptoLLMRouterNotReady("Hermes CLI returned an empty response.")
        return _LLMResponse(content=content)

    def healthcheck(self) -> HermesRouterStatus:
        command = self.config.hermes_cli_command.strip() or "hermes"
        resolved = shutil.which(command)
        if resolved is None:
            return HermesRouterStatus(
                False,
                "hermes_cli",
                self.config.ai_model,
                command,
                f"Hermes CLI executable was not found: {command}",
            )
        return HermesRouterStatus(
            True,
            "hermes_cli",
            self.config.ai_model,
            resolved,
            "Hermes CLI is available for single-query review.",
        )


class _LLMResponse:
    def __init__(self, content: str):
        self.content = content


_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _clean_hermes_cli_output(value: str) -> str:
    clean = _ANSI_ESCAPE.sub("", value).replace("\r", "")
    lines = [
        line.strip()
        for line in clean.splitlines()
        if line.strip() and not line.strip().lower().startswith("session_id:")
    ]
    return "\n".join(lines).strip()


def _compact_cli_error(value: str) -> str:
    clean = _clean_hermes_cli_output(value)
    return clean[:500] or "no error output"


def _models_from_payload(payload: dict[str, Any]) -> set[str]:
    rows = payload.get("data") or payload.get("models") or []
    models: set[str] = set()
    if isinstance(rows, list):
        for item in rows:
            if isinstance(item, str):
                models.add(item)
            elif isinstance(item, dict) and isinstance(item.get("id"), str):
                models.add(item["id"])
    return models
