from __future__ import annotations

import json
import random
from dataclasses import replace

from tradingagents.crypto.backtest import BacktestReport, BacktestTrade
from tradingagents.crypto.config import CryptoTradingConfig
from tradingagents.crypto.evolution import (
    CryptoEvolutionRunner,
    EvolutionArchiveStore,
    EvolutionEnvironment,
    EvolutionRunStore,
    LunarGenome,
    evolution_archive_sections,
    evolution_champion_cache_path,
    evolution_compare_sections,
    evolution_html_output_path,
    evolution_preset_sections,
    evolution_preset_template,
    evolution_record_sections,
    evolution_run_ledger_path,
    evolution_run_sections,
    evolution_runs_sections,
    ghost_dca_return_pct,
    load_archive_elite_genomes,
    load_archive_environment_anchor,
    load_evolution_preset,
    load_runtime_champion_config,
    render_evolution_preset_html,
    render_evolution_preset_markdown,
    render_evolution_archive_html,
    render_evolution_archive_markdown,
    render_evolution_compare_html,
    render_evolution_compare_markdown,
    render_evolution_record_html,
    render_evolution_record_markdown,
    render_evolution_runs_html,
    render_evolution_runs_markdown,
    render_evolution_run_html,
    render_evolution_run_markdown,
    score_backtest_report,
    validate_evolution_preset,
)
from tradingagents.crypto.models import Candle, SymbolRules


class FakeEvolutionClient:
    def __init__(self, candles: list[Candle]):
        self.candles = candles
        self.kline_calls: list[tuple[str, str, int]] = []

    def get_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        self.kline_calls.append((symbol, interval, limit))
        return self.candles[-limit:]

    def get_symbol_rules(self, symbol: str) -> SymbolRules:
        return SymbolRules(
            symbol=symbol,
            base_asset=symbol,
            quote_asset="USDC",
            min_qty=0.00001,
            step_size=0.00001,
            min_notional=10.0,
        )


def test_lunar_genome_mutation_stays_inside_parameter_box():
    rng = random.Random(11)
    genome = LunarGenome.random(rng).mutate(rng, probability=1.0, scale=5.0)

    payload = genome.to_dict()
    assert 3 <= payload["max_dca_months"] <= 36
    assert 60 <= payload["t_micro"] <= 360
    assert 0.02 <= payload["min_trade_threshold"] <= 0.30
    assert 0.01 <= payload["micro_reserve_rate"] <= 0.35
    assert 0.0 <= payload["ka"] <= 8.0


def test_evolution_html_output_path_uses_base_name(tmp_path):
    assert evolution_html_output_path(tmp_path / "evolution") == tmp_path / "evolution.html"


def test_ghost_dca_return_tracks_simple_uptrend():
    candles = [
        _candle(index, open_price=100 + index, close=100 + index, high=101 + index, low=99 + index)
        for index in range(10)
    ]

    assert ghost_dca_return_pct({"BTC": candles}, 1000.0) > 0


def test_score_penalizes_pessimistic_fee_friction():
    report = BacktestReport(
        symbols=("BTC",),
        interval="5m",
        bars_requested=100,
        lookback_limit=60,
        max_holding_bars=5,
        fee_bps=0,
        slippage_bps=0,
        signals_evaluated=2,
        risk_rejected=0,
        trades=(
            BacktestTrade(
                symbol="BTC",
                strategy="test",
                entry_time="2026-01-01T00:00:00+00:00",
                exit_time="2026-01-01T00:05:00+00:00",
                entry_price=100.0,
                exit_price=104.0,
                quantity=1.0,
                notional_usdt=100.0,
                pnl_usdt=4.0,
                pnl_pct=4.0,
                outcome="TAKE_PROFIT",
                holding_bars=1,
                confidence=0.80,
                risk_reward=2.0,
                reason="fixture",
            ),
        ),
        total_pnl_usdt=4.0,
        ending_equity_usdt=1004.0,
        total_return_pct=0.4,
        max_drawdown_pct=0.0,
    )

    no_fee = score_backtest_report("winter", 1.0, report, 0.0, EvolutionEnvironment(), 0.0)
    high_fee = score_backtest_report("winter", 1.0, report, 0.0, EvolutionEnvironment(), 20.0)

    assert high_fee.fee_friction_pct > no_fee.fee_friction_pct
    assert high_fee.score < no_fee.score


def test_crypto_evolution_runner_returns_research_challenger():
    candles = _seasonal_breakout_candles()
    config = replace(
        CryptoTradingConfig(),
        interval="5m",
        account_equity_usdt=1000.0,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )

    report = CryptoEvolutionRunner(config, FakeEvolutionClient(candles)).run(
        symbols=("BTC",),
        bars=len(candles),
        population_size=4,
        generations=1,
        seed=7,
        fee_bps=20.0,
        slippage_bps=0.0,
    )

    payload = report.to_dict()
    assert report.challenger.status == "challenger"
    assert len(report.ranked_candidates) == 4
    assert len(report.generation_history) == 1
    assert report.generation_history[0].generation == 1
    assert report.generation_history[0].elite_count == 1
    assert payload["safety"]["places_live_orders"] is False
    assert payload["generation_history"][0]["generation"] == 1
    assert payload["population_plan"]["incumbent_elites"] == 1
    assert payload["population_plan"]["targeted_mutants"] == 2
    assert payload["population_plan"]["explorers"] == 1
    assert payload["population_plan"]["seed_pool_size"] >= 1
    assert payload["run_context"]["fee_bps"] == 20.0
    assert payload["run_context"]["slippage_bps"] == 0.0
    assert payload["run_context"]["monte_carlo_simulations"] == 250
    assert payload["run_context"]["environment_source"] == "default_anchor_sample"
    assert payload["run_context"]["safety"]["places_live_orders"] is False
    assert payload["crucible_windows"][0]["name"] == "all"
    assert any(window["name"].startswith("sampled_") for window in payload["crucible_windows"])
    assert payload["crucible_windows"][0]["start_time"] is not None
    assert payload["challenger"]["safety"]["requires_manual_promote"] is True
    assert payload["challenger"]["environment"]["max_leverage"] == 1
    assert any(
        season["name"].startswith("all:") for season in payload["challenger"]["score"]["seasons"]
    )
    assert any(
        season["name"].startswith("sampled_") for season in payload["challenger"]["score"]["seasons"]
    )
    assert len(payload["challenger"]["score"]["seasons"]) > 4
    assert report.challenger.final_review is not None
    assert "inventory_bridge" in payload["challenger"]["final_review"]
    assert payload["challenger"]["final_review"]["monte_carlo"]["simulations"] == 250
    assert "## Run Context" in report.render_markdown()


def test_crypto_evolution_runner_reuses_epoch_candle_snapshot():
    candles = _seasonal_breakout_candles()
    client = FakeEvolutionClient(candles)
    config = replace(
        CryptoTradingConfig(),
        interval="5m",
        account_equity_usdt=1000.0,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )

    CryptoEvolutionRunner(config, client).run(
        symbols=("BTC",),
        bars=len(candles),
        population_size=4,
        generations=1,
        seed=7,
        fee_bps=20.0,
        slippage_bps=0.0,
    )

    assert client.kline_calls == [("BTC", "5m", len(candles))]


def test_evolution_run_store_records_completed_job_dashboard(tmp_path):
    candles = _seasonal_breakout_candles()
    config = replace(
        CryptoTradingConfig(),
        interval="5m",
        account_equity_usdt=1000.0,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
        state_dir=tmp_path,
    )
    report = CryptoEvolutionRunner(config, FakeEvolutionClient(candles)).run(
        symbols=("BTC",),
        bars=len(candles),
        population_size=4,
        generations=1,
        seed=7,
        fee_bps=20.0,
        slippage_bps=0.0,
    )
    store = EvolutionRunStore(config, tmp_path / "runs.json")

    failed_start = store.record_started(
        symbols=("BTC",),
        interval="5m",
        bars_requested=len(candles),
        population_size=4,
        generations=1,
        seed=8,
        run_context={"phase": "failure-fixture"},
        created_at="2026-01-01T00:00:00+00:00",
    )
    failed = store.record_failed(
        failed_start["run_id"],
        error="fixture failure",
        error_type="RuntimeError",
    )
    completed_start = store.record_started(
        symbols=("BTC",),
        interval="5m",
        bars_requested=len(candles),
        population_size=4,
        generations=1,
        seed=7,
        run_context={"phase": "completion-fixture"},
        created_at="2026-01-01T00:00:01+00:00",
    )
    record = store.record_completed(
        report,
        output_paths={"json": str(tmp_path / "evolution.json")},
        archive_path=tmp_path / "archive.json",
        archived_candidate_id=report.challenger.candidate_id,
        run_id=completed_start["run_id"],
    )
    data = store.load()
    sections = evolution_runs_sections(data, ledger_path=store.path, limit=10)
    markdown = render_evolution_runs_markdown(data, ledger_path=store.path, limit=10)
    html = render_evolution_runs_html(data, ledger_path=store.path, limit=10)
    job_sections = evolution_run_sections(record, ledger_path=store.path)
    job_markdown = render_evolution_run_markdown(record, ledger_path=store.path)
    job_html = render_evolution_run_html(failed, ledger_path=store.path)

    assert evolution_run_ledger_path(config) == tmp_path / "evolution_runs.json"
    assert failed["status"] == "failed"
    assert failed["error"]["type"] == "RuntimeError"
    assert record["run_id"] == completed_start["run_id"]
    assert record["status"] == "completed"
    assert record["challenger_id"] == report.challenger.candidate_id
    assert record["output_paths"]["json"].endswith("evolution.json")
    assert record["archive_path"].endswith("archive.json")
    assert len(data["runs"]) == 2
    assert sections["identity"]["run_count"] == 2
    assert sections["status_counts"]["completed"] == 1
    assert sections["status_counts"]["failed"] == 1
    assert sections["runs"][0]["challenger_id"] == report.challenger.candidate_id
    assert sections["runs"][0]["output_count"] == 1
    assert sections["runs"][1]["error_message"] == "fixture failure"
    assert "# Evolution Jobs Dashboard" in markdown
    assert "fixture failure" in markdown
    assert "Evolution Jobs Dashboard" in html
    assert "live orders disabled" in html
    assert job_sections["identity"]["run_id"] == record["run_id"]
    assert job_sections["outputs"]["json"].endswith("evolution.json")
    assert job_sections["run_context"]["phase"] == "completion-fixture"
    assert "# Evolution Job State" in job_markdown
    assert "completion-fixture" in job_markdown
    assert "Evolution Job State" in job_html
    assert "fixture failure" in job_html


def test_evolution_archive_promotes_challenger_and_retires_old_champion(tmp_path):
    candles = _seasonal_breakout_candles()
    config = replace(
        CryptoTradingConfig(),
        interval="5m",
        account_equity_usdt=1000.0,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )
    report = CryptoEvolutionRunner(config, FakeEvolutionClient(candles)).run(
        symbols=("BTC",),
        bars=len(candles),
        population_size=4,
        generations=1,
        seed=7,
        fee_bps=20.0,
        slippage_bps=0.0,
    )
    second_candidate = replace(report.challenger, candidate_id="challenger-manual")
    second_report = replace(report, challenger=second_candidate)
    store = EvolutionArchiveStore(config, tmp_path / "evolution_archive.json")

    first_record = store.save_challenger(report)
    second_record = store.save_challenger(second_report)
    first_promotion = store.promote(first_record["candidate_id"])
    comparison_sections = evolution_compare_sections(
        store.find(second_record["candidate_id"]),
        store.current_champion(),
    )
    comparison_markdown = render_evolution_compare_markdown(
        store.find(second_record["candidate_id"]),
        store.current_champion(),
    )
    comparison_html = render_evolution_compare_html(
        store.find(second_record["candidate_id"]),
        store.current_champion(),
    )
    second_promotion = store.promote(second_record["candidate_id"])
    data = store.load()
    sections = evolution_archive_sections(data, archive_path=store.path, limit=10)
    markdown = render_evolution_archive_markdown(data, archive_path=store.path, limit=10)
    html = render_evolution_archive_html(data, archive_path=store.path, limit=10)

    assert first_promotion.retired is None
    assert first_promotion.promotion_review is None
    assert first_promotion.champion_cache_path == evolution_champion_cache_path(config, store.path)
    assert first_promotion.champion_cache["candidate_id"] == first_record["candidate_id"]
    assert second_promotion.retired is not None
    assert second_promotion.promotion_review is not None
    assert second_promotion.promotion_review["baseline_candidate_id"] == first_record["candidate_id"]
    assert second_promotion.promotion_review["blocks_promotion"] is False
    assert second_promotion.champion_cache_path.exists()
    assert second_promotion.champion_cache["candidate_id"] == "challenger-manual"
    assert second_promotion.champion_cache["safety"]["places_live_orders"] is False
    assert data["champion_id"] == "challenger-manual"
    assert store.current_champion()["candidate_id"] == "challenger-manual"
    statuses = {record["candidate_id"]: record["status"] for record in data["records"]}
    assert statuses[first_record["candidate_id"]] == "retired"
    assert statuses["challenger-manual"] == "champion"
    assert store.current_champion()["promotion_review_at_promote"]["baseline_candidate_id"] == first_record["candidate_id"]
    assert store.current_champion()["candidate"]["safety"]["requires_manual_promote"] is False
    cached_payload = json.loads(second_promotion.champion_cache_path.read_text(encoding="utf-8"))
    assert cached_payload["candidate_id"] == "challenger-manual"
    assert cached_payload["promotion_review_at_promote"]["baseline_candidate_id"] == first_record["candidate_id"]
    assert comparison_sections["identity"]["candidate_id"] == "challenger-manual"
    assert comparison_sections["identity"]["baseline_id"] == first_record["candidate_id"]
    assert comparison_sections["safety"]["delta_direction"] == "candidate_minus_baseline"
    assert comparison_sections["promotion_review"]["verdict"] in {"pass", "review", "fail"}
    assert comparison_sections["promotion_review"]["blocks_promotion"] is False
    assert "score_delta" in comparison_sections["promotion_review"]
    assert any(row["name"] == "score" for row in comparison_sections["score_delta"])
    assert "# Evolution Package Comparison" in comparison_markdown
    assert "## Promotion Review" in comparison_markdown
    assert "Evolution Package Comparison" in comparison_html
    assert "Promotion Review" in comparison_html
    assert sections["identity"]["champion_id"] == "challenger-manual"
    assert sections["status_counts"]["champion"] == 1
    assert sections["status_counts"]["retired"] == 1
    assert sections["records"][0]["candidate_id"] == "challenger-manual"
    assert "# Evolution Archive Dashboard" in markdown
    assert "Evolution Archive Dashboard" in html
    assert "read only" in html


def test_runtime_champion_config_is_analysis_paper_only_and_conservative(tmp_path):
    candles = _seasonal_breakout_candles()
    base_config = replace(
        CryptoTradingConfig(),
        interval="5m",
        account_equity_usdt=1000.0,
        min_confidence=0.70,
        max_position_pct=0.05,
        execution_mode="paper",
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )
    report = CryptoEvolutionRunner(base_config, FakeEvolutionClient(candles)).run(
        symbols=("BTC",),
        bars=len(candles),
        population_size=4,
        generations=1,
        seed=7,
        fee_bps=20.0,
        slippage_bps=0.0,
    )
    archive_path = tmp_path / "evolution_archive.json"
    store = EvolutionArchiveStore(base_config, archive_path)
    record = store.save_challenger(report)
    store.promote(record["candidate_id"])

    application = load_runtime_champion_config(base_config, archive_path, season="autumn")

    assert application is not None
    assert application.candidate_id == record["candidate_id"]
    assert application.parameter_source == "cache"
    assert application.champion_cache_path == evolution_champion_cache_path(base_config, archive_path)
    assert application.config.hyperliquid_max_leverage == 1
    assert application.config.lookback_limit == report.challenger.genome.t_micro
    assert application.config.min_confidence >= base_config.min_confidence
    assert application.config.max_position_pct <= base_config.max_position_pct
    assert application.applied_settings["hyperliquid_max_leverage"] == 1
    assert application.season_source == "manual"
    assert application.regime_assessment is None

    cache_payload = json.loads(application.champion_cache_path.read_text(encoding="utf-8"))
    cache_payload["candidate_id"] = "stale-cache"
    application.champion_cache_path.write_text(json.dumps(cache_payload), encoding="utf-8")
    fallback_application = load_runtime_champion_config(base_config, archive_path, season="autumn")

    assert fallback_application is not None
    assert fallback_application.candidate_id == record["candidate_id"]
    assert fallback_application.parameter_source == "archive"
    assert fallback_application.champion_cache_path is None

    cache_payload["candidate_id"] = record["candidate_id"]
    application.champion_cache_path.write_text(json.dumps(cache_payload), encoding="utf-8")
    archive_path.write_text("{", encoding="utf-8")
    cache_only_application = load_runtime_champion_config(base_config, archive_path, season="autumn")

    assert cache_only_application is not None
    assert cache_only_application.candidate_id == record["candidate_id"]
    assert cache_only_application.parameter_source == "cache"

    application.champion_cache_path.write_text("{", encoding="utf-8")
    assert load_runtime_champion_config(base_config, archive_path, season="autumn") is None

    live_config = replace(base_config, execution_mode="live")
    try:
        load_runtime_champion_config(live_config, archive_path, season="spring")
    except ValueError as exc:
        assert "analysis or paper" in str(exc)
    else:
        raise AssertionError("live mode must not load evolution champion packages")


def test_evolution_record_sections_render_state_window_layers(tmp_path):
    candles = _seasonal_breakout_candles()
    config = replace(
        CryptoTradingConfig(),
        interval="5m",
        account_equity_usdt=1000.0,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )
    report = CryptoEvolutionRunner(config, FakeEvolutionClient(candles)).run(
        symbols=("BTC",),
        bars=len(candles),
        population_size=4,
        generations=1,
        seed=7,
        fee_bps=20.0,
        slippage_bps=0.0,
    )
    store = EvolutionArchiveStore(config, tmp_path / "evolution_archive.json")
    record = store.save_challenger(report)

    sections = evolution_record_sections(record)
    markdown = render_evolution_record_markdown(record)
    html = render_evolution_record_html(record)

    assert sections["identity"]["candidate_id"] == record["candidate_id"]
    assert "dead_reserve_ratio" in sections["environment"]
    assert "beta_threshold" in sections["macro_genes"]
    assert "t_micro" in sections["timing_genes"]
    assert "min_trade_threshold" in sections["micro_genes"]
    assert sections["generation_history"][0]["generation"] == 1
    assert sections["run_context"]["fee_bps"] == 20.0
    assert "inventory_event_count" in sections["score"]
    assert "inventory_rejected" in sections["score"]
    assert sections["population_plan"]["incumbent_elites"] == 1
    assert sections["population_plan"]["targeted_mutants"] == 2
    assert sections["population_plan"]["explorers"] == 1
    assert sections["crucible_windows"][0]["name"] == "all"
    assert "monte_carlo" in sections["final_review"]
    assert "inventory_bridge" in sections["final_review"]
    assert "## Environment" in markdown
    assert "## Run Context" in markdown
    assert "## Micro Genes" in markdown
    assert "## Population Initialization" in markdown
    assert "## Crucible Windows" in markdown
    assert "## Generation History" in markdown
    assert "Inventory Bridge" in markdown
    assert "## Final Full-Sample Review" in markdown
    assert 'data-editable="false"' in html
    assert "Run Context" in html
    assert "Population Initialization" in html
    assert "Crucible Windows" in html
    assert "Inventory Bridge" in html
    assert record["candidate_id"] in html


def test_evolution_preset_template_round_trips_into_runner(tmp_path):
    template = evolution_preset_template()
    template["environment"]["dead_reserve_ratio"] = 0.33
    template["environment"]["global_stop_loss"] = 0.12
    for index, row in enumerate(template["season_regime"]["seasons"]):
        row["aggressiveness_multiplier"] = (0.60, 0.80, 1.05, 1.25)[index]
    template["seed_genome"]["t_micro"] = 90
    template["seed_genome"]["micro_reserve_rate"] = 0.07
    preset_path = tmp_path / "evolution_preset.json"
    preset_path.write_text(json.dumps(template), encoding="utf-8")

    preset = load_evolution_preset(preset_path)

    assert preset.environment is not None
    assert preset.environment.dead_reserve_ratio == 0.33
    assert preset.environment.max_leverage == 1
    assert preset.season_regime is not None
    assert preset.season_regime.aggressiveness_multipliers == (0.60, 0.80, 1.05, 1.25)
    assert preset.seed_genome is not None
    assert preset.seed_genome.t_micro == 90

    candles = _seasonal_breakout_candles()
    config = replace(
        CryptoTradingConfig(),
        interval="5m",
        account_equity_usdt=1000.0,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )
    report = CryptoEvolutionRunner(config, FakeEvolutionClient(candles)).run(
        symbols=("BTC",),
        bars=len(candles),
        population_size=4,
        generations=1,
        seed=7,
        fee_bps=20.0,
        slippage_bps=0.0,
        environment=preset.environment,
        season_regime=preset.season_regime,
        seed_genome=preset.seed_genome,
    )

    assert report.challenger.environment.dead_reserve_ratio == 0.33
    assert report.challenger.environment.global_stop_loss == 0.12
    assert report.challenger.season_regime.aggressiveness_multipliers == (
        0.60,
        0.80,
        1.05,
        1.25,
    )
    assert report.run_context["environment_source"] == "explicit"
    assert report.run_context["season_regime_source"] == "explicit"
    assert report.run_context["seed_genome_source"] == "explicit"


def test_evolution_preset_sections_render_launch_state(tmp_path):
    template = evolution_preset_template()
    template["environment"]["dead_reserve_ratio"] = 0.42
    template["seed_genome"]["min_trade_threshold"] = 0.11
    preset_path = tmp_path / "evolution_preset.json"
    preset_path.write_text(json.dumps(template), encoding="utf-8")

    preset = load_evolution_preset(preset_path)
    sections = evolution_preset_sections(preset)
    markdown = render_evolution_preset_markdown(preset)
    html = render_evolution_preset_html(preset)
    normalized = evolution_preset_template(preset)

    assert sections["identity"]["defaults_used"] == []
    assert sections["environment"]["dead_reserve_ratio"] == 0.42
    assert sections["micro_genes"]["min_trade_threshold"] == 0.11
    assert normalized["seed_genome"]["min_trade_threshold"] == 0.11
    assert "## Season Regime" in markdown
    assert "## Micro Genes" in markdown
    assert "places_live_orders" in markdown
    assert 'data-editable="true"' in html
    assert "downloadPreset" in html
    assert "data-profile=\"conservative\"" in html
    assert "data-profile=\"aggressive\"" in html
    assert "min_trade_threshold" in html


def test_evolution_preset_validation_reports_projected_inputs(tmp_path):
    template = evolution_preset_template()
    template["environment"]["dead_reserve_ratio"] = 2.0
    template["environment"]["max_leverage"] = 5
    template["season_regime"]["seasons"][0]["aggressiveness_multiplier"] = 0.01
    template["season_regime"]["tick_offset_minutes"] = 99
    template["seed_genome"]["t_micro"] = 1
    template["seed_genome"]["min_trade_threshold"] = 9.0
    template["safety"]["places_live_orders"] = True
    preset_path = tmp_path / "evolution_preset.json"
    preset_path.write_text(json.dumps(template), encoding="utf-8")

    validation = validate_evolution_preset(preset_path)

    assert not validation.ok
    assert validation.preset.environment is not None
    assert validation.preset.environment.dead_reserve_ratio == 0.80
    assert validation.preset.environment.max_leverage == 1
    assert validation.preset.season_regime is not None
    assert validation.preset.season_regime.aggressiveness_multipliers[0] == 0.25
    assert validation.preset.season_regime.tick_offset_minutes == 59
    assert validation.preset.seed_genome is not None
    assert validation.preset.seed_genome.t_micro == 60
    assert validation.preset.seed_genome.min_trade_threshold == 0.30
    assert any(item.startswith("environment.dead_reserve_ratio:") for item in validation.projected_fields)
    assert any(item.startswith("environment.max_leverage:") for item in validation.projected_fields)
    assert any(item.startswith("seed_genome.t_micro:") for item in validation.projected_fields)
    assert "input_values_projected_to_legal_boxes" in validation.warnings
    assert "environment.max_leverage fixed to 1 for spot-only GA epochs" in validation.warnings
    assert "safety.places_live_orders ignored; evolution presets are research-only" in validation.warnings
    assert validation.to_dict()["normalized"]["environment"]["max_leverage"] == 1


def test_archive_elite_genomes_feed_next_initial_population(tmp_path):
    candles = _seasonal_breakout_candles()
    config = replace(
        CryptoTradingConfig(),
        interval="5m",
        account_equity_usdt=1000.0,
        lana_strategy_enabled=False,
        strategy_fusion_enabled=True,
    )
    report = CryptoEvolutionRunner(config, FakeEvolutionClient(candles)).run(
        symbols=("BTC",),
        bars=len(candles),
        population_size=4,
        generations=1,
        seed=7,
        fee_bps=20.0,
        slippage_bps=0.0,
    )
    champion_genome = report.challenger.genome
    challenger_genome = report.challenger.genome.mutate(
        random.Random(31),
        probability=1.0,
        scale=0.25,
    )
    store = EvolutionArchiveStore(config, tmp_path / "evolution_archive.json")
    first = store.save_challenger(report)
    challenger_report = replace(
        report,
        challenger=replace(
            report.challenger,
            candidate_id="challenger-alt",
            genome=challenger_genome,
        ),
    )
    store.save_challenger(challenger_report)
    store.promote(first["candidate_id"])

    elite_genomes = load_archive_elite_genomes(config, store.path)
    environment_anchor = load_archive_environment_anchor(config, store.path)
    seeded_report = CryptoEvolutionRunner(config, FakeEvolutionClient(candles)).run(
        symbols=("BTC",),
        bars=len(candles),
        population_size=10,
        generations=1,
        seed=17,
        fee_bps=20.0,
        slippage_bps=0.0,
        elite_seed_genomes=elite_genomes,
    )

    assert elite_genomes[0] == champion_genome
    assert challenger_genome in elite_genomes
    assert environment_anchor == report.challenger.environment
    assert any(candidate.genome == champion_genome for candidate in seeded_report.ranked_candidates)
    assert seeded_report.population_plan is not None
    assert seeded_report.population_plan.incumbent_elites == 1
    assert seeded_report.population_plan.targeted_mutants == 4
    assert seeded_report.population_plan.explorers == 5
    assert seeded_report.population_plan.elite_seed_count == len(elite_genomes)

    assert environment_anchor is not None
    expected_environment = environment_anchor.sample_next_epoch(random.Random(23))
    anchored_report = CryptoEvolutionRunner(config, FakeEvolutionClient(candles)).run(
        symbols=("BTC",),
        bars=len(candles),
        population_size=4,
        generations=1,
        seed=23,
        fee_bps=20.0,
        slippage_bps=0.0,
        environment_anchor=environment_anchor,
    )
    assert anchored_report.challenger.environment == expected_environment


def _seasonal_breakout_candles() -> list[Candle]:
    candles: list[Candle] = []
    for season in range(4):
        base = 100.0 + season * 8
        for offset in range(135):
            index = len(candles)
            close = base + offset * 0.01
            candles.append(
                _candle(
                    index,
                    open_price=close - 0.01,
                    close=close,
                    high=close + 0.05,
                    low=close - 0.05,
                    volume=5000,
                )
            )
        breakout_open = base + 1.45
        candles.append(
            _candle(
                len(candles),
                open_price=breakout_open,
                close=breakout_open + 1.2,
                high=breakout_open + 1.4,
                low=breakout_open - 0.1,
                volume=15000,
            )
        )
        for _ in range(14):
            index = len(candles)
            close = breakout_open + 2.6
            candles.append(
                _candle(
                    index,
                    open_price=close - 0.1,
                    close=close,
                    high=close + 0.4,
                    low=close - 0.2,
                    volume=7000,
                )
            )
    return candles


def _candle(
    index: int,
    open_price: float,
    close: float,
    high: float,
    low: float,
    volume: float = 5000.0,
) -> Candle:
    open_time = index * 300_000
    return Candle(
        open_time_ms=open_time,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        close_time_ms=open_time + 299_999,
    )
