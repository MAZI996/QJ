from __future__ import annotations

import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

import tradingagents.crypto.llm_router as router_module
from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.llm_router import (
    CryptoLLMRouterNotReady,
    HermesCLIReviewLLM,
    create_crypto_review_llm,
)


def test_router_creates_hermes_cli_adapter():
    config = replace(CryptoTradingConfig(), ai_router="hermes_cli")

    adapter = create_crypto_review_llm(config)

    assert isinstance(adapter, HermesCLIReviewLLM)


def test_hermes_cli_uses_single_query_without_tools(monkeypatch):
    captured = {}
    config = replace(
        CryptoTradingConfig(),
        ai_router="hermes_cli",
        ai_model="provider/model",
        hermes_cli_command="hermes",
        hermes_cli_timeout_seconds=75,
    )
    monkeypatch.setattr(router_module.shutil, "which", lambda _command: "/usr/bin/hermes")

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout="\x1b[32msession_id: abc\x1b[0m\nAction: HOLD\nSymbol: NONE\n",
            stderr="",
        )

    monkeypatch.setattr(router_module.subprocess, "run", fake_run)

    response = HermesCLIReviewLLM(config).invoke("review this")

    assert response.content == "Action: HOLD\nSymbol: NONE"
    assert captured["args"] == [
        "hermes",
        "chat",
        "-Q",
        "--max-turns",
        "1",
        "--toolsets",
        "",
        "--source",
        "tradingagents",
        "--model",
        "provider/model",
        "--query",
        "review this",
    ]
    assert captured["kwargs"]["timeout"] == 75
    assert captured["kwargs"]["check"] is False
    assert captured["kwargs"]["env"]["NO_COLOR"] == "1"


def test_hermes_cli_uses_configured_default_model_when_ai_model_is_empty(monkeypatch):
    captured = {}
    config = replace(CryptoTradingConfig(), ai_router="hermes_cli", ai_model="")
    monkeypatch.setattr(router_module.shutil, "which", lambda _command: "/usr/bin/hermes")

    def fake_run(args, **_kwargs):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout="Action: HOLD\n", stderr="")

    monkeypatch.setattr(router_module.subprocess, "run", fake_run)

    HermesCLIReviewLLM(config).invoke("review")

    assert "--model" not in captured["args"]


def test_hermes_cli_missing_executable_is_blocked(monkeypatch):
    config = replace(CryptoTradingConfig(), ai_router="hermes_cli")
    monkeypatch.setattr(router_module.shutil, "which", lambda _command: None)

    with pytest.raises(CryptoLLMRouterNotReady, match="was not found"):
        HermesCLIReviewLLM(config).invoke("review")


def test_hermes_cli_timeout_is_blocked(monkeypatch):
    config = replace(
        CryptoTradingConfig(),
        ai_router="hermes_cli",
        hermes_cli_timeout_seconds=12,
    )
    monkeypatch.setattr(router_module.shutil, "which", lambda _command: "/usr/bin/hermes")

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="hermes", timeout=12)

    monkeypatch.setattr(router_module.subprocess, "run", fake_run)

    with pytest.raises(CryptoLLMRouterNotReady, match="timed out after 12 seconds"):
        HermesCLIReviewLLM(config).invoke("review")


def test_hermes_cli_nonzero_exit_is_blocked(monkeypatch):
    config = replace(CryptoTradingConfig(), ai_router="hermes_cli")
    monkeypatch.setattr(router_module.shutil, "which", lambda _command: "/usr/bin/hermes")
    monkeypatch.setattr(
        router_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="provider unavailable\n",
        ),
    )

    with pytest.raises(CryptoLLMRouterNotReady, match="provider unavailable"):
        HermesCLIReviewLLM(config).invoke("review")


def test_hermes_cli_healthcheck_reports_executable(monkeypatch):
    config = replace(CryptoTradingConfig(), ai_router="hermes_cli")
    monkeypatch.setattr(router_module.shutil, "which", lambda _command: "/usr/bin/hermes")

    status = HermesCLIReviewLLM(config).healthcheck()

    assert status.ready is True
    assert status.router == "hermes_cli"
    assert status.base_url == "/usr/bin/hermes"
