from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "hermes"
    / "okx_analysis_watchdog.sh"
)
TRIAL_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "hermes"
    / "okx_demo_controlled_trial.sh"
)


def test_hermes_watchdog_is_analysis_only_and_credential_free():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "TRADINGAGENTS_CRYPTO_EXECUTION_MODE=analysis" in text
    assert "TRADINGAGENTS_CRYPTO_OKX_DEMO_EXECUTION_ENABLED=false" in text
    assert "TRADINGAGENTS_CRYPTO_OKX_MAX_LEVERAGE=1" in text
    assert "unset OKX_API_KEY OKX_API_SECRET OKX_API_PASSPHRASE" in text
    assert "OKX_DEMO_EMERGENCY_STOP" in text
    assert 'install -m 600 /dev/null "$DEMO_EMERGENCY_STOP_FILE"' in text
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


def test_controlled_trial_requires_one_time_arm_and_rearms_stop():
    text = TRIAL_SCRIPT.read_text(encoding="utf-8")

    assert "ARM_OKX_DEMO_TRIAL" in text
    assert 'flock -n 9' in text
    assert 'trap rearm_stop EXIT' in text
    assert 'rm -f "$EMERGENCY_STOP_FILE"' in text
    assert 'install -m 600 /dev/null "$EMERGENCY_STOP_FILE"' in text
    assert "TRADINGAGENTS_CRYPTO_OKX_DEMO_EXECUTION_ENABLED=true" in text
    assert "TRADINGAGENTS_CRYPTO_OKX_MAX_LEVERAGE=1" in text
    assert "TRADINGAGENTS_CRYPTO_MAX_LOSS_PER_TRADE_USDT=0.50" in text
    assert "TRADINGAGENTS_CRYPTO_MAX_POSITION_PCT=0.005" in text
    assert "TRADINGAGENTS_CRYPTO_DAILY_LOSS_LIMIT_USDT=1.00" in text
    assert "TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_MAX_HOLDING_MINUTES=1" in text
    assert "Machine readiness: ready=" in text
    assert "timeout 240" in text
    assert "--mode testnet" in text
    assert "--execute-top" in text
    assert "--auto-close" in text
    assert "TRIAL_PASS" in text
