from __future__ import annotations

from tradingagents.crypto import adoption_by_key, adoption_keys, adoption_sources


def test_github_adoption_map_covers_all_reviewed_sources():
    assert set(adoption_keys()) == {
        "hyperliquid-python-sdk",
        "freqtrade",
        "hummingbot",
        "jesse",
        "vectorbt",
        "hftbacktest",
        "nautilus-trader",
    }


def test_github_adoption_sources_have_local_targets_and_guardrails():
    for source in adoption_sources():
        assert source.repository_url.startswith("https://github.com/")
        assert source.local_modules
        assert source.next_step
        assert source.guardrail


def test_hyperliquid_sdk_remains_primary_venue_adoption():
    source = adoption_by_key("hyperliquid-python-sdk")

    assert source.status == "autopilot_stream_gate_added"
    assert "Primary venue adapter" in source.adoption_target
    assert "WebSocket uptime" in source.next_step
    assert "live orders" in source.guardrail


def test_optional_research_dependencies_do_not_become_execution_paths():
    optional = {source.key: source for source in adoption_sources() if "optional" in source.status}

    assert set(optional) == {"vectorbt", "hftbacktest"}
    assert "candidate configurations only" in optional["vectorbt"].next_step
    assert "latency" in optional["hftbacktest"].guardrail
