from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "hermes"
    / "okx_analysis_watchdog.sh"
)


def test_hermes_watchdog_is_analysis_only_and_credential_free():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "TRADINGAGENTS_CRYPTO_EXECUTION_MODE=analysis" in text
    assert "TRADINGAGENTS_CRYPTO_OKX_DEMO_EXECUTION_ENABLED=false" in text
    assert "TRADINGAGENTS_CRYPTO_OKX_MAX_LEVERAGE=1" in text
    assert "unset OKX_API_KEY OKX_API_SECRET OKX_API_PASSPHRASE" in text
    assert "--mode analysis" in text
    assert "--no-guard-positions" in text
    assert "--execute-top" not in text
    assert ".env.okx" not in text


def test_hermes_watchdog_prevents_overlapping_cycles_and_restarts_stream():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "flock -n 9" in text
    assert "pgrep -f" in text
    assert "crypto-okx-stream" in text
    assert "--require-fresh-stream" in text
