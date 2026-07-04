"""Genetic research lab for the Hyperliquid crypto extension.

This module is deliberately research-only. It reuses the historical replay
backtester, never calls the execution router, and writes challenger-style
parameter packages that still require explicit promotion before any runtime use.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import random
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backtest import BacktestInventoryBridgeConfig, BacktestReport, CryptoBacktester
from .config import CryptoTradingConfig
from .hyperliquid_client import HyperliquidClient
from .models import Candle, SymbolRules


SEASON_NAMES: tuple[str, ...] = ("winter", "spring", "summer", "autumn")
CRUCIBLE_WINDOWS: tuple[tuple[str, float, float], ...] = (
    ("all", 1.00, 0.40),
    ("long", 0.75, 0.25),
    ("medium", 0.50, 0.20),
    ("short", 0.25, 0.15),
)

_INT_GENE_BOUNDS: dict[str, tuple[int, int]] = {
    "max_dca_months": (3, 36),
    "gc_threshold_months": (3, 48),
    "t_macro": (24, 720),
    "t_micro": (60, 360),
    "t_deadline": (4, 96),
    "ema_anchor": (20, 240),
}

_FLOAT_GENE_BOUNDS: dict[str, tuple[float, float]] = {
    "beta_threshold": (0.02, 0.60),
    "moon_phase_pressure": (0.0, 1.50),
    "deadline_force_pct": (0.0, 0.60),
    "gc_max_ratio": (0.0, 0.80),
    "kp": (0.0, 5.0),
    "kv": (0.0, 5.0),
    "ka": (0.0, 8.0),
    "min_trade_threshold": (0.02, 0.30),
    "micro_reserve_rate": (0.01, 0.35),
    "sigmoid_scale": (0.50, 8.0),
    "gamma": (0.10, 4.0),
    "beta": (0.10, 4.0),
}

_ENV_FLOAT_BOUNDS: dict[str, tuple[float, float]] = {
    "dead_reserve_ratio": (0.0, 0.80),
    "global_stop_loss": (0.0, 0.60),
}

_SEASON_MULTIPLIER_BOUNDS: tuple[float, float] = (0.25, 2.0)
_TICK_OFFSET_BOUNDS: tuple[int, int] = (0, 59)

_MACRO_GENE_NAMES: tuple[str, ...] = (
    "max_dca_months",
    "beta_threshold",
    "moon_phase_pressure",
    "deadline_force_pct",
    "gc_threshold_months",
    "gc_max_ratio",
)

_TIMING_GENE_NAMES: tuple[str, ...] = (
    "t_macro",
    "t_micro",
    "t_deadline",
    "ema_anchor",
)

_MICRO_GENE_NAMES: tuple[str, ...] = (
    "kp",
    "kv",
    "ka",
    "min_trade_threshold",
    "micro_reserve_rate",
    "sigmoid_scale",
    "gamma",
    "beta",
)


@dataclass(frozen=True)
class LunarGenome:
    """Macro, timing, and micro combat genes evolved as one chromosome."""

    max_dca_months: int = 12
    beta_threshold: float = 0.18
    moon_phase_pressure: float = 0.20
    deadline_force_pct: float = 0.10
    gc_threshold_months: int = 18
    gc_max_ratio: float = 0.25
    t_macro: int = 168
    t_micro: int = 120
    t_deadline: int = 32
    ema_anchor: int = 50
    kp: float = 1.0
    kv: float = 1.0
    ka: float = 1.0
    min_trade_threshold: float = 0.08
    micro_reserve_rate: float = 0.10
    sigmoid_scale: float = 2.0
    gamma: float = 1.0
    beta: float = 1.0

    @classmethod
    def default(cls) -> "LunarGenome":
        return cls()

    @classmethod
    def random(cls, rng: random.Random) -> "LunarGenome":
        values: dict[str, int | float] = {}
        for name, bounds in _INT_GENE_BOUNDS.items():
            values[name] = rng.randint(bounds[0], bounds[1])
        for name, bounds in _FLOAT_GENE_BOUNDS.items():
            values[name] = rng.uniform(bounds[0], bounds[1])
        return cls(**values).project()

    def project(self) -> "LunarGenome":
        values = asdict(self)
        for name, bounds in _INT_GENE_BOUNDS.items():
            values[name] = int(round(_clamp(float(values[name]), bounds[0], bounds[1])))
        for name, bounds in _FLOAT_GENE_BOUNDS.items():
            values[name] = _clamp(float(values[name]), bounds[0], bounds[1])
        return LunarGenome(**values)

    def mutate(
        self,
        rng: random.Random,
        probability: float = 0.15,
        scale: float = 1.0,
    ) -> "LunarGenome":
        values = asdict(self)
        for name, bounds in _INT_GENE_BOUNDS.items():
            if rng.random() <= probability:
                step = max(1.0, (bounds[1] - bounds[0]) * 0.08 * scale)
                values[name] = int(round(float(values[name]) + rng.gauss(0.0, step)))
        for name, bounds in _FLOAT_GENE_BOUNDS.items():
            if rng.random() <= probability:
                step = (bounds[1] - bounds[0]) * 0.08 * scale
                values[name] = float(values[name]) + rng.gauss(0.0, step)
        return LunarGenome(**values).project()

    def orthogonal_crossover(self, other: "LunarGenome", rng: random.Random) -> "LunarGenome":
        blocks = (
            (
                "macro",
                (
                    "max_dca_months",
                    "beta_threshold",
                    "moon_phase_pressure",
                    "deadline_force_pct",
                    "gc_threshold_months",
                    "gc_max_ratio",
                ),
            ),
            ("timing", ("t_macro", "t_micro", "t_deadline", "ema_anchor")),
            ("pde", ("kp", "kv", "ka")),
            (
                "trigger",
                (
                    "min_trade_threshold",
                    "micro_reserve_rate",
                    "sigmoid_scale",
                    "gamma",
                    "beta",
                ),
            ),
        )
        self_values = asdict(self)
        other_values = asdict(other)
        child = dict(self_values)
        for _, fields in blocks:
            source = self_values if rng.random() < 0.5 else other_values
            for field in fields:
                child[field] = source[field]
        return LunarGenome(**child).project()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvolutionEnvironment:
    """Epoch-wide physics that genes are not allowed to rewrite."""

    dead_reserve_ratio: float = 0.20
    global_stop_loss: float = 0.20
    max_leverage: int = 1

    @classmethod
    def default(cls) -> "EvolutionEnvironment":
        return cls()

    def project(self) -> "EvolutionEnvironment":
        return EvolutionEnvironment(
            dead_reserve_ratio=_clamp(
                self.dead_reserve_ratio,
                *_ENV_FLOAT_BOUNDS["dead_reserve_ratio"],
            ),
            global_stop_loss=_clamp(self.global_stop_loss, *_ENV_FLOAT_BOUNDS["global_stop_loss"]),
            max_leverage=1,
        )

    def sample_next_epoch(self, rng: random.Random) -> "EvolutionEnvironment":
        values = asdict(self.project())
        for name, bounds in _ENV_FLOAT_BOUNDS.items():
            width = bounds[1] - bounds[0]
            values[name] = _truncated_normal(float(values[name]), width * 0.10, bounds, rng)
        values["max_leverage"] = 1
        return EvolutionEnvironment(**values).project()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.project())


@dataclass(frozen=True)
class SeasonRegime:
    """Four fixed season frictions for one epoch."""

    aggressiveness_multipliers: tuple[float, float, float, float] = (0.70, 0.90, 1.00, 1.15)
    tick_offset_minutes: int = 0

    @classmethod
    def sample(cls, rng: random.Random) -> "SeasonRegime":
        levels = (0.55, 0.75, 0.90, 1.00, 1.15, 1.35)
        weights = (0.08, 0.14, 0.22, 0.28, 0.18, 0.10)
        draws = sorted(rng.choices(levels, weights=weights, k=4))
        return cls(aggressiveness_multipliers=tuple(draws), tick_offset_minutes=0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seasons": [
                {"name": name, "aggressiveness_multiplier": multiplier}
                for name, multiplier in zip(SEASON_NAMES, self.aggressiveness_multipliers)
            ],
            "tick_offset_minutes": self.tick_offset_minutes,
        }

    def project(self) -> "SeasonRegime":
        multipliers = tuple(
            _clamp(float(value), *_SEASON_MULTIPLIER_BOUNDS)
            for value in self.aggressiveness_multipliers
        )
        if len(multipliers) != 4:
            raise ValueError("season regime must include exactly four aggressiveness multipliers.")
        tick_offset = int(
            round(_clamp(float(self.tick_offset_minutes), *_TICK_OFFSET_BOUNDS))
        )
        return SeasonRegime(
            aggressiveness_multipliers=multipliers,
            tick_offset_minutes=tick_offset,
        )


@dataclass(frozen=True)
class SeasonScore:
    name: str
    aggressiveness_multiplier: float
    score: float
    alpha_pct: float
    total_return_pct: float
    baseline_return_pct: float
    realized_pnl_usdt: float
    fee_friction_pct: float
    max_drawdown_pct: float
    trades: int
    wins: int
    win_rate: float
    max_consecutive_losses: int
    risk_rejected: int
    inventory_event_count: int
    inventory_rejected: int
    inventory_realized_pnl_usdt: float


@dataclass(frozen=True)
class GenomeScore:
    score: float
    alpha_pct: float
    realized_pnl_usdt: float
    fee_friction_pct: float
    max_drawdown_pct: float
    trades: int
    win_rate: float
    max_consecutive_losses: int
    risk_rejected: int
    inventory_event_count: int
    inventory_rejected: int
    inventory_realized_pnl_usdt: float
    seasons: tuple[SeasonScore, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["seasons"] = [asdict(item) for item in self.seasons]
        return payload


@dataclass(frozen=True)
class MonteCarloReview:
    simulations: int
    trade_count: int
    bankruptcy_probability: float
    stop_loss_breach_probability: float
    return_p05_pct: float
    return_p50_pct: float
    return_p95_pct: float
    max_drawdown_p95_pct: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InventoryBridgeStats:
    event_count: int
    macro_buys: int
    acceleration_unlocks: int
    stale_gc_unlocks: int
    lot_size_rejected: int
    micro_sells: int
    realized_pnl_usdt: float
    ending_dead_quantity: float
    ending_float_quantity: float
    ending_quote_balance_usdt: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FinalSampleReview:
    interval: str
    bars_requested: int
    aggressiveness_multiplier: float
    baseline_return_pct: float
    alpha_pct: float
    signals_evaluated: int
    risk_rejected: int
    trades: int
    win_rate: float
    total_pnl_usdt: float
    total_return_pct: float
    max_drawdown_pct: float
    inventory_bridge: InventoryBridgeStats
    monte_carlo: MonteCarloReview
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["inventory_bridge"] = self.inventory_bridge.to_dict()
        payload["monte_carlo"] = self.monte_carlo.to_dict()
        payload["warnings"] = list(self.warnings)
        return payload


@dataclass(frozen=True)
class EvolutionGenerationStat:
    generation: int
    best_candidate_id: str
    best_score: float
    average_score: float
    best_alpha_pct: float
    best_max_drawdown_pct: float
    best_trades: int
    elite_count: int
    mutation_probability: float
    mutation_scale: float
    stale_generations: int
    improved: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvolutionWindowStat:
    name: str
    weight: float
    bars: int
    start_time: str | None
    end_time: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvolutionPopulationPlan:
    population_size: int
    incumbent_elites: int
    targeted_mutants: int
    explorers: int
    seed_pool_size: int
    elite_seed_count: int
    has_explicit_seed_genome: bool

    @property
    def incumbent_ratio(self) -> float:
        return self.incumbent_elites / self.population_size if self.population_size else 0.0

    @property
    def targeted_mutant_ratio(self) -> float:
        return self.targeted_mutants / self.population_size if self.population_size else 0.0

    @property
    def explorer_ratio(self) -> float:
        return self.explorers / self.population_size if self.population_size else 0.0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["incumbent_ratio"] = self.incumbent_ratio
        payload["targeted_mutant_ratio"] = self.targeted_mutant_ratio
        payload["explorer_ratio"] = self.explorer_ratio
        return payload


@dataclass(frozen=True)
class EvolutionCandidate:
    candidate_id: str
    status: str
    genome: LunarGenome
    environment: EvolutionEnvironment
    season_regime: SeasonRegime
    score: GenomeScore
    final_review: FinalSampleReview | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "candidate_id": self.candidate_id,
            "status": self.status,
            "genome": self.genome.to_dict(),
            "environment": self.environment.to_dict(),
            "season_regime": self.season_regime.to_dict(),
            "score": self.score.to_dict(),
            "safety": {
                "mode": "research_only",
                "places_live_orders": False,
                "requires_manual_promote": True,
                "max_leverage": self.environment.max_leverage,
            },
        }
        if self.final_review is not None:
            payload["final_review"] = self.final_review.to_dict()
        return payload


@dataclass(frozen=True)
class EvolutionRunReport:
    created_at: str
    symbols: tuple[str, ...]
    interval: str
    bars_requested: int
    population_size: int
    generations: int
    seed: int | None
    challenger: EvolutionCandidate
    ranked_candidates: tuple[EvolutionCandidate, ...]
    generation_history: tuple[EvolutionGenerationStat, ...] = ()
    crucible_windows: tuple[EvolutionWindowStat, ...] = ()
    population_plan: EvolutionPopulationPlan | None = None
    run_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "symbols": list(self.symbols),
            "interval": self.interval,
            "bars_requested": self.bars_requested,
            "population_size": self.population_size,
            "generations": self.generations,
            "seed": self.seed,
            "challenger": self.challenger.to_dict(),
            "ranked_candidates": [candidate.to_dict() for candidate in self.ranked_candidates],
            "generation_history": [item.to_dict() for item in self.generation_history],
            "crucible_windows": [item.to_dict() for item in self.crucible_windows],
            "population_plan": (
                self.population_plan.to_dict() if self.population_plan is not None else {}
            ),
            "run_context": dict(self.run_context),
            "safety": {
                "places_live_orders": False,
                "writes_challenger_only": True,
                "live_agent_unchanged": True,
            },
        }

    def render_markdown(self, top: int = 10) -> str:
        challenger = self.challenger
        lines = [
            "# Hyperliquid Evolution Lab",
            "",
            f"- Created at: {self.created_at}",
            f"- Symbols: {', '.join(self.symbols)}",
            f"- Interval: {self.interval}",
            f"- Bars per symbol: {self.bars_requested}",
            f"- Population: {self.population_size}",
            f"- Generations: {self.generations}",
            f"- Seed: {self.seed if self.seed is not None else '-'}",
            "- Mode: research only",
            "- Live orders: disabled",
            "",
            "## Challenger",
            "",
            f"- Candidate ID: `{challenger.candidate_id}`",
            f"- Score: {challenger.score.score:.4f}",
            f"- Alpha: {challenger.score.alpha_pct:.2f}%",
            f"- Realized PnL: {challenger.score.realized_pnl_usdt:.4f} USDT",
            f"- Max drawdown: {challenger.score.max_drawdown_pct:.2f}%",
            f"- Trades: {challenger.score.trades}",
            f"- Win rate: {challenger.score.win_rate:.2%}",
            "",
        ]
        if self.run_context:
            lines.extend(
                [
                    "## Run Context",
                    "",
                    _markdown_mapping_table(_flatten_for_display(self.run_context)),
                    "",
                ]
            )
        if self.population_plan is not None:
            plan = self.population_plan
            lines.extend(
                [
                    "## Population Initialization",
                    "",
                    f"- Incumbent elites: {plan.incumbent_elites} ({plan.incumbent_ratio:.2%})",
                    f"- Targeted mutants: {plan.targeted_mutants} ({plan.targeted_mutant_ratio:.2%})",
                    f"- Explorers: {plan.explorers} ({plan.explorer_ratio:.2%})",
                    f"- Seed pool size: {plan.seed_pool_size}",
                    f"- Archive elite seeds: {plan.elite_seed_count}",
                    f"- Explicit seed genome: {'yes' if plan.has_explicit_seed_genome else 'no'}",
                    "",
                ]
            )
        if self.crucible_windows:
            lines.extend(
                [
                    "## Crucible Windows",
                    "",
                    "| Window | Weight | Bars | Start | End |",
                    "| --- | ---: | ---: | --- | --- |",
                ]
            )
            for item in self.crucible_windows:
                lines.append(
                    "| {name} | {weight:.2f} | {bars} | {start} | {end} |".format(
                        name=item.name,
                        weight=item.weight,
                        bars=item.bars,
                        start=item.start_time or "-",
                        end=item.end_time or "-",
                    )
                )
            lines.append("")
        if challenger.final_review is not None:
            review = challenger.final_review
            mc = review.monte_carlo
            lines.extend(
                [
                    "## Final Full-Sample Review",
                    "",
                    f"- Alpha: {review.alpha_pct:.2f}%",
                    f"- Return: {review.total_return_pct:.2f}%",
                    f"- Max drawdown: {review.max_drawdown_pct:.2f}%",
                    f"- Trades: {review.trades}",
                    (
                        "- Inventory bridge: "
                        f"events={review.inventory_bridge.event_count}, "
                        f"micro_sells={review.inventory_bridge.micro_sells}, "
                        f"realized={review.inventory_bridge.realized_pnl_usdt:.4f} USDT"
                    ),
                    (
                        "- Monte Carlo P05/P50/P95: "
                        f"{mc.return_p05_pct:.2f}% / {mc.return_p50_pct:.2f}% / "
                        f"{mc.return_p95_pct:.2f}%"
                    ),
                    f"- Bankruptcy probability: {mc.bankruptcy_probability:.2%}",
                    f"- Stop-loss breach probability: {mc.stop_loss_breach_probability:.2%}",
                ]
            )
            if review.warnings:
                lines.extend(["", "Warnings:"])
                lines.extend(f"- {warning}" for warning in review.warnings)
            lines.append("")
        if self.generation_history:
            lines.extend(
                [
                    "## Generation History",
                    "",
                    "| Gen | Best score | Avg score | Mut prob | Mut scale | Stale | Improved | Best candidate |",
                    "| ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
                ]
            )
            for item in self.generation_history:
                lines.append(
                    "| {generation} | {best:.4f} | {avg:.4f} | {prob:.3f} | {scale:.3f} | "
                    "{stale} | {improved} | `{candidate}` |".format(
                        generation=item.generation,
                        best=item.best_score,
                        avg=item.average_score,
                        prob=item.mutation_probability,
                        scale=item.mutation_scale,
                        stale=item.stale_generations,
                        improved="yes" if item.improved else "no",
                        candidate=item.best_candidate_id,
                    )
                )
            lines.append("")
        lines.extend(
            [
                "## Ranked Candidates",
                "",
                "| Rank | Candidate | Score | Alpha | PnL USDT | Max DD | Trades | Win | Fee friction |",
                "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for rank, candidate in enumerate(self.ranked_candidates[:top], start=1):
            score = candidate.score
            lines.append(
                "| {rank} | `{candidate_id}` | {score:.4f} | {alpha:.2f}% | "
                "{pnl:.4f} | {drawdown:.2f}% | {trades} | {win_rate:.2%} | "
                "{friction:.2f}% |".format(
                    rank=rank,
                    candidate_id=candidate.candidate_id,
                    score=score.score,
                    alpha=score.alpha_pct,
                    pnl=score.realized_pnl_usdt,
                    drawdown=score.max_drawdown_pct,
                    trades=score.trades,
                    win_rate=score.win_rate,
                    friction=score.fee_friction_pct,
                )
            )
        lines.extend(
            [
                "",
                "## Safety",
                "",
                "- This report is generated by historical replay only.",
                "- The challenger is not a champion and cannot place orders.",
                "- Runtime instances must continue to load the current champion or built-in defaults.",
            ]
        )
        return "\n".join(lines).strip() + "\n"


@dataclass(frozen=True)
class EvolutionPromotionResult:
    candidate_id: str
    promoted: dict[str, Any]
    retired: dict[str, Any] | None
    archive_path: Path
    promotion_review: dict[str, Any] | None = None
    champion_cache_path: Path | None = None
    champion_cache: dict[str, Any] | None = None


@dataclass(frozen=True)
class RuntimeChampionApplication:
    candidate_id: str
    archive_path: Path
    season: str
    aggressiveness_multiplier: float
    config: CryptoTradingConfig
    applied_settings: dict[str, Any]
    record: dict[str, Any]
    season_source: str = "manual"
    regime_assessment: dict[str, Any] | None = None
    parameter_source: str = "archive"
    champion_cache_path: Path | None = None


@dataclass(frozen=True)
class EvolutionLabPreset:
    environment: EvolutionEnvironment | None = None
    season_regime: SeasonRegime | None = None
    seed_genome: LunarGenome | None = None


@dataclass(frozen=True)
class EvolutionPresetValidation:
    """Pre-run preset inspection after projecting inputs into legal boxes."""

    preset: EvolutionLabPreset
    normalized: dict[str, Any]
    defaults_used: tuple[str, ...]
    projected_fields: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.projected_fields and not self.warnings

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "defaults_used": list(self.defaults_used),
            "projected_fields": list(self.projected_fields),
            "warnings": list(self.warnings),
            "normalized": self.normalized,
        }


class CryptoGenomeEvaluator:
    """Evaluate one genome under fixed environment and season frictions."""

    def __init__(
        self,
        config: CryptoTradingConfig | None = None,
        client=None,
        slice_seed: int | None = None,
    ):
        self.config = config or CryptoTradingConfig.from_env()
        self.client = client or HyperliquidClient(self.config)
        self._candle_cache: dict[tuple[tuple[str, ...], str, int], dict[str, list[Candle]]] = {}
        self._slice_rng = random.Random(slice_seed)
        self._window_cache: dict[
            tuple[tuple[tuple[str, int, int, int], ...], int],
            tuple[tuple[str, float, dict[str, list[Candle]]], ...],
        ] = {}
        self._last_window_stats: tuple[EvolutionWindowStat, ...] = ()

    @property
    def window_history(self) -> tuple[EvolutionWindowStat, ...]:
        return self._last_window_stats

    def evaluate(
        self,
        genome: LunarGenome,
        environment: EvolutionEnvironment,
        season_regime: SeasonRegime,
        symbols: tuple[str, ...],
        bars: int,
        fee_bps: float = 20.0,
        slippage_bps: float = 2.0,
    ) -> GenomeScore:
        base_candles = self._load_candles(symbols, bars)
        weighted_scores: list[tuple[SeasonScore, float]] = []
        for window_name, window_weight, window_candles in self._crucible_window_sets(
            base_candles,
            min_bars=max(260, self.config.lookback_limit * 4 + 4),
        ):
            for index, (name, multiplier) in enumerate(
                zip(SEASON_NAMES, season_regime.aggressiveness_multipliers)
            ):
                segment = _slice_season(window_candles, index)
                if not segment:
                    segment = window_candles
                config = _config_from_genome(
                    self.config,
                    genome,
                    environment,
                    aggressiveness_multiplier=multiplier,
                )
                inventory_bridge = _inventory_bridge_from_genome(
                    genome,
                    environment,
                    aggressiveness_multiplier=multiplier,
                )
                cached_client = _CachedBacktestClient(self.client, segment)
                report = CryptoBacktester(config, cached_client).run(
                    symbols=symbols,
                    bars=min(bars, min(len(items) for items in segment.values())),
                    max_holding_bars=genome.t_deadline,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                    inventory_bridge=inventory_bridge,
                )
                baseline_return = ghost_dca_return_pct(segment, config.account_equity_usdt)
                weighted_scores.append(
                    (
                        score_backtest_report(
                            name=f"{window_name}:{name}",
                            multiplier=multiplier,
                            report=report,
                            baseline_return_pct=baseline_return,
                            environment=environment,
                            fee_bps=fee_bps,
                        ),
                        window_weight,
                    )
                )
        return _combine_weighted_scores(tuple(weighted_scores))

    def _load_candles(self, symbols: tuple[str, ...], bars: int) -> dict[str, list[Candle]]:
        normalized_symbols = tuple(symbols)
        key = (normalized_symbols, self.config.interval, bars)
        if key not in self._candle_cache:
            self._candle_cache[key] = {
                symbol: self.client.get_klines(symbol, self.config.interval, bars)
                for symbol in normalized_symbols
            }
        return {symbol: list(candles) for symbol, candles in self._candle_cache[key].items()}

    def _crucible_window_sets(
        self,
        candles_by_symbol: dict[str, list[Candle]],
        min_bars: int,
    ) -> tuple[tuple[str, float, dict[str, list[Candle]]], ...]:
        key = (_candle_snapshot_key(candles_by_symbol), min_bars)
        if key not in self._window_cache:
            self._window_cache[key] = _crucible_window_sets(
                candles_by_symbol,
                min_bars=min_bars,
                rng=self._slice_rng,
            )
        windows = self._window_cache[key]
        self._last_window_stats = tuple(
            _evolution_window_stat(name, weight, window_candles)
            for name, weight, window_candles in windows
        )
        return windows

    def final_review(
        self,
        genome: LunarGenome,
        environment: EvolutionEnvironment,
        season_regime: SeasonRegime,
        symbols: tuple[str, ...],
        bars: int,
        fee_bps: float = 20.0,
        slippage_bps: float = 2.0,
        simulations: int = 250,
        seed: int | None = None,
    ) -> FinalSampleReview:
        base_candles = self._load_candles(symbols, bars)
        multiplier = sum(season_regime.aggressiveness_multipliers) / len(
            season_regime.aggressiveness_multipliers
        )
        config = _config_from_genome(
            self.config,
            genome,
            environment,
            aggressiveness_multiplier=multiplier,
        )
        inventory_bridge = _inventory_bridge_from_genome(
            genome,
            environment,
            aggressiveness_multiplier=multiplier,
        )
        report = CryptoBacktester(config, _CachedBacktestClient(self.client, base_candles)).run(
            symbols=symbols,
            bars=min(bars, min(len(items) for items in base_candles.values())),
            max_holding_bars=genome.t_deadline,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            inventory_bridge=inventory_bridge,
        )
        baseline_return = ghost_dca_return_pct(base_candles, config.account_equity_usdt)
        alpha = report.total_return_pct - baseline_return
        monte_carlo = monte_carlo_trade_review(
            report=report,
            environment=environment,
            simulations=simulations,
            rng=random.Random(seed),
        )
        warnings = _final_review_warnings(report, monte_carlo)
        inventory_bridge = _inventory_bridge_stats(report)
        return FinalSampleReview(
            interval=config.interval,
            bars_requested=bars,
            aggressiveness_multiplier=multiplier,
            baseline_return_pct=baseline_return,
            alpha_pct=alpha,
            signals_evaluated=report.signals_evaluated,
            risk_rejected=report.risk_rejected,
            trades=len(report.trades),
            win_rate=report.win_rate,
            total_pnl_usdt=report.total_pnl_usdt,
            total_return_pct=report.total_return_pct,
            max_drawdown_pct=report.max_drawdown_pct,
            inventory_bridge=inventory_bridge,
            monte_carlo=monte_carlo,
            warnings=warnings,
        )


class CryptoEvolutionRunner:
    """Small GA loop that produces a challenger package, not a live strategy."""

    def __init__(
        self,
        config: CryptoTradingConfig | None = None,
        client=None,
    ):
        self.config = config or CryptoTradingConfig.from_env()
        self.client = client or HyperliquidClient(self.config)

    def run(
        self,
        symbols: tuple[str, ...],
        bars: int = 800,
        population_size: int = 10,
        generations: int = 3,
        seed: int | None = None,
        fee_bps: float = 20.0,
        slippage_bps: float = 2.0,
        elite_ratio: float = 0.05,
        environment: EvolutionEnvironment | None = None,
        environment_anchor: EvolutionEnvironment | None = None,
        season_regime: SeasonRegime | None = None,
        seed_genome: LunarGenome | None = None,
        elite_seed_genomes: tuple[LunarGenome, ...] = (),
        monte_carlo_simulations: int = 250,
    ) -> EvolutionRunReport:
        if population_size < 4:
            raise ValueError("population_size must be at least 4.")
        if generations < 1:
            raise ValueError("generations must be at least 1.")

        explicit_environment = environment is not None
        has_environment_anchor = environment_anchor is not None
        explicit_season_regime = season_regime is not None
        explicit_seed_genome = seed_genome is not None
        rng = random.Random(seed)
        anchor = (
            environment_anchor.project()
            if environment_anchor is not None
            else EvolutionEnvironment.default()
        )
        environment = (
            environment.project()
            if environment is not None
            else anchor.sample_next_epoch(rng)
        )
        season_regime = season_regime.project() if season_regime is not None else SeasonRegime.sample(rng)
        evaluator = CryptoGenomeEvaluator(self.config, self.client, slice_seed=seed)
        population, population_plan = _initial_population(
            population_size,
            rng,
            seed_genome=seed_genome,
            elite_seed_genomes=elite_seed_genomes,
        )
        mutation_probability = 0.15
        mutation_scale = 1.0
        best_score = -math.inf
        stale_generations = 0
        ranked: list[EvolutionCandidate] = []
        generation_history: list[EvolutionGenerationStat] = []

        for generation_index in range(1, generations + 1):
            ranked = _rank_population(
                population=population,
                evaluator=evaluator,
                environment=environment,
                season_regime=season_regime,
                symbols=symbols,
                bars=bars,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            )
            current_best = ranked[0].score.score
            improved = current_best > best_score + 1e-9
            if current_best > best_score + 1e-9:
                best_score = current_best
                stale_generations = 0
            else:
                stale_generations += 1
                mutation_probability = min(0.55, mutation_probability * 1.25)
                mutation_scale = min(3.0, mutation_scale * 1.25)

            elite_count = max(1, math.ceil(population_size * elite_ratio))
            generation_history.append(
                _generation_stat(
                    generation=generation_index,
                    ranked=ranked,
                    elite_count=elite_count,
                    mutation_probability=mutation_probability,
                    mutation_scale=mutation_scale,
                    stale_generations=stale_generations,
                    improved=improved,
                )
            )
            if generation_index == generations:
                break

            elites = [candidate.genome for candidate in ranked[:elite_count]]
            parent_pool = [candidate.genome for candidate in ranked[: max(2, population_size // 2)]]
            population = list(elites)
            while len(population) < population_size:
                parent_a = rng.choice(parent_pool)
                parent_b = rng.choice(parent_pool)
                child = parent_a.orthogonal_crossover(parent_b, rng).mutate(
                    rng,
                    probability=mutation_probability,
                    scale=mutation_scale,
                )
                population.append(child)

        final_ranked = ranked
        challenger_review = evaluator.final_review(
            genome=final_ranked[0].genome,
            environment=environment,
            season_regime=season_regime,
            symbols=symbols,
            bars=bars,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            simulations=monte_carlo_simulations,
            seed=seed,
        )
        challenger = replace(final_ranked[0], status="challenger", final_review=challenger_review)
        return EvolutionRunReport(
            created_at=datetime.now(timezone.utc).isoformat(),
            symbols=symbols,
            interval=self.config.interval,
            bars_requested=bars,
            population_size=population_size,
            generations=generations,
            seed=seed,
            challenger=challenger,
            ranked_candidates=tuple(final_ranked),
            generation_history=tuple(generation_history),
            crucible_windows=evaluator.window_history,
            population_plan=population_plan,
            run_context={
                "fee_bps": fee_bps,
                "slippage_bps": slippage_bps,
                "monte_carlo_simulations": monte_carlo_simulations,
                "elite_ratio": elite_ratio,
                "environment_source": (
                    "explicit"
                    if explicit_environment
                    else "champion_anchor_sample"
                    if has_environment_anchor
                    else "default_anchor_sample"
                ),
                "season_regime_source": "explicit" if explicit_season_regime else "sampled",
                "seed_genome_source": "explicit" if explicit_seed_genome else "population_mix",
                "elite_seed_count": len(elite_seed_genomes),
                "max_leverage": environment.max_leverage,
                "safety": {
                    "places_live_orders": False,
                    "writes_challenger_only": True,
                    "max_leverage_fixed": 1,
                },
            },
        )


def score_backtest_report(
    name: str,
    multiplier: float,
    report: BacktestReport,
    baseline_return_pct: float,
    environment: EvolutionEnvironment,
    fee_bps: float,
) -> SeasonScore:
    alpha = report.total_return_pct - baseline_return_pct
    starting_equity = report.ending_equity_usdt - report.total_pnl_usdt
    realized_pct = (report.total_pnl_usdt / starting_equity) * 100 if starting_equity else 0.0
    friction_pct = _estimated_fee_friction_pct(report, fee_bps)
    drawdown_penalty = report.max_drawdown_pct**1.35
    friction_penalty = friction_pct * 2.0
    inventory_rejected = _inventory_rejected_count(report)
    rejection_penalty = min(25.0, (report.risk_rejected + inventory_rejected) * 0.05)
    no_trade_penalty = 25.0 if not report.trades else 0.0
    stop_loss_penalty = 0.0
    if environment.global_stop_loss > 0:
        allowed_drawdown = environment.global_stop_loss * 100
        if report.max_drawdown_pct > allowed_drawdown:
            stop_loss_penalty = (report.max_drawdown_pct - allowed_drawdown) * 3.0
    score = (
        (alpha * 2.0)
        + realized_pct
        - drawdown_penalty
        - friction_penalty
        - rejection_penalty
        - no_trade_penalty
        - stop_loss_penalty
    )
    return SeasonScore(
        name=name,
        aggressiveness_multiplier=multiplier,
        score=score,
        alpha_pct=alpha,
        total_return_pct=report.total_return_pct,
        baseline_return_pct=baseline_return_pct,
        realized_pnl_usdt=report.total_pnl_usdt,
        fee_friction_pct=friction_pct,
        max_drawdown_pct=report.max_drawdown_pct,
        trades=len(report.trades),
        wins=report.wins,
        win_rate=report.win_rate,
        max_consecutive_losses=report.max_consecutive_losses,
        risk_rejected=report.risk_rejected,
        inventory_event_count=len(report.inventory_events),
        inventory_rejected=inventory_rejected,
        inventory_realized_pnl_usdt=sum(event.realized_pnl_usdt for event in report.inventory_events),
    )


def ghost_dca_return_pct(candles_by_symbol: dict[str, list[Candle]], starting_equity: float) -> float:
    if not candles_by_symbol or starting_equity <= 0:
        return 0.0
    allocation = starting_equity / len(candles_by_symbol)
    ending_value = 0.0
    for candles in candles_by_symbol.values():
        if not candles:
            ending_value += allocation
            continue
        quote_per_bar = allocation / len(candles)
        units = 0.0
        spent = 0.0
        for candle in candles:
            if candle.close <= 0:
                continue
            units += quote_per_bar / candle.close
            spent += quote_per_bar
        ending_value += units * candles[-1].close + max(0.0, allocation - spent)
    return ((ending_value - starting_equity) / starting_equity) * 100


def monte_carlo_trade_review(
    report: BacktestReport,
    environment: EvolutionEnvironment,
    simulations: int = 250,
    rng: random.Random | None = None,
) -> MonteCarloReview:
    active_rng = rng or random.Random()
    simulation_count = max(1, simulations)
    pnl_values = [trade.pnl_usdt for trade in report.trades]
    starting_equity = report.ending_equity_usdt - report.total_pnl_usdt
    if not pnl_values or starting_equity <= 0:
        return MonteCarloReview(
            simulations=simulation_count,
            trade_count=len(pnl_values),
            bankruptcy_probability=0.0,
            stop_loss_breach_probability=0.0,
            return_p05_pct=0.0,
            return_p50_pct=0.0,
            return_p95_pct=0.0,
            max_drawdown_p95_pct=0.0,
        )

    returns: list[float] = []
    drawdowns: list[float] = []
    bankruptcies = 0
    stop_loss_breaches = 0
    stop_loss_drawdown_pct = environment.global_stop_loss * 100 if environment.global_stop_loss > 0 else None
    for _ in range(simulation_count):
        path = [active_rng.choice(pnl_values) for _ in pnl_values]
        final_equity, max_drawdown_pct, bankrupt = _simulate_pnl_path(starting_equity, path)
        returns.append(((final_equity - starting_equity) / starting_equity) * 100)
        drawdowns.append(max_drawdown_pct)
        if bankrupt:
            bankruptcies += 1
        if stop_loss_drawdown_pct is not None and max_drawdown_pct >= stop_loss_drawdown_pct:
            stop_loss_breaches += 1

    return MonteCarloReview(
        simulations=simulation_count,
        trade_count=len(pnl_values),
        bankruptcy_probability=bankruptcies / simulation_count,
        stop_loss_breach_probability=stop_loss_breaches / simulation_count,
        return_p05_pct=_percentile(returns, 0.05),
        return_p50_pct=_percentile(returns, 0.50),
        return_p95_pct=_percentile(returns, 0.95),
        max_drawdown_p95_pct=_percentile(drawdowns, 0.95),
    )


def evolution_output_paths(base_path: Path) -> tuple[Path, Path]:
    return base_path.with_suffix(".json"), base_path.with_suffix(".md")


def evolution_html_output_path(base_path: Path) -> Path:
    return base_path.with_suffix(".html")


def evolution_preset_sections(preset: EvolutionLabPreset | None = None) -> dict[str, Any]:
    environment = (
        preset.environment.project()
        if preset is not None and preset.environment is not None
        else EvolutionEnvironment.default()
    )
    season_regime = (
        preset.season_regime.project()
        if preset is not None and preset.season_regime is not None
        else SeasonRegime()
    )
    seed_genome = (
        preset.seed_genome.project()
        if preset is not None and preset.seed_genome is not None
        else LunarGenome.default()
    )
    genome = seed_genome.to_dict()
    defaults_used = []
    if preset is None or preset.environment is None:
        defaults_used.append("environment")
    if preset is None or preset.season_regime is None:
        defaults_used.append("season_regime")
    if preset is None or preset.seed_genome is None:
        defaults_used.append("seed_genome")
    return {
        "identity": {
            "kind": "evolution_preset",
            "version": 1,
            "defaults_used": defaults_used,
        },
        "environment": environment.to_dict(),
        "season_regime": season_regime.to_dict(),
        "macro_genes": {name: genome[name] for name in _MACRO_GENE_NAMES},
        "timing_genes": {name: genome[name] for name in _TIMING_GENE_NAMES},
        "micro_genes": {name: genome[name] for name in _MICRO_GENE_NAMES},
        "bounds": _evolution_preset_bounds(),
        "safety": _evolution_preset_safety(),
    }


def evolution_preset_template(preset: EvolutionLabPreset | None = None) -> dict[str, Any]:
    sections = evolution_preset_sections(preset)
    genome = {
        **sections["macro_genes"],
        **sections["timing_genes"],
        **sections["micro_genes"],
    }
    return {
        "version": 1,
        "environment": sections["environment"],
        "season_regime": sections["season_regime"],
        "seed_genome": genome,
        "bounds": sections["bounds"],
        "safety": sections["safety"],
    }


def render_evolution_preset_markdown(preset: EvolutionLabPreset | None = None) -> str:
    sections = evolution_preset_sections(preset)
    identity = sections["identity"]
    lines = [
        "# Evolution Lab Launch State",
        "",
        "## Identity",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| kind | {identity['kind']} |",
        f"| version | {identity['version']} |",
        f"| defaults_used | {', '.join(identity['defaults_used']) or '-'} |",
        "",
        "## Environment",
        "",
        _markdown_mapping_table(sections["environment"]),
        "",
        "## Season Regime",
        "",
        "| Season | Aggressiveness |",
        "|---|---:|",
    ]
    for row in sections["season_regime"]["seasons"]:
        lines.append(f"| {row['name']} | {float(row['aggressiveness_multiplier']):.6f} |")
    lines.extend(
        [
            f"| tick_offset_minutes | {sections['season_regime']['tick_offset_minutes']} |",
            "",
            "## Macro Genes",
            "",
            _markdown_mapping_table(sections["macro_genes"]),
            "",
            "## Timing Genes",
            "",
            _markdown_mapping_table(sections["timing_genes"]),
            "",
            "## Micro Genes",
            "",
            _markdown_mapping_table(sections["micro_genes"]),
            "",
            "## Safety",
            "",
            _markdown_mapping_table(sections["safety"]),
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def render_evolution_preset_html(preset: EvolutionLabPreset | None = None) -> str:
    sections = evolution_preset_sections(preset)
    preset_payload = evolution_preset_template(preset)
    return _render_evolution_state_html(
        title="Evolution Launch State",
        sections=sections,
        editable=True,
        preset_payload=preset_payload,
    )


def load_evolution_preset(path: Path) -> EvolutionLabPreset:
    return _evolution_preset_from_payload(_read_evolution_preset_payload(path))


def validate_evolution_preset(path: Path) -> EvolutionPresetValidation:
    payload = _read_evolution_preset_payload(path)
    preset = _evolution_preset_from_payload(payload)
    normalized = evolution_preset_template(preset)
    defaults_used = tuple(evolution_preset_sections(preset)["identity"]["defaults_used"])
    raw_values = _preset_validation_values(payload)
    normalized_values = _preset_validation_values(normalized)
    projected_fields = tuple(
        f"{name}: {raw_values[name]} -> {normalized_values[name]}"
        for name in sorted(raw_values)
        if name in normalized_values and not _preset_values_equal(raw_values[name], normalized_values[name])
    )
    warnings = []
    if defaults_used:
        warnings.append(f"defaulted_layers: {', '.join(defaults_used)}")
    if projected_fields:
        warnings.append("input_values_projected_to_legal_boxes")

    safety = payload.get("safety")
    if isinstance(safety, dict) and safety.get("places_live_orders") is not False:
        warnings.append("safety.places_live_orders ignored; evolution presets are research-only")

    raw_environment = payload.get("environment")
    if isinstance(raw_environment, dict) and raw_environment.get("max_leverage") != 1:
        warnings.append("environment.max_leverage fixed to 1 for spot-only GA epochs")

    return EvolutionPresetValidation(
        preset=preset,
        normalized=normalized,
        defaults_used=defaults_used,
        projected_fields=projected_fields,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _read_evolution_preset_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Evolution preset is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Evolution preset must be a JSON object: {path}")
    return payload


def _evolution_preset_from_payload(payload: dict[str, Any]) -> EvolutionLabPreset:
    environment = None
    if "environment" in payload:
        environment = _environment_from_payload(payload["environment"])

    season_regime = None
    if "season_regime" in payload:
        season_regime = _season_regime_from_payload(payload["season_regime"])

    seed_genome = None
    if "seed_genome" in payload:
        seed_genome = _genome_from_payload(payload["seed_genome"])
    elif "genome" in payload:
        seed_genome = _genome_from_payload(payload["genome"])

    return EvolutionLabPreset(
        environment=environment,
        season_regime=season_regime,
        seed_genome=seed_genome,
    )


def _preset_validation_values(payload: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    environment = payload.get("environment")
    if isinstance(environment, dict):
        for name in (*_ENV_FLOAT_BOUNDS.keys(), "max_leverage"):
            if name in environment:
                values[f"environment.{name}"] = environment[name]

    season_regime = payload.get("season_regime")
    if isinstance(season_regime, dict):
        if "tick_offset_minutes" in season_regime:
            values["season_regime.tick_offset_minutes"] = season_regime["tick_offset_minutes"]
        multipliers = _preset_raw_season_multipliers(season_regime)
        for index, value in enumerate(multipliers):
            if index >= len(SEASON_NAMES):
                break
            values[f"season_regime.{SEASON_NAMES[index]}.aggressiveness_multiplier"] = value

    genome = payload.get("seed_genome")
    if not isinstance(genome, dict):
        genome = payload.get("genome")
    if isinstance(genome, dict):
        for name in (*_MACRO_GENE_NAMES, *_TIMING_GENE_NAMES, *_MICRO_GENE_NAMES):
            if name in genome:
                values[f"seed_genome.{name}"] = genome[name]
    return values


def _preset_raw_season_multipliers(payload: dict[str, Any]) -> tuple[Any, ...]:
    if isinstance(payload.get("aggressiveness_multipliers"), list):
        return tuple(payload["aggressiveness_multipliers"])
    rows = payload.get("seasons")
    if not isinstance(rows, list):
        return ()
    by_name: dict[str, Any] = {}
    positional: list[Any] = []
    for row in rows:
        if not isinstance(row, dict) or "aggressiveness_multiplier" not in row:
            continue
        name = row.get("name")
        if isinstance(name, str) and name in SEASON_NAMES:
            by_name[name] = row["aggressiveness_multiplier"]
        else:
            positional.append(row["aggressiveness_multiplier"])
    if by_name:
        return tuple(by_name[name] for name in SEASON_NAMES if name in by_name)
    return tuple(positional)


def _preset_values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9)
    return left == right


def load_archive_elite_genomes(
    config: CryptoTradingConfig,
    archive_path: Path | None = None,
    limit: int = 8,
) -> tuple[LunarGenome, ...]:
    store = EvolutionArchiveStore(config, archive_path)
    records = [
        record
        for record in store.records()
        if record.get("status") in {"champion", "challenger"}
    ]
    records.sort(
        key=lambda record: (
            1 if record.get("status") == "champion" else 0,
            float(record.get("score", {}).get("score", -math.inf)),
            str(record.get("created_at") or ""),
        ),
        reverse=True,
    )

    genomes: list[LunarGenome] = []
    seen: set[str] = set()
    for record in records:
        candidate = record.get("candidate")
        if not isinstance(candidate, dict):
            continue
        try:
            genome = _genome_from_record(candidate)
        except (TypeError, ValueError):
            continue
        key = json.dumps(genome.to_dict(), sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        genomes.append(genome)
        if len(genomes) >= limit:
            break
    return tuple(genomes)


def load_archive_environment_anchor(
    config: CryptoTradingConfig,
    archive_path: Path | None = None,
) -> EvolutionEnvironment | None:
    store = EvolutionArchiveStore(config, archive_path)
    record = store.current_champion()
    if record is None:
        return None
    candidate = record.get("candidate")
    if not isinstance(candidate, dict):
        raise ValueError("Champion archive record is missing candidate details.")
    return _environment_from_record(candidate)


class EvolutionArchiveStore:
    """Local challenger/champion archive for evolution lab outputs."""

    version = 1

    def __init__(
        self,
        config: CryptoTradingConfig | None = None,
        path: Path | None = None,
    ):
        self.config = config or CryptoTradingConfig.from_env()
        self.path = path or self.default_path(self.config)

    @staticmethod
    def default_path(config: CryptoTradingConfig) -> Path:
        return config.state_dir / "evolution_archive.json"

    @staticmethod
    def default_champion_cache_path(path: Path) -> Path:
        return path.with_name("evolution_champion_cache.json")

    def save_challenger(self, report: EvolutionRunReport) -> dict[str, Any]:
        data = self.load()
        candidate_id = report.challenger.candidate_id
        existing = self.find(candidate_id, data=data)
        if existing is not None:
            return existing

        record = {
            "candidate_id": candidate_id,
            "status": "challenger",
            "created_at": report.created_at,
            "promoted_at": None,
            "retired_at": None,
            "symbols": list(report.symbols),
            "interval": report.interval,
            "bars_requested": report.bars_requested,
            "population_size": report.population_size,
            "generations": report.generations,
            "seed": report.seed,
            "score": report.challenger.score.to_dict(),
            "generation_history": [item.to_dict() for item in report.generation_history],
            "crucible_windows": [item.to_dict() for item in report.crucible_windows],
            "population_plan": (
                report.population_plan.to_dict() if report.population_plan is not None else {}
            ),
            "run_context": dict(report.run_context),
            "candidate": report.challenger.to_dict(),
        }
        data["records"].append(record)
        self.save(data)
        return record

    def promote(self, candidate_id: str) -> EvolutionPromotionResult:
        data = self.load()
        target = self.find(candidate_id, data=data)
        if target is None:
            raise KeyError(f"candidate {candidate_id} was not found in the evolution archive.")

        now = _utc_now()
        previous_champion = next(
            (
                record
                for record in data["records"]
                if record.get("candidate_id") != candidate_id
                and record.get("status") == "champion"
            ),
            None,
        )
        promotion_review = _promotion_review_record(target, previous_champion, now)
        if promotion_review is not None:
            target["promotion_review_at_promote"] = promotion_review
        retired: dict[str, Any] | None = None
        for record in data["records"]:
            if record["candidate_id"] == candidate_id:
                continue
            if record.get("status") == "champion":
                record["status"] = "retired"
                record["retired_at"] = now
                record["candidate"]["status"] = "retired"
                retired = record

        target["status"] = "champion"
        target["promoted_at"] = target.get("promoted_at") or now
        target["retired_at"] = None
        target["candidate"]["status"] = "champion"
        target["candidate"]["safety"]["requires_manual_promote"] = False
        data["champion_id"] = candidate_id
        self.save(data)
        champion_cache = self.write_champion_cache(target, cached_at=now)
        return EvolutionPromotionResult(
            candidate_id=candidate_id,
            promoted=target,
            retired=retired,
            archive_path=self.path,
            promotion_review=promotion_review,
            champion_cache_path=self.default_champion_cache_path(self.path),
            champion_cache=champion_cache,
        )

    def current_champion(self) -> dict[str, Any] | None:
        data = self.load()
        champion_id = data.get("champion_id")
        if not champion_id:
            return None
        return self.find(str(champion_id), data=data)

    def records(self) -> list[dict[str, Any]]:
        data = self.load()
        return list(data["records"])

    def write_champion_cache(
        self,
        record: dict[str, Any] | None = None,
        cached_at: str | None = None,
    ) -> dict[str, Any] | None:
        champion = record or self.current_champion()
        if champion is None:
            return None
        payload = _champion_cache_payload(
            champion,
            archive_path=self.path,
            cached_at=cached_at or _utc_now(),
        )
        cache_path = self.default_champion_cache_path(self.path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return payload

    def find(self, candidate_id: str, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
        active_data = data or self.load()
        for record in active_data["records"]:
            if record.get("candidate_id") == candidate_id:
                return record
        return None

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_archive()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Evolution archive is not valid JSON: {self.path}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise ValueError(f"Evolution archive has an invalid shape: {self.path}")
        data.setdefault("version", self.version)
        data.setdefault("champion_id", None)
        data.setdefault("updated_at", None)
        return data

    def save(self, data: dict[str, Any]) -> None:
        payload = dict(data)
        payload["version"] = self.version
        payload["updated_at"] = _utc_now()
        payload.setdefault("champion_id", None)
        payload.setdefault("records", [])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


class EvolutionRunStore:
    """Append-only-ish task ledger for front-end evolution job dashboards."""

    version = 1

    def __init__(
        self,
        config: CryptoTradingConfig | None = None,
        path: Path | None = None,
    ):
        self.config = config or CryptoTradingConfig.from_env()
        self.path = path or self.default_path(self.config)

    @staticmethod
    def default_path(config: CryptoTradingConfig) -> Path:
        return config.state_dir / "evolution_runs.json"

    def record_started(
        self,
        *,
        symbols: tuple[str, ...],
        interval: str,
        bars_requested: int,
        population_size: int,
        generations: int,
        seed: int | None,
        run_context: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        started_at = created_at or _utc_now()
        run_id = evolution_pending_run_id(
            created_at=started_at,
            symbols=symbols,
            interval=interval,
            seed=seed,
        )
        record = {
            "run_id": run_id,
            "status": "running",
            "created_at": started_at,
            "started_at": started_at,
            "completed_at": None,
            "failed_at": None,
            "symbols": list(symbols),
            "interval": interval,
            "bars_requested": bars_requested,
            "population_size": population_size,
            "generations": generations,
            "seed": seed,
            "challenger_id": None,
            "score": {},
            "final_review_warnings": [],
            "output_paths": {},
            "archive_path": None,
            "archived_candidate_id": None,
            "run_context": dict(run_context or {}),
            "error": None,
            "safety": {
                "places_live_orders": False,
                "writes_challenger_only": True,
                "max_leverage_fixed": 1,
            },
        }
        return self._upsert(record)

    def record_completed(
        self,
        report: EvolutionRunReport,
        output_paths: dict[str, str] | None = None,
        archive_path: Path | None = None,
        archived_candidate_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        data = self.load()
        active_run_id = run_id or evolution_run_id(report)
        existing = self._find(active_run_id, data=data)
        existing_context = (
            dict(existing.get("run_context", {}))
            if existing is not None and isinstance(existing.get("run_context"), dict)
            else {}
        )
        merged_context = {**existing_context, **report.run_context}
        record = {
            "run_id": active_run_id,
            "status": "completed",
            "created_at": existing.get("created_at") if existing else report.created_at,
            "started_at": existing.get("started_at") if existing else report.created_at,
            "completed_at": _utc_now(),
            "failed_at": None,
            "symbols": list(report.symbols),
            "interval": report.interval,
            "bars_requested": report.bars_requested,
            "population_size": report.population_size,
            "generations": report.generations,
            "seed": report.seed,
            "challenger_id": report.challenger.candidate_id,
            "score": report.challenger.score.to_dict(),
            "final_review_warnings": (
                list(report.challenger.final_review.warnings)
                if report.challenger.final_review is not None
                else []
            ),
            "output_paths": dict(output_paths or {}),
            "archive_path": str(archive_path) if archive_path is not None else None,
            "archived_candidate_id": archived_candidate_id,
            "run_context": merged_context,
            "error": None,
            "safety": {
                "places_live_orders": False,
                "writes_challenger_only": True,
                "max_leverage_fixed": 1,
            },
        }
        return self._upsert(record, data=data)

    def record_failed(
        self,
        run_id: str,
        error: str,
        error_type: str = "Exception",
        run_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = self.load()
        existing = self._find(run_id, data=data) or {
            "run_id": run_id,
            "created_at": _utc_now(),
            "symbols": [],
            "interval": "-",
            "bars_requested": 0,
            "population_size": 0,
            "generations": 0,
            "seed": None,
        }
        record = dict(existing)
        record.update(
            {
                "status": "failed",
                "completed_at": None,
                "failed_at": _utc_now(),
                "challenger_id": existing.get("challenger_id"),
                "score": existing.get("score") if isinstance(existing.get("score"), dict) else {},
                "final_review_warnings": existing.get("final_review_warnings", []),
                "output_paths": existing.get("output_paths", {}),
                "archive_path": existing.get("archive_path"),
                "archived_candidate_id": existing.get("archived_candidate_id"),
                "run_context": dict(run_context or existing.get("run_context") or {}),
                "error": {
                    "type": error_type,
                    "message": error,
                },
                "safety": {
                    "places_live_orders": False,
                    "writes_challenger_only": True,
                    "max_leverage_fixed": 1,
                },
            }
        )
        return self._upsert(record, data=data)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_run_ledger()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Evolution run ledger is not valid JSON: {self.path}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
            raise ValueError(f"Evolution run ledger has an invalid shape: {self.path}")
        data.setdefault("version", self.version)
        data.setdefault("updated_at", None)
        return data

    def save(self, data: dict[str, Any]) -> None:
        payload = dict(data)
        payload["version"] = self.version
        payload["updated_at"] = _utc_now()
        payload.setdefault("runs", [])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _find(self, run_id: str, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
        active_data = data or self.load()
        for record in active_data["runs"]:
            if isinstance(record, dict) and record.get("run_id") == run_id:
                return record
        return None

    def _upsert(
        self,
        record: dict[str, Any],
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        active_data = data or self.load()
        run_id = record.get("run_id")
        active_data["runs"] = [
            item for item in active_data["runs"] if not isinstance(item, dict) or item.get("run_id") != run_id
        ]
        active_data["runs"].append(record)
        self.save(active_data)
        return record


def evolution_run_id(report: EvolutionRunReport) -> str:
    raw = "|".join(
        (
            report.created_at,
            report.challenger.candidate_id,
            ",".join(report.symbols),
            report.interval,
            str(report.seed),
        )
    )
    return "evo-run-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def evolution_pending_run_id(
    *,
    created_at: str,
    symbols: tuple[str, ...],
    interval: str,
    seed: int | None,
) -> str:
    raw = "|".join((created_at, ",".join(symbols), interval, str(seed), "pending"))
    return "evo-run-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def evolution_run_ledger_path(
    config: CryptoTradingConfig,
    path: Path | None = None,
) -> Path:
    return path or EvolutionRunStore.default_path(config)


def load_runtime_champion_config(
    config: CryptoTradingConfig,
    archive_path: Path | None = None,
    season: str = "spring",
) -> RuntimeChampionApplication | None:
    """Apply the current champion to analysis/paper runtime config only.

    The champion is never allowed to loosen hard risk settings: it cannot raise
    the configured position cap, lower the configured confidence floor, or set
    leverage above 1.
    """

    if config.execution_mode not in {"analysis", "paper"}:
        raise ValueError("Evolution champion packages can only be loaded in analysis or paper mode.")

    store = EvolutionArchiveStore(config, archive_path)
    cache_path = evolution_champion_cache_path(config, store.path)
    record, parameter_source = _load_runtime_champion_record(store, cache_path)
    if record is None:
        return None

    candidate = _runtime_candidate_payload(record)
    genome = _genome_from_record(candidate)
    environment = _environment_from_record(candidate)
    multiplier = _runtime_season_multiplier(candidate, season)
    reserve_cap = max(0.0, 1.0 - environment.dead_reserve_ratio)
    genome_position_cap = genome.micro_reserve_rate * multiplier
    max_position_pct = min(config.max_position_pct, reserve_cap, genome_position_cap)
    genome_confidence_floor = _clamp(0.52 + genome.min_trade_threshold, 0.55, 0.90)
    min_confidence = max(config.min_confidence, genome_confidence_floor)
    runtime_config = replace(
        config,
        hyperliquid_max_leverage=1,
        lookback_limit=genome.t_micro,
        min_confidence=min_confidence,
        max_position_pct=max_position_pct,
    )
    return RuntimeChampionApplication(
        candidate_id=record["candidate_id"],
        archive_path=store.path,
        season=_normalize_season(season),
        aggressiveness_multiplier=multiplier,
        config=runtime_config,
        applied_settings={
            "lookback_limit": runtime_config.lookback_limit,
            "min_confidence": runtime_config.min_confidence,
            "max_position_pct": runtime_config.max_position_pct,
            "hyperliquid_max_leverage": runtime_config.hyperliquid_max_leverage,
        },
        record=record,
        parameter_source=parameter_source,
        champion_cache_path=cache_path if parameter_source == "cache" else None,
    )


def evolution_champion_cache_path(
    config: CryptoTradingConfig,
    archive_path: Path | None = None,
) -> Path:
    return EvolutionArchiveStore.default_champion_cache_path(
        archive_path or EvolutionArchiveStore.default_path(config)
    )


def evolution_record_sections(record: dict[str, Any]) -> dict[str, Any]:
    """Split one archive record into the state-window layers."""

    candidate = record.get("candidate", {})
    if not isinstance(candidate, dict):
        raise ValueError("Evolution archive record is missing candidate details.")
    genome = candidate.get("genome")
    environment = candidate.get("environment")
    season_regime = candidate.get("season_regime")
    score = record.get("score") or candidate.get("score")
    if not isinstance(genome, dict):
        raise ValueError("Evolution archive record is missing genome details.")
    if not isinstance(environment, dict):
        raise ValueError("Evolution archive record is missing environment details.")
    if not isinstance(season_regime, dict):
        raise ValueError("Evolution archive record is missing season details.")
    if not isinstance(score, dict):
        raise ValueError("Evolution archive record is missing score details.")

    return {
        "identity": {
            "candidate_id": record.get("candidate_id", candidate.get("candidate_id", "-")),
            "status": record.get("status", candidate.get("status", "-")),
            "created_at": record.get("created_at"),
            "promoted_at": record.get("promoted_at"),
            "retired_at": record.get("retired_at"),
            "symbols": record.get("symbols", []),
            "interval": record.get("interval", "-"),
            "bars_requested": record.get("bars_requested", 0),
            "population_size": record.get("population_size", 0),
            "generations": record.get("generations", 0),
            "seed": record.get("seed"),
        },
        "environment": dict(environment),
        "season_regime": dict(season_regime),
        "macro_genes": {name: genome.get(name) for name in _MACRO_GENE_NAMES},
        "timing_genes": {name: genome.get(name) for name in _TIMING_GENE_NAMES},
        "micro_genes": {name: genome.get(name) for name in _MICRO_GENE_NAMES},
        "score": dict(score),
        "generation_history": list(record.get("generation_history", [])),
        "crucible_windows": list(record.get("crucible_windows", [])),
        "population_plan": (
            dict(record.get("population_plan", {}))
            if isinstance(record.get("population_plan"), dict)
            else {}
        ),
        "run_context": (
            dict(record.get("run_context", {}))
            if isinstance(record.get("run_context"), dict)
            else {}
        ),
        "final_review": (
            dict(candidate.get("final_review", {}))
            if isinstance(candidate.get("final_review"), dict)
            else {}
        ),
        "safety": dict(candidate.get("safety", {})) if isinstance(candidate.get("safety"), dict) else {},
    }


def render_evolution_record_markdown(record: dict[str, Any]) -> str:
    sections = evolution_record_sections(record)
    identity = sections["identity"]
    score = sections["score"]
    lines = [
        "# Evolution Package",
        "",
        f"- Candidate ID: `{identity['candidate_id']}`",
        f"- Status: {identity['status']}",
        f"- Symbols: {', '.join(identity.get('symbols') or [])}",
        f"- Interval: {identity['interval']}",
        f"- Bars: {identity['bars_requested']}",
        f"- Score: {float(score.get('score', 0.0)):.4f}",
        f"- Alpha: {float(score.get('alpha_pct', 0.0)):.2f}%",
        f"- Max drawdown: {float(score.get('max_drawdown_pct', 0.0)):.2f}%",
        "",
    ]
    run_context = sections.get("run_context", {})
    if run_context:
        lines.extend(
            [
                "## Run Context",
                "",
                _markdown_mapping_table(_flatten_for_display(run_context)),
                "",
            ]
        )
    lines.extend(["## Environment", ""])
    lines.extend(_markdown_key_values(sections["environment"]))
    lines.extend(["", "## Season Regime", ""])
    for row in sections["season_regime"].get("seasons", []):
        if isinstance(row, dict):
            lines.append(
                f"- {row.get('name', '-')}: "
                f"{float(row.get('aggressiveness_multiplier', 0.0)):.2f}"
            )
    lines.append(f"- tick_offset_minutes: {sections['season_regime'].get('tick_offset_minutes', 0)}")
    lines.extend(["", "## Macro Genes", ""])
    lines.extend(_markdown_key_values(sections["macro_genes"]))
    lines.extend(["", "## Timing Genes", ""])
    lines.extend(_markdown_key_values(sections["timing_genes"]))
    lines.extend(["", "## Micro Genes", ""])
    lines.extend(_markdown_key_values(sections["micro_genes"]))
    lines.extend(["", "## Score", ""])
    for name in (
        "score",
        "alpha_pct",
        "realized_pnl_usdt",
        "fee_friction_pct",
        "max_drawdown_pct",
        "trades",
        "win_rate",
        "max_consecutive_losses",
            "risk_rejected",
            "inventory_event_count",
            "inventory_rejected",
            "inventory_realized_pnl_usdt",
    ):
        if name in score:
            lines.append(f"- {name}: {score[name]}")
    population_plan = sections.get("population_plan", {})
    if population_plan:
        lines.extend(["", "## Population Initialization", ""])
        for name in (
            "incumbent_elites",
            "targeted_mutants",
            "explorers",
            "seed_pool_size",
            "elite_seed_count",
            "has_explicit_seed_genome",
            "incumbent_ratio",
            "targeted_mutant_ratio",
            "explorer_ratio",
        ):
            if name in population_plan:
                lines.append(f"- {name}: {population_plan[name]}")
    crucible_windows = sections.get("crucible_windows", [])
    if crucible_windows:
        lines.extend(
            [
                "",
                "## Crucible Windows",
                "",
                "| Window | Weight | Bars | Start | End |",
                "|---|---:|---:|---|---|",
            ]
        )
        for item in crucible_windows:
            if isinstance(item, dict):
                lines.append(
                    "| {name} | {weight} | {bars} | {start} | {end} |".format(
                        name=item.get("name", "-"),
                        weight=item.get("weight", "-"),
                        bars=item.get("bars", "-"),
                        start=item.get("start_time") or "-",
                        end=item.get("end_time") or "-",
                    )
                )
    generation_history = sections.get("generation_history", [])
    if generation_history:
        lines.extend(["", "## Generation History", ""])
        for item in generation_history:
            if isinstance(item, dict):
                lines.append(
                    "- gen {generation}: best={best_score}, avg={average_score}, "
                    "mutation={mutation_probability}/{mutation_scale}, stale={stale_generations}".format(
                        generation=item.get("generation", "-"),
                        best_score=item.get("best_score", "-"),
                        average_score=item.get("average_score", "-"),
                        mutation_probability=item.get("mutation_probability", "-"),
                        mutation_scale=item.get("mutation_scale", "-"),
                        stale_generations=item.get("stale_generations", "-"),
                    )
                )
    final_review = sections.get("final_review", {})
    if final_review:
        lines.extend(["", "## Final Full-Sample Review", ""])
        for name in (
            "alpha_pct",
            "total_return_pct",
            "max_drawdown_pct",
            "trades",
            "win_rate",
            "risk_rejected",
        ):
            if name in final_review:
                lines.append(f"- {name}: {final_review[name]}")
        inventory_bridge = final_review.get("inventory_bridge", {})
        if isinstance(inventory_bridge, dict):
            lines.extend(["", "Inventory Bridge:"])
            for name in (
                "event_count",
                "macro_buys",
                "acceleration_unlocks",
                "stale_gc_unlocks",
                "lot_size_rejected",
                "micro_sells",
                "realized_pnl_usdt",
                "ending_dead_quantity",
                "ending_float_quantity",
                "ending_quote_balance_usdt",
            ):
                if name in inventory_bridge:
                    lines.append(f"- {name}: {inventory_bridge[name]}")
        monte_carlo = final_review.get("monte_carlo", {})
        if isinstance(monte_carlo, dict):
            lines.extend(["", "Monte Carlo:"])
            for name in (
                "simulations",
                "bankruptcy_probability",
                "stop_loss_breach_probability",
                "return_p05_pct",
                "return_p50_pct",
                "return_p95_pct",
                "max_drawdown_p95_pct",
            ):
                if name in monte_carlo:
                    lines.append(f"- {name}: {monte_carlo[name]}")
        warnings = final_review.get("warnings", [])
        if warnings:
            lines.extend(["", "Warnings:"])
            lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- This package cannot place orders by itself.",
            "- Runtime champion loading is limited to analysis and paper modes.",
            "- Hyperliquid leverage remains fixed at 1.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def render_evolution_record_html(record: dict[str, Any]) -> str:
    return _render_evolution_state_html(
        title="Evolution Package State",
        sections=evolution_record_sections(record),
        editable=False,
        preset_payload=None,
    )


def evolution_archive_sections(
    data: dict[str, Any],
    archive_path: Path | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Build a dashboard-friendly view of the local evolution archive."""

    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise ValueError("Evolution archive has an invalid shape.")
    records = list(reversed(data.get("records", [])))
    if limit is not None:
        records = records[: max(0, limit)]
    status_counts: dict[str, int] = {}
    for record in data.get("records", []):
        status = str(record.get("status", "-"))
        status_counts[status] = status_counts.get(status, 0) + 1
    rows = []
    for record in records:
        if not isinstance(record, dict):
            continue
        score = record.get("score", {})
        if not isinstance(score, dict):
            score = {}
        rows.append(
            {
                "status": record.get("status", "-"),
                "candidate_id": record.get("candidate_id", "-"),
                "symbols": list(record.get("symbols", [])),
                "interval": record.get("interval", "-"),
                "bars_requested": record.get("bars_requested", 0),
                "population_size": record.get("population_size", 0),
                "generations": record.get("generations", 0),
                "score": score.get("score", 0.0),
                "alpha_pct": score.get("alpha_pct", 0.0),
                "max_drawdown_pct": score.get("max_drawdown_pct", 0.0),
                "trades": score.get("trades", 0),
                "inventory_rejected": score.get("inventory_rejected", 0),
                "created_at": record.get("created_at"),
                "promoted_at": record.get("promoted_at"),
                "retired_at": record.get("retired_at"),
            }
        )
    return {
        "identity": {
            "version": data.get("version", EvolutionArchiveStore.version),
            "updated_at": data.get("updated_at"),
            "archive_path": str(archive_path) if archive_path is not None else "-",
            "champion_id": data.get("champion_id"),
            "record_count": len(data.get("records", [])),
            "displayed_count": len(rows),
        },
        "status_counts": status_counts,
        "records": rows,
        "safety": {
            "dashboard_is_read_only": True,
            "promote_requires_cli_command": True,
            "places_live_orders": False,
        },
    }


def render_evolution_archive_markdown(
    data: dict[str, Any],
    archive_path: Path | None = None,
    limit: int | None = None,
) -> str:
    sections = evolution_archive_sections(data, archive_path=archive_path, limit=limit)
    identity = sections["identity"]
    lines = [
        "# Evolution Archive Dashboard",
        "",
        f"- Archive: {identity['archive_path']}",
        f"- Champion: `{identity.get('champion_id') or '-'}`",
        f"- Records: {identity['record_count']}",
        f"- Displayed: {identity['displayed_count']}",
        f"- Updated: {identity.get('updated_at') or '-'}",
        "",
        "## Status Counts",
        "",
    ]
    for status, count in sorted(sections["status_counts"].items()):
        lines.append(f"- {status}: {count}")
    lines.extend(
        [
            "",
            "## Records",
            "",
            "| Status | Candidate | Symbols | Score | Alpha | Max DD | Trades | Created | Promoted |",
            "|---|---|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in sections["records"]:
        lines.append(
            "| {status} | `{candidate}` | {symbols} | {score:.4f} | {alpha:.2f}% | "
            "{drawdown:.2f}% | {trades} | {created} | {promoted} |".format(
                status=row.get("status", "-"),
                candidate=row.get("candidate_id", "-"),
                symbols=",".join(row.get("symbols", [])),
                score=float(row.get("score") or 0.0),
                alpha=float(row.get("alpha_pct") or 0.0),
                drawdown=float(row.get("max_drawdown_pct") or 0.0),
                trades=row.get("trades", 0),
                created=row.get("created_at") or "-",
                promoted=row.get("promoted_at") or "-",
            )
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- This dashboard is read-only.",
            "- Promotion still requires `crypto-evolution-promote`.",
            "- The archive dashboard cannot place orders.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def render_evolution_archive_html(
    data: dict[str, Any],
    archive_path: Path | None = None,
    limit: int | None = None,
) -> str:
    sections = evolution_archive_sections(data, archive_path=archive_path, limit=limit)
    identity = sections["identity"]
    status_rows = "".join(
        f"<tr><td>{_html_text(status)}</td><td>{_html_text(count)}</td></tr>"
        for status, count in sorted(sections["status_counts"].items())
    )
    record_rows = []
    for row in sections["records"]:
        status = str(row.get("status", "-"))
        status_class = "champion" if status == "champion" else "challenger" if status == "challenger" else "retired"
        record_rows.append(
            f'<tr class="{_html_attr(status_class)}">'
            f"<td>{_html_text(status)}</td>"
            f"<td><code>{_html_text(row.get('candidate_id', '-'))}</code></td>"
            f"<td>{_html_text(','.join(row.get('symbols', [])))}</td>"
            f"<td>{_html_text(row.get('interval', '-'))}</td>"
            f"<td>{_html_text(_format_html_value(row.get('score')))}</td>"
            f"<td>{_html_text(_format_html_value(row.get('alpha_pct')))}%</td>"
            f"<td>{_html_text(_format_html_value(row.get('max_drawdown_pct')))}%</td>"
            f"<td>{_html_text(_format_html_value(row.get('trades')))}</td>"
            f"<td>{_html_text(row.get('created_at') or '-')}</td>"
            f"<td>{_html_text(row.get('promoted_at') or '-')}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Evolution Archive Dashboard</title>
  <style>
    :root {{ color-scheme: light; --ink:#14212b; --muted:#64727f; --line:#d9e1e8; --panel:#ffffff; --band:#f4f8fb; --green:#0f766e; --yellow:#8a5a00; --dim:#7b8794; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Inter, Segoe UI, Arial, sans-serif; background:var(--band); color:var(--ink); }}
    main {{ width:min(1180px, calc(100% - 32px)); margin:0 auto; padding:28px 0 40px; }}
    header {{ display:flex; align-items:flex-end; justify-content:space-between; gap:20px; margin-bottom:18px; }}
    h1 {{ margin:0; font-size:30px; line-height:1.1; letter-spacing:0; }}
    h2 {{ margin:0 0 12px; font-size:18px; letter-spacing:0; }}
    code {{ background:#eaf1f6; padding:2px 6px; border-radius:6px; }}
    .badge {{ border:1px solid var(--line); background:#fff; color:var(--muted); padding:8px 10px; border-radius:8px; white-space:nowrap; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; box-shadow:0 1px 2px rgba(20,33,43,.04); }}
    .wide {{ grid-column:1 / -1; }}
    table {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
    th, td {{ border-bottom:1px solid var(--line); padding:9px 8px; text-align:left; vertical-align:middle; font-size:14px; overflow-wrap:anywhere; }}
    th:last-child, td:last-child {{ text-align:right; }}
    tr:last-child td {{ border-bottom:0; }}
    .champion td:first-child {{ color:var(--green); font-weight:700; }}
    .challenger td:first-child {{ color:var(--yellow); font-weight:700; }}
    .retired td:first-child {{ color:var(--dim); }}
    @media (max-width: 820px) {{ .grid {{ grid-template-columns:1fr; }} header {{ display:block; }} .badge {{ display:inline-block; margin-top:12px; }} th, td {{ font-size:13px; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Evolution Archive Dashboard</h1>
      </div>
      <div class="badge">read only | live orders disabled</div>
    </header>
    <div class="grid">
      <section class="panel">
        <h2>Archive</h2>
        <table><tbody>
          <tr><td>archive_path</td><td>{_html_text(identity.get('archive_path'))}</td></tr>
          <tr><td>champion_id</td><td>{_html_text(identity.get('champion_id') or '-')}</td></tr>
          <tr><td>record_count</td><td>{_html_text(identity.get('record_count'))}</td></tr>
          <tr><td>displayed_count</td><td>{_html_text(identity.get('displayed_count'))}</td></tr>
          <tr><td>updated_at</td><td>{_html_text(identity.get('updated_at') or '-')}</td></tr>
        </tbody></table>
      </section>
      <section class="panel">
        <h2>Status Counts</h2>
        <table><tbody>{status_rows}</tbody></table>
      </section>
      <section class="panel wide">
        <h2>Records</h2>
        <table>
          <thead>
            <tr><th>Status</th><th>Candidate</th><th>Symbols</th><th>Interval</th><th>Score</th><th>Alpha</th><th>Max DD</th><th>Trades</th><th>Created</th><th>Promoted</th></tr>
          </thead>
          <tbody>{''.join(record_rows)}</tbody>
        </table>
      </section>
    </div>
  </main>
</body>
</html>
"""


def evolution_runs_sections(
    data: dict[str, Any],
    ledger_path: Path | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Build a dashboard-friendly view of completed evolution jobs."""

    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        raise ValueError("Evolution run ledger has an invalid shape.")
    runs = list(reversed(data.get("runs", [])))
    if limit is not None:
        runs = runs[: max(0, limit)]
    status_counts: dict[str, int] = {}
    for run in data.get("runs", []):
        status = str(run.get("status", "-"))
        status_counts[status] = status_counts.get(status, 0) + 1
    rows = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        score = run.get("score", {})
        if not isinstance(score, dict):
            score = {}
        run_context = run.get("run_context", {})
        if not isinstance(run_context, dict):
            run_context = {}
        preset = run_context.get("preset", {}) if isinstance(run_context.get("preset"), dict) else {}
        output_paths = run.get("output_paths", {})
        if not isinstance(output_paths, dict):
            output_paths = {}
        error = run.get("error", {}) if isinstance(run.get("error"), dict) else {}
        rows.append(
            {
                "run_id": run.get("run_id", "-"),
                "status": run.get("status", "-"),
                "challenger_id": run.get("challenger_id", "-"),
                "symbols": list(run.get("symbols", [])),
                "interval": run.get("interval", "-"),
                "bars_requested": run.get("bars_requested", 0),
                "population_size": run.get("population_size", 0),
                "generations": run.get("generations", 0),
                "score": score.get("score", 0.0),
                "alpha_pct": score.get("alpha_pct", 0.0),
                "max_drawdown_pct": score.get("max_drawdown_pct", 0.0),
                "trades": score.get("trades", 0),
                "created_at": run.get("created_at"),
                "completed_at": run.get("completed_at"),
                "archive_path": run.get("archive_path"),
                "archived_candidate_id": run.get("archived_candidate_id"),
                "output_count": len(output_paths),
                "preset_ok": preset.get("ok"),
                "warning_count": len(run.get("final_review_warnings", []) or [])
                + len(preset.get("warnings", []) or []),
                "error_type": error.get("type"),
                "error_message": error.get("message"),
            }
        )
    return {
        "identity": {
            "version": data.get("version", EvolutionRunStore.version),
            "updated_at": data.get("updated_at"),
            "ledger_path": str(ledger_path) if ledger_path is not None else "-",
            "run_count": len(data.get("runs", [])),
            "displayed_count": len(rows),
        },
        "status_counts": status_counts,
        "runs": rows,
        "safety": {
            "dashboard_is_read_only": True,
            "places_live_orders": False,
        },
    }


def render_evolution_runs_markdown(
    data: dict[str, Any],
    ledger_path: Path | None = None,
    limit: int | None = None,
) -> str:
    sections = evolution_runs_sections(data, ledger_path=ledger_path, limit=limit)
    identity = sections["identity"]
    lines = [
        "# Evolution Jobs Dashboard",
        "",
        f"- Ledger: {identity['ledger_path']}",
        f"- Runs: {identity['run_count']}",
        f"- Displayed: {identity['displayed_count']}",
        f"- Updated: {identity.get('updated_at') or '-'}",
        "",
        "## Status Counts",
        "",
    ]
    for status, count in sorted(sections["status_counts"].items()):
        lines.append(f"- {status}: {count}")
    lines.extend(
        [
            "",
            "## Runs",
            "",
            "| Status | Run | Challenger | Symbols | Score | Alpha | Max DD | Trades | Warnings | Completed | Error |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in sections["runs"]:
        lines.append(
            "| {status} | `{run}` | `{challenger}` | {symbols} | {score:.4f} | "
            "{alpha:.2f}% | {drawdown:.2f}% | {trades} | {warnings} | {completed} | {error} |".format(
                status=row.get("status", "-"),
                run=row.get("run_id", "-"),
                challenger=row.get("challenger_id") or "-",
                symbols=",".join(row.get("symbols", [])),
                score=float(row.get("score") or 0.0),
                alpha=float(row.get("alpha_pct") or 0.0),
                drawdown=float(row.get("max_drawdown_pct") or 0.0),
                trades=row.get("trades", 0),
                warnings=row.get("warning_count", 0),
                completed=row.get("completed_at") or "-",
                error=row.get("error_message") or "-",
            )
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- This dashboard is read-only.",
            "- Jobs are historical research records only.",
            "- The job ledger cannot place orders.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def render_evolution_runs_html(
    data: dict[str, Any],
    ledger_path: Path | None = None,
    limit: int | None = None,
) -> str:
    sections = evolution_runs_sections(data, ledger_path=ledger_path, limit=limit)
    identity = sections["identity"]
    status_rows = "".join(
        f"<tr><td>{_html_text(status)}</td><td>{_html_text(count)}</td></tr>"
        for status, count in sorted(sections["status_counts"].items())
    )
    run_rows = []
    for row in sections["runs"]:
        run_rows.append(
            "<tr>"
            f"<td>{_html_text(row.get('status', '-'))}</td>"
            f"<td><code>{_html_text(row.get('run_id', '-'))}</code></td>"
            f"<td><code>{_html_text(row.get('challenger_id') or '-')}</code></td>"
            f"<td>{_html_text(','.join(row.get('symbols', [])))}</td>"
            f"<td>{_html_text(row.get('interval', '-'))}</td>"
            f"<td>{_html_text(_format_html_value(row.get('score')))}</td>"
            f"<td>{_html_text(_format_html_value(row.get('alpha_pct')))}%</td>"
            f"<td>{_html_text(_format_html_value(row.get('max_drawdown_pct')))}%</td>"
            f"<td>{_html_text(_format_html_value(row.get('trades')))}</td>"
            f"<td>{_html_text(_format_html_value(row.get('warning_count')))}</td>"
            f"<td>{_html_text(row.get('completed_at') or '-')}</td>"
            f"<td>{_html_text(row.get('error_message') or '-')}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Evolution Jobs Dashboard</title>
  <style>
    :root {{ color-scheme: light; --ink:#14212b; --muted:#64727f; --line:#d9e1e8; --panel:#ffffff; --band:#f4f8fb; --accent:#0f766e; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Inter, Segoe UI, Arial, sans-serif; background:var(--band); color:var(--ink); }}
    main {{ width:min(1180px, calc(100% - 32px)); margin:0 auto; padding:28px 0 40px; }}
    header {{ display:flex; align-items:flex-end; justify-content:space-between; gap:20px; margin-bottom:18px; }}
    h1 {{ margin:0; font-size:30px; line-height:1.1; letter-spacing:0; }}
    h2 {{ margin:0 0 12px; font-size:18px; letter-spacing:0; }}
    code {{ background:#eaf1f6; padding:2px 6px; border-radius:6px; }}
    .badge {{ border:1px solid var(--line); background:#fff; color:var(--muted); padding:8px 10px; border-radius:8px; white-space:nowrap; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; box-shadow:0 1px 2px rgba(20,33,43,.04); }}
    .wide {{ grid-column:1 / -1; }}
    table {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
    th, td {{ border-bottom:1px solid var(--line); padding:9px 8px; text-align:left; vertical-align:middle; font-size:14px; overflow-wrap:anywhere; }}
    th:last-child, td:last-child {{ text-align:right; }}
    tr:last-child td {{ border-bottom:0; }}
    @media (max-width: 820px) {{ .grid {{ grid-template-columns:1fr; }} header {{ display:block; }} .badge {{ display:inline-block; margin-top:12px; }} th, td {{ font-size:13px; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div><h1>Evolution Jobs Dashboard</h1></div>
      <div class="badge">read only | live orders disabled</div>
    </header>
    <div class="grid">
      <section class="panel">
        <h2>Ledger</h2>
        <table><tbody>
          <tr><td>ledger_path</td><td>{_html_text(identity.get('ledger_path'))}</td></tr>
          <tr><td>run_count</td><td>{_html_text(identity.get('run_count'))}</td></tr>
          <tr><td>displayed_count</td><td>{_html_text(identity.get('displayed_count'))}</td></tr>
          <tr><td>updated_at</td><td>{_html_text(identity.get('updated_at') or '-')}</td></tr>
        </tbody></table>
      </section>
      <section class="panel">
        <h2>Status Counts</h2>
        <table><tbody>{status_rows}</tbody></table>
      </section>
      <section class="panel wide">
        <h2>Runs</h2>
        <table>
          <thead>
            <tr><th>Status</th><th>Run</th><th>Challenger</th><th>Symbols</th><th>Interval</th><th>Score</th><th>Alpha</th><th>Max DD</th><th>Trades</th><th>Warnings</th><th>Completed</th><th>Error</th></tr>
          </thead>
          <tbody>{''.join(run_rows)}</tbody>
        </table>
      </section>
    </div>
  </main>
</body>
</html>
"""


def evolution_run_sections(
    run: dict[str, Any],
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    """Split one evolution job record into dashboard state panels."""

    if not isinstance(run, dict):
        raise ValueError("Evolution run record must be a JSON object.")
    score = run.get("score", {})
    if not isinstance(score, dict):
        score = {}
    output_paths = run.get("output_paths", {})
    if not isinstance(output_paths, dict):
        output_paths = {}
    run_context = run.get("run_context", {})
    if not isinstance(run_context, dict):
        run_context = {}
    error = run.get("error", {})
    if not isinstance(error, dict):
        error = {}
    warnings = run.get("final_review_warnings", [])
    if not isinstance(warnings, list):
        warnings = []
    return {
        "identity": {
            "run_id": run.get("run_id", "-"),
            "status": run.get("status", "-"),
            "ledger_path": str(ledger_path) if ledger_path is not None else "-",
            "created_at": run.get("created_at"),
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
            "failed_at": run.get("failed_at"),
            "symbols": list(run.get("symbols", [])),
            "interval": run.get("interval", "-"),
            "bars_requested": run.get("bars_requested", 0),
            "population_size": run.get("population_size", 0),
            "generations": run.get("generations", 0),
            "seed": run.get("seed"),
            "challenger_id": run.get("challenger_id"),
        },
        "score": dict(score),
        "outputs": dict(output_paths),
        "archive": {
            "archive_path": run.get("archive_path"),
            "archived_candidate_id": run.get("archived_candidate_id"),
        },
        "run_context": dict(run_context),
        "final_review_warnings": list(warnings),
        "error": {
            "type": error.get("type"),
            "message": error.get("message"),
        },
        "safety": dict(run.get("safety", {})) if isinstance(run.get("safety"), dict) else {},
    }


def render_evolution_run_markdown(
    run: dict[str, Any],
    ledger_path: Path | None = None,
) -> str:
    sections = evolution_run_sections(run, ledger_path=ledger_path)
    identity = sections["identity"]
    lines = [
        "# Evolution Job State",
        "",
        f"- Run ID: `{identity.get('run_id')}`",
        f"- Status: {identity.get('status')}",
        f"- Ledger: {identity.get('ledger_path')}",
        f"- Challenger: `{identity.get('challenger_id') or '-'}`",
        f"- Symbols: {', '.join(identity.get('symbols') or [])}",
        f"- Interval: {identity.get('interval')}",
        f"- Started: {identity.get('started_at') or '-'}",
        f"- Completed: {identity.get('completed_at') or '-'}",
        f"- Failed: {identity.get('failed_at') or '-'}",
        "",
        "## Launch",
        "",
        _markdown_mapping_table(
            {
                "bars_requested": identity.get("bars_requested"),
                "population_size": identity.get("population_size"),
                "generations": identity.get("generations"),
                "seed": identity.get("seed"),
            }
        ),
    ]
    if sections["score"]:
        lines.extend(["", "## Score", "", _markdown_mapping_table(sections["score"])])
    if sections["outputs"]:
        lines.extend(["", "## Outputs", ""])
        lines.extend(_markdown_key_values(sections["outputs"]))
    if sections["archive"]:
        lines.extend(["", "## Archive", ""])
        lines.extend(_markdown_key_values(sections["archive"]))
    if sections["run_context"]:
        lines.extend(
            [
                "",
                "## Run Context",
                "",
                _markdown_mapping_table(_flatten_for_display(sections["run_context"])),
            ]
        )
    if sections["final_review_warnings"]:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in sections["final_review_warnings"])
    if sections["error"].get("type") or sections["error"].get("message"):
        lines.extend(["", "## Error", ""])
        lines.extend(_markdown_key_values(sections["error"]))
    lines.extend(
        [
            "",
            "## Safety",
            "",
            _markdown_mapping_table(sections["safety"]),
        ]
    )
    return "\n".join(lines).strip() + "\n"


def render_evolution_run_html(
    run: dict[str, Any],
    ledger_path: Path | None = None,
) -> str:
    sections = evolution_run_sections(run, ledger_path=ledger_path)
    identity = dict(sections["identity"])
    identity["symbols"] = ",".join(identity.get("symbols") or [])
    panels = [
        _html_readonly_panel("Identity", identity),
        _html_readonly_panel("Launch", {
            "bars_requested": identity.get("bars_requested"),
            "population_size": identity.get("population_size"),
            "generations": identity.get("generations"),
            "seed": identity.get("seed"),
        }),
    ]
    if sections["score"]:
        panels.append(_html_readonly_panel("Score", sections["score"]))
    if sections["outputs"]:
        panels.append(_html_readonly_panel("Outputs", sections["outputs"]))
    if sections["archive"]:
        panels.append(_html_readonly_panel("Archive", sections["archive"]))
    if sections["run_context"]:
        panels.append(_html_readonly_panel("Run Context", _flatten_for_display(sections["run_context"])))
    if sections["final_review_warnings"]:
        panels.append(
            _html_readonly_panel(
                "Warnings",
                {"items": json.dumps(sections["final_review_warnings"], ensure_ascii=False)},
            )
        )
    if sections["error"].get("type") or sections["error"].get("message"):
        panels.append(_html_readonly_panel("Error", sections["error"]))
    panels.append(_html_readonly_panel("Safety", sections["safety"]))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Evolution Job State</title>
  <style>
    :root {{ color-scheme: light; --ink:#14212b; --muted:#64727f; --line:#d9e1e8; --panel:#ffffff; --band:#f4f8fb; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Inter, Segoe UI, Arial, sans-serif; background:var(--band); color:var(--ink); }}
    main {{ width:min(1180px, calc(100% - 32px)); margin:0 auto; padding:28px 0 40px; }}
    header {{ display:flex; align-items:flex-end; justify-content:space-between; gap:20px; margin-bottom:18px; }}
    h1 {{ margin:0; font-size:30px; line-height:1.1; letter-spacing:0; }}
    h2 {{ margin:0 0 12px; font-size:18px; letter-spacing:0; }}
    code {{ background:#eaf1f6; padding:2px 6px; border-radius:6px; }}
    .badge {{ border:1px solid var(--line); background:#fff; color:var(--muted); padding:8px 10px; border-radius:8px; white-space:nowrap; }}
    .grid {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:14px; }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; box-shadow:0 1px 2px rgba(20,33,43,.04); }}
    table {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
    td {{ border-bottom:1px solid var(--line); padding:9px 8px; text-align:left; vertical-align:middle; font-size:14px; overflow-wrap:anywhere; }}
    td:last-child {{ text-align:right; }}
    tr:last-child td {{ border-bottom:0; }}
    @media (max-width: 820px) {{ .grid {{ grid-template-columns:1fr; }} header {{ display:block; }} .badge {{ display:inline-block; margin-top:12px; }} td {{ font-size:13px; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div><h1>Evolution Job State</h1></div>
      <div class="badge">read only | live orders disabled</div>
    </header>
    <div class="grid">
      {''.join(panels)}
    </div>
  </main>
</body>
</html>
"""


def evolution_compare_sections(
    candidate_record: dict[str, Any],
    baseline_record: dict[str, Any],
) -> dict[str, Any]:
    """Compare one candidate against a baseline package, usually the champion."""

    candidate = evolution_record_sections(candidate_record)
    baseline = evolution_record_sections(baseline_record)
    score_delta = _compare_values(
        candidate["score"],
        baseline["score"],
        fields=(
            "score",
            "alpha_pct",
            "realized_pnl_usdt",
            "fee_friction_pct",
            "max_drawdown_pct",
            "trades",
            "win_rate",
            "risk_rejected",
            "inventory_rejected",
            "inventory_realized_pnl_usdt",
        ),
    )
    final_review_delta = _compare_values(
        candidate.get("final_review", {}),
        baseline.get("final_review", {}),
        fields=(
            "alpha_pct",
            "total_return_pct",
            "max_drawdown_pct",
            "trades",
            "win_rate",
            "risk_rejected",
        ),
    )
    return {
        "identity": {
            "candidate_id": candidate["identity"].get("candidate_id"),
            "candidate_status": candidate["identity"].get("status"),
            "baseline_id": baseline["identity"].get("candidate_id"),
            "baseline_status": baseline["identity"].get("status"),
            "symbols": candidate["identity"].get("symbols", []),
            "interval": candidate["identity"].get("interval", "-"),
        },
        "promotion_review": _promotion_review(candidate, baseline),
        "score_delta": score_delta,
        "environment_delta": _compare_values(candidate["environment"], baseline["environment"]),
        "season_delta": _compare_values(
            _season_compare_payload(candidate["season_regime"]),
            _season_compare_payload(baseline["season_regime"]),
        ),
        "macro_gene_delta": _compare_values(candidate["macro_genes"], baseline["macro_genes"]),
        "timing_gene_delta": _compare_values(candidate["timing_genes"], baseline["timing_genes"]),
        "micro_gene_delta": _compare_values(candidate["micro_genes"], baseline["micro_genes"]),
        "final_review_delta": final_review_delta,
        "safety": {
            "comparison_is_read_only": True,
            "promote_requires_cli_command": True,
            "places_live_orders": False,
            "delta_direction": "candidate_minus_baseline",
        },
    }


def render_evolution_compare_markdown(
    candidate_record: dict[str, Any],
    baseline_record: dict[str, Any],
) -> str:
    sections = evolution_compare_sections(candidate_record, baseline_record)
    identity = sections["identity"]
    lines = [
        "# Evolution Package Comparison",
        "",
        f"- Candidate: `{identity.get('candidate_id')}` ({identity.get('candidate_status')})",
        f"- Baseline: `{identity.get('baseline_id')}` ({identity.get('baseline_status')})",
        f"- Symbols: {', '.join(identity.get('symbols') or [])}",
        f"- Interval: {identity.get('interval')}",
        "- Delta direction: candidate minus baseline",
        "",
    ]
    review = sections.get("promotion_review", {})
    if isinstance(review, dict) and review:
        lines.extend(
            [
                "## Promotion Review",
                "",
                f"- Verdict: {review.get('verdict', '-')}",
                f"- Blocks promotion: {review.get('blocks_promotion', False)}",
                f"- Score delta: {_format_compare_value(review.get('score_delta'))}",
                f"- Alpha delta: {_format_compare_value(review.get('alpha_delta_pct'))}",
                f"- Max drawdown delta: {_format_compare_value(review.get('max_drawdown_delta_pct'))}",
                f"- Fee friction delta: {_format_compare_value(review.get('fee_friction_delta_pct'))}",
                "",
                "Reasons:",
            ]
        )
        lines.extend(f"- {reason}" for reason in review.get("reasons", []))
    for title, key in (
        ("Score Delta", "score_delta"),
        ("Environment Delta", "environment_delta"),
        ("Season Delta", "season_delta"),
        ("Macro Gene Delta", "macro_gene_delta"),
        ("Timing Gene Delta", "timing_gene_delta"),
        ("Micro Gene Delta", "micro_gene_delta"),
        ("Final Review Delta", "final_review_delta"),
    ):
        rows = sections.get(key, [])
        if rows:
            lines.extend(["", f"## {title}", "", _markdown_compare_table(rows)])
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- This comparison is read-only.",
            "- Promotion still requires `crypto-evolution-promote`.",
            "- The comparison report cannot place orders.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def render_evolution_compare_html(
    candidate_record: dict[str, Any],
    baseline_record: dict[str, Any],
) -> str:
    sections = evolution_compare_sections(candidate_record, baseline_record)
    identity = sections["identity"]
    review = sections.get("promotion_review", {})
    review_panel = _html_promotion_review(review) if isinstance(review, dict) and review else ""
    panels = []
    for title, key in (
        ("Score Delta", "score_delta"),
        ("Environment Delta", "environment_delta"),
        ("Season Delta", "season_delta"),
        ("Macro Gene Delta", "macro_gene_delta"),
        ("Timing Gene Delta", "timing_gene_delta"),
        ("Micro Gene Delta", "micro_gene_delta"),
        ("Final Review Delta", "final_review_delta"),
    ):
        rows = sections.get(key, [])
        if rows:
            panels.append(_html_compare_panel(title, rows))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Evolution Package Comparison</title>
  <style>
    :root {{ color-scheme: light; --ink:#14212b; --muted:#64727f; --line:#d9e1e8; --panel:#ffffff; --band:#f4f8fb; --green:#0f766e; --red:#b42318; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Inter, Segoe UI, Arial, sans-serif; background:var(--band); color:var(--ink); }}
    main {{ width:min(1180px, calc(100% - 32px)); margin:0 auto; padding:28px 0 40px; }}
    header {{ display:flex; align-items:flex-end; justify-content:space-between; gap:20px; margin-bottom:18px; }}
    h1 {{ margin:0; font-size:30px; line-height:1.1; letter-spacing:0; }}
    h2 {{ margin:0 0 12px; font-size:18px; letter-spacing:0; }}
    code {{ background:#eaf1f6; padding:2px 6px; border-radius:6px; }}
    .badge {{ border:1px solid var(--line); background:#fff; color:var(--muted); padding:8px 10px; border-radius:8px; white-space:nowrap; }}
    .grid {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:14px; }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; box-shadow:0 1px 2px rgba(20,33,43,.04); }}
    .wide {{ grid-column:1 / -1; }}
    table {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
    th, td {{ border-bottom:1px solid var(--line); padding:9px 8px; text-align:left; vertical-align:middle; font-size:14px; overflow-wrap:anywhere; }}
    th:last-child, td:last-child {{ text-align:right; }}
    tr:last-child td {{ border-bottom:0; }}
    .positive {{ color:var(--green); font-weight:700; }}
    .negative {{ color:var(--red); font-weight:700; }}
    @media (max-width: 820px) {{ .grid {{ grid-template-columns:1fr; }} header {{ display:block; }} .badge {{ display:inline-block; margin-top:12px; }} th, td {{ font-size:13px; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Evolution Package Comparison</h1>
      </div>
      <div class="badge">read only | candidate minus baseline</div>
    </header>
    <section class="panel wide">
      <h2>Identity</h2>
      <table><tbody>
        <tr><td>candidate</td><td><code>{_html_text(identity.get('candidate_id'))}</code></td></tr>
        <tr><td>candidate_status</td><td>{_html_text(identity.get('candidate_status'))}</td></tr>
        <tr><td>baseline</td><td><code>{_html_text(identity.get('baseline_id'))}</code></td></tr>
        <tr><td>baseline_status</td><td>{_html_text(identity.get('baseline_status'))}</td></tr>
        <tr><td>symbols</td><td>{_html_text(','.join(identity.get('symbols') or []))}</td></tr>
        <tr><td>interval</td><td>{_html_text(identity.get('interval'))}</td></tr>
      </tbody></table>
    </section>
    {review_panel}
    <div class="grid">{''.join(panels)}</div>
  </main>
</body>
</html>
"""


def _initial_population(
    population_size: int,
    rng: random.Random,
    seed_genome: LunarGenome | None = None,
    elite_seed_genomes: tuple[LunarGenome, ...] = (),
) -> tuple[list[LunarGenome], EvolutionPopulationPlan]:
    incumbent_count = max(1, math.ceil(population_size * 0.10))
    mutant_count = math.ceil(population_size * 0.40)
    population: list[LunarGenome] = []
    seed_pool = _seed_pool(seed_genome, elite_seed_genomes)
    for _ in range(incumbent_count):
        population.append(seed_pool[len(population) % len(seed_pool)])
    for _ in range(mutant_count):
        parent = rng.choice(seed_pool)
        population.append(parent.mutate(rng, probability=0.90, scale=1.5))
    while len(population) < population_size:
        population.append(LunarGenome.random(rng))
    population = population[:population_size]
    plan = EvolutionPopulationPlan(
        population_size=population_size,
        incumbent_elites=min(incumbent_count, len(population)),
        targeted_mutants=min(mutant_count, max(0, len(population) - incumbent_count)),
        explorers=max(0, len(population) - incumbent_count - mutant_count),
        seed_pool_size=len(seed_pool),
        elite_seed_count=len(elite_seed_genomes),
        has_explicit_seed_genome=seed_genome is not None,
    )
    return population, plan


def _seed_pool(
    seed_genome: LunarGenome | None,
    elite_seed_genomes: tuple[LunarGenome, ...],
) -> tuple[LunarGenome, ...]:
    candidates: list[LunarGenome] = []
    if seed_genome is not None:
        candidates.append(seed_genome.project())
    candidates.extend(genome.project() for genome in elite_seed_genomes)
    candidates.append((seed_genome or LunarGenome.default()).project())

    unique: list[LunarGenome] = []
    seen: set[str] = set()
    for genome in candidates:
        key = json.dumps(genome.to_dict(), sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(genome)
    return tuple(unique or (LunarGenome.default(),))


def _rank_population(
    population: list[LunarGenome],
    evaluator: CryptoGenomeEvaluator,
    environment: EvolutionEnvironment,
    season_regime: SeasonRegime,
    symbols: tuple[str, ...],
    bars: int,
    fee_bps: float,
    slippage_bps: float,
) -> list[EvolutionCandidate]:
    candidates: list[EvolutionCandidate] = []
    for genome in population:
        score = evaluator.evaluate(
            genome=genome,
            environment=environment,
            season_regime=season_regime,
            symbols=symbols,
            bars=bars,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        candidates.append(
            EvolutionCandidate(
                candidate_id=_candidate_id(genome, score),
                status="evaluated",
                genome=genome,
                environment=environment,
                season_regime=season_regime,
                score=score,
            )
        )
    return sorted(candidates, key=lambda item: item.score.score, reverse=True)


def _generation_stat(
    generation: int,
    ranked: list[EvolutionCandidate],
    elite_count: int,
    mutation_probability: float,
    mutation_scale: float,
    stale_generations: int,
    improved: bool,
) -> EvolutionGenerationStat:
    best = ranked[0]
    average_score = sum(candidate.score.score for candidate in ranked) / len(ranked)
    return EvolutionGenerationStat(
        generation=generation,
        best_candidate_id=best.candidate_id,
        best_score=best.score.score,
        average_score=average_score,
        best_alpha_pct=best.score.alpha_pct,
        best_max_drawdown_pct=best.score.max_drawdown_pct,
        best_trades=best.score.trades,
        elite_count=elite_count,
        mutation_probability=mutation_probability,
        mutation_scale=mutation_scale,
        stale_generations=stale_generations,
        improved=improved,
    )


def _config_from_genome(
    config: CryptoTradingConfig,
    genome: LunarGenome,
    environment: EvolutionEnvironment,
    aggressiveness_multiplier: float,
) -> CryptoTradingConfig:
    reserve_cap = max(0.0, 1.0 - environment.dead_reserve_ratio)
    max_position_pct = min(reserve_cap, genome.micro_reserve_rate * aggressiveness_multiplier)
    min_confidence = _clamp(0.52 + genome.min_trade_threshold, 0.55, 0.90)
    return replace(
        config,
        hyperliquid_max_leverage=1,
        lookback_limit=genome.t_micro,
        min_confidence=min_confidence,
        max_position_pct=max_position_pct,
        strategy_fusion_enabled=True,
        lana_strategy_enabled=False,
        hotlist_enabled=False,
    )


def _inventory_bridge_from_genome(
    genome: LunarGenome,
    environment: EvolutionEnvironment,
    aggressiveness_multiplier: float,
) -> BacktestInventoryBridgeConfig:
    sensitivity = max(0.25, genome.ka)
    return BacktestInventoryBridgeConfig(
        dead_reserve_ratio=environment.dead_reserve_ratio,
        max_dca_slices=genome.max_dca_months,
        unlock_acceleration_threshold=max(0.0004, 0.008 / (1.0 + sensitivity)),
        sell_ratio=_clamp(genome.micro_reserve_rate * aggressiveness_multiplier * 2.0, 0.05, 1.0),
        macro_tick_bars=genome.t_macro,
        ema_anchor_bars=genome.ema_anchor,
        beta_threshold=genome.beta_threshold,
        moon_phase_pressure=genome.moon_phase_pressure,
        deadline_force_pct=genome.deadline_force_pct,
        gc_threshold_bars=genome.gc_threshold_months * max(1, genome.t_macro),
        gc_max_ratio=genome.gc_max_ratio,
    )


def _inventory_bridge_stats(report: BacktestReport) -> InventoryBridgeStats:
    events = report.inventory_events
    last_event = events[-1] if events else None
    return InventoryBridgeStats(
        event_count=len(events),
        macro_buys=sum(1 for event in events if event.event == "MACRO_DCA_BUY"),
        acceleration_unlocks=sum(1 for event in events if event.event == "ACCELERATION_UNLOCK"),
        stale_gc_unlocks=sum(1 for event in events if event.event == "GC_UNLOCK"),
        lot_size_rejected=sum(1 for event in events if event.event == "LOT_SIZE_REJECTED"),
        micro_sells=sum(1 for event in events if event.event == "MICRO_SELL_FLOAT"),
        realized_pnl_usdt=sum(event.realized_pnl_usdt for event in events),
        ending_dead_quantity=last_event.dead_quantity if last_event is not None else 0.0,
        ending_float_quantity=last_event.float_quantity if last_event is not None else 0.0,
        ending_quote_balance_usdt=last_event.quote_balance_usdt if last_event is not None else 0.0,
    )


def _inventory_rejected_count(report: BacktestReport) -> int:
    return sum(1 for event in report.inventory_events if event.event == "LOT_SIZE_REJECTED")


def _combine_scores(scores: tuple[SeasonScore, ...]) -> GenomeScore:
    return _combine_weighted_scores(tuple((score, 1.0) for score in scores))


def _combine_weighted_scores(weighted_scores: tuple[tuple[SeasonScore, float], ...]) -> GenomeScore:
    scores = tuple(score for score, _ in weighted_scores)
    if not scores:
        return GenomeScore(
            score=-math.inf,
            alpha_pct=0.0,
            realized_pnl_usdt=0.0,
            fee_friction_pct=0.0,
            max_drawdown_pct=0.0,
            trades=0,
            win_rate=0.0,
            max_consecutive_losses=0,
            risk_rejected=0,
            inventory_event_count=0,
            inventory_rejected=0,
            inventory_realized_pnl_usdt=0.0,
            seasons=(),
        )
    total_weight = sum(max(0.0, weight) for _, weight in weighted_scores) or 1.0

    def weighted_average(getter) -> float:
        return sum(getter(score) * max(0.0, weight) for score, weight in weighted_scores) / total_weight

    trades = sum(item.trades for item in scores)
    wins = sum(item.wins for item in scores)
    return GenomeScore(
        score=weighted_average(lambda item: item.score),
        alpha_pct=weighted_average(lambda item: item.alpha_pct),
        realized_pnl_usdt=weighted_average(lambda item: item.realized_pnl_usdt),
        fee_friction_pct=weighted_average(lambda item: item.fee_friction_pct),
        max_drawdown_pct=max(item.max_drawdown_pct for item in scores),
        trades=trades,
        win_rate=(wins / trades) if trades else 0.0,
        max_consecutive_losses=max(item.max_consecutive_losses for item in scores),
        risk_rejected=sum(item.risk_rejected for item in scores),
        inventory_event_count=sum(item.inventory_event_count for item in scores),
        inventory_rejected=sum(item.inventory_rejected for item in scores),
        inventory_realized_pnl_usdt=sum(item.inventory_realized_pnl_usdt for item in scores),
        seasons=scores,
    )


def _crucible_window_sets(
    candles_by_symbol: dict[str, list[Candle]],
    min_bars: int,
    rng: random.Random | None = None,
    sampled_windows: int = 2,
) -> tuple[tuple[str, float, dict[str, list[Candle]]], ...]:
    if not candles_by_symbol:
        return ()
    shortest = min((len(candles) for candles in candles_by_symbol.values()), default=0)
    if shortest <= 0:
        return ()
    windows: list[tuple[str, float, dict[str, list[Candle]]]] = []
    seen_lengths: set[int] = set()
    for name, ratio, weight in CRUCIBLE_WINDOWS:
        length = min(shortest, max(min_bars, int(shortest * ratio)))
        if length <= 0 or length in seen_lengths:
            continue
        seen_lengths.add(length)
        windows.append(
            (
                name,
                weight,
                {
                    symbol: candles[-length:]
                    for symbol, candles in candles_by_symbol.items()
                    if candles
                },
            )
        )
    if rng is not None and shortest > min_bars:
        attempts = 0
        while len([item for item in windows if item[0].startswith("sampled_")]) < sampled_windows:
            attempts += 1
            if attempts > sampled_windows * 10:
                break
            length = rng.randint(min_bars, shortest - 1)
            if length in seen_lengths:
                continue
            start = rng.randint(0, shortest - length)
            seen_lengths.add(length)
            sample_index = len([item for item in windows if item[0].startswith("sampled_")]) + 1
            windows.append(
                (
                    f"sampled_{sample_index}",
                    0.10,
                    {
                        symbol: candles[start : start + length]
                        for symbol, candles in candles_by_symbol.items()
                        if candles
                    },
                )
            )
    if not windows:
        windows.append(("all", 1.0, candles_by_symbol))
    return tuple(windows)


def _slice_season(
    candles_by_symbol: dict[str, list[Candle]],
    season_index: int,
) -> dict[str, list[Candle]]:
    sliced: dict[str, list[Candle]] = {}
    for symbol, candles in candles_by_symbol.items():
        if not candles:
            sliced[symbol] = []
            continue
        segment_size = max(1, len(candles) // 4)
        start = season_index * segment_size
        end = len(candles) if season_index == 3 else min(len(candles), start + segment_size)
        sliced[symbol] = candles[start:end]
    return sliced


def _candle_snapshot_key(
    candles_by_symbol: dict[str, list[Candle]],
) -> tuple[tuple[str, int, int, int], ...]:
    key_parts: list[tuple[str, int, int, int]] = []
    for symbol, candles in sorted(candles_by_symbol.items()):
        first = candles[0].open_time_ms if candles else 0
        last = candles[-1].close_time_ms if candles else 0
        key_parts.append((symbol, len(candles), first, last))
    return tuple(key_parts)


def _evolution_window_stat(
    name: str,
    weight: float,
    candles_by_symbol: dict[str, list[Candle]],
) -> EvolutionWindowStat:
    populated = [candles for candles in candles_by_symbol.values() if candles]
    if not populated:
        return EvolutionWindowStat(
            name=name,
            weight=weight,
            bars=0,
            start_time=None,
            end_time=None,
        )
    bars = min(len(candles) for candles in populated)
    start_ms = max(candles[0].open_time_ms for candles in populated)
    end_ms = min(candles[-1].close_time_ms for candles in populated)
    return EvolutionWindowStat(
        name=name,
        weight=weight,
        bars=bars,
        start_time=_millis_to_iso(start_ms),
        end_time=_millis_to_iso(end_ms),
    )


def _millis_to_iso(timestamp_ms: int | None) -> str | None:
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


def _estimated_fee_friction_pct(report: BacktestReport, fee_bps: float) -> float:
    if not report.trades:
        return 0.0
    starting_equity = report.ending_equity_usdt - report.total_pnl_usdt
    if starting_equity <= 0:
        return 0.0
    notional = sum(trade.notional_usdt for trade in report.trades)
    estimated_fees = notional * 2 * (fee_bps / 10_000)
    return (estimated_fees / starting_equity) * 100


def _simulate_pnl_path(starting_equity: float, pnl_values: list[float]) -> tuple[float, float, bool]:
    equity = starting_equity
    peak = starting_equity
    max_drawdown_pct = 0.0
    bankrupt = False
    for pnl in pnl_values:
        equity += pnl
        if equity <= 0:
            bankrupt = True
            equity = 0.0
            max_drawdown_pct = 100.0
            break
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, ((peak - equity) / peak) * 100)
    return equity, max_drawdown_pct, bankrupt


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * _clamp(percentile, 0.0, 1.0)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[int(index)]
    weight = index - lower
    return (ordered[lower] * (1 - weight)) + (ordered[upper] * weight)


def _final_review_warnings(
    report: BacktestReport,
    monte_carlo: MonteCarloReview,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if not report.trades:
        warnings.append("final_review_no_trades")
    if monte_carlo.bankruptcy_probability > 0:
        warnings.append("monte_carlo_bankruptcy_paths_detected")
    if monte_carlo.stop_loss_breach_probability > 0.10:
        warnings.append("monte_carlo_stop_loss_breach_probability_above_10pct")
    if monte_carlo.return_p05_pct < 0:
        warnings.append("monte_carlo_p05_return_negative")
    return tuple(warnings)


def _candidate_id(genome: LunarGenome, score: GenomeScore) -> str:
    payload = {
        "genome": genome.to_dict(),
        "score": round(score.score, 8),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "challenger-" + hashlib.sha256(raw).hexdigest()[:12]


def _promotion_review_record(
    target: dict[str, Any],
    baseline: dict[str, Any] | None,
    reviewed_at: str,
) -> dict[str, Any] | None:
    if baseline is None:
        return None
    try:
        sections = evolution_compare_sections(target, baseline)
        review = dict(sections["promotion_review"])
        review["baseline_candidate_id"] = baseline.get("candidate_id")
        review["candidate_id"] = target.get("candidate_id")
        review["reviewed_at"] = reviewed_at
        return review
    except ValueError as exc:
        return {
            "verdict": "review",
            "blocks_promotion": False,
            "candidate_id": target.get("candidate_id"),
            "baseline_candidate_id": baseline.get("candidate_id"),
            "reviewed_at": reviewed_at,
            "reasons": [f"promotion_review_unavailable:{exc}"],
        }


def _champion_cache_payload(
    record: dict[str, Any],
    archive_path: Path,
    cached_at: str,
) -> dict[str, Any]:
    candidate = record.get("candidate", {})
    if not isinstance(candidate, dict):
        raise ValueError("Champion record is missing candidate details.")
    return {
        "version": 1,
        "cached_at": cached_at,
        "archive_path": str(archive_path),
        "candidate_id": record.get("candidate_id"),
        "status": record.get("status"),
        "promoted_at": record.get("promoted_at"),
        "symbols": list(record.get("symbols", [])),
        "interval": record.get("interval"),
        "score": dict(record.get("score", {})) if isinstance(record.get("score"), dict) else {},
        "environment": dict(candidate.get("environment", {}))
        if isinstance(candidate.get("environment"), dict)
        else {},
        "season_regime": dict(candidate.get("season_regime", {}))
        if isinstance(candidate.get("season_regime"), dict)
        else {},
        "genome": dict(candidate.get("genome", {})) if isinstance(candidate.get("genome"), dict) else {},
        "promotion_review_at_promote": dict(record.get("promotion_review_at_promote", {}))
        if isinstance(record.get("promotion_review_at_promote"), dict)
        else {},
        "safety": {
            "cache_is_read_only": True,
            "places_live_orders": False,
            "max_leverage": 1,
            "runtime_modes_allowed": ["analysis", "paper"],
            "live_requires_separate_execution_confirmation": True,
        },
    }


def _load_runtime_champion_record(
    store: EvolutionArchiveStore,
    cache_path: Path,
) -> tuple[dict[str, Any] | None, str]:
    cache_record = _load_champion_cache_record(cache_path)
    try:
        archive_data = store.load()
    except ValueError:
        archive_data = _empty_archive()
    archive_champion_id = archive_data.get("champion_id")
    if cache_record is not None:
        if not archive_champion_id or archive_champion_id == cache_record.get("candidate_id"):
            return cache_record, "cache"
    archive_record = _current_champion_from_archive_data(archive_data)
    if archive_record is not None:
        return archive_record, "archive"
    return cache_record, "cache" if cache_record is not None else "archive"


def _current_champion_from_archive_data(data: dict[str, Any]) -> dict[str, Any] | None:
    champion_id = data.get("champion_id")
    records = data.get("records", [])
    if not champion_id or not isinstance(records, list):
        return None
    for record in records:
        if isinstance(record, dict) and record.get("candidate_id") == str(champion_id):
            return record
    return None


def _load_champion_cache_record(cache_path: Path) -> dict[str, Any] | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    candidate_id = payload.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        return None
    safety = payload.get("safety", {})
    if not isinstance(safety, dict) or safety.get("places_live_orders") is not False:
        return None
    if payload.get("status") != "champion":
        return None
    if not isinstance(payload.get("genome"), dict):
        return None
    if not isinstance(payload.get("environment"), dict):
        return None
    if not isinstance(payload.get("season_regime"), dict):
        return None
    return {
        "candidate_id": candidate_id,
        "status": "champion",
        "created_at": payload.get("created_at"),
        "promoted_at": payload.get("promoted_at"),
        "retired_at": None,
        "symbols": list(payload.get("symbols", [])),
        "interval": payload.get("interval"),
        "score": dict(payload.get("score", {})) if isinstance(payload.get("score"), dict) else {},
        "promotion_review_at_promote": (
            dict(payload.get("promotion_review_at_promote", {}))
            if isinstance(payload.get("promotion_review_at_promote"), dict)
            else {}
        ),
        "candidate": {
            "candidate_id": candidate_id,
            "status": "champion",
            "genome": dict(payload.get("genome", {})),
            "environment": dict(payload.get("environment", {})),
            "season_regime": dict(payload.get("season_regime", {})),
            "score": dict(payload.get("score", {})) if isinstance(payload.get("score"), dict) else {},
            "safety": {
                "mode": "runtime_cache",
                "places_live_orders": False,
                "requires_manual_promote": False,
                "max_leverage": 1,
            },
        },
        "cache": payload,
    }


def _runtime_candidate_payload(record: dict[str, Any]) -> dict[str, Any]:
    candidate = record.get("candidate", {})
    if not isinstance(candidate, dict):
        raise ValueError("Champion archive record is missing candidate details.")
    return candidate


def _empty_archive() -> dict[str, Any]:
    return {
        "version": EvolutionArchiveStore.version,
        "updated_at": None,
        "champion_id": None,
        "records": [],
    }


def _empty_run_ledger() -> dict[str, Any]:
    return {
        "version": EvolutionRunStore.version,
        "updated_at": None,
        "runs": [],
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _markdown_key_values(payload: dict[str, Any]) -> list[str]:
    return [f"- {name}: {value}" for name, value in payload.items()]


def _compare_values(
    candidate: dict[str, Any],
    baseline: dict[str, Any],
    fields: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    names = fields or tuple(sorted(set(candidate) | set(baseline)))
    rows: list[dict[str, Any]] = []
    for name in names:
        if name not in candidate and name not in baseline:
            continue
        candidate_value = candidate.get(name)
        baseline_value = baseline.get(name)
        delta = None
        if _is_number(candidate_value) and _is_number(baseline_value):
            delta = float(candidate_value) - float(baseline_value)
        rows.append(
            {
                "name": name,
                "candidate": candidate_value,
                "baseline": baseline_value,
                "delta": delta,
            }
        )
    return rows


def _promotion_review(
    candidate: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    candidate_score = candidate.get("score", {})
    baseline_score = baseline.get("score", {})
    candidate_final = candidate.get("final_review", {})
    score_delta = _numeric_delta(candidate_score, baseline_score, "score")
    alpha_delta = _numeric_delta(candidate_score, baseline_score, "alpha_pct")
    drawdown_delta = _numeric_delta(candidate_score, baseline_score, "max_drawdown_pct")
    friction_delta = _numeric_delta(candidate_score, baseline_score, "fee_friction_pct")
    trade_delta = _numeric_delta(candidate_score, baseline_score, "trades")
    final_drawdown_delta = _numeric_delta(candidate_final, baseline.get("final_review", {}), "max_drawdown_pct")
    warnings = tuple(str(item) for item in candidate_final.get("warnings", []) if item)
    severe_warnings = tuple(
        warning
        for warning in warnings
        if "bankruptcy" in warning or "stop_loss" in warning or "no_trades" in warning
    )
    reasons: list[str] = []
    verdict = "pass"
    if score_delta < 0:
        verdict = "fail"
        reasons.append("candidate_score_below_baseline")
    if alpha_delta < 0:
        verdict = "fail"
        reasons.append("candidate_alpha_below_baseline")
    if _number(candidate_score.get("trades")) <= 0:
        verdict = "fail"
        reasons.append("candidate_has_no_scored_trades")
    if severe_warnings:
        verdict = "fail"
        reasons.extend(f"severe_final_review_warning:{warning}" for warning in severe_warnings)

    review_triggers = []
    if 0 <= score_delta < 1.0:
        review_triggers.append("score_delta_below_1")
    if 0 <= alpha_delta < 0.50:
        review_triggers.append("alpha_delta_below_0_5pct")
    if drawdown_delta > 2.0:
        review_triggers.append("max_drawdown_worse_by_more_than_2pct")
    if final_drawdown_delta > 2.0:
        review_triggers.append("final_review_drawdown_worse_by_more_than_2pct")
    if friction_delta > 1.0:
        review_triggers.append("fee_friction_worse_by_more_than_1pct")
    if trade_delta < 0:
        review_triggers.append("trade_count_below_baseline")
    if warnings and not severe_warnings:
        review_triggers.extend(f"final_review_warning:{warning}" for warning in warnings)
    if verdict == "pass" and review_triggers:
        verdict = "review"
        reasons.extend(review_triggers)
    if not reasons:
        reasons.append("candidate_clears_deterministic_promotion_review")
    return {
        "verdict": verdict,
        "blocks_promotion": False,
        "score_delta": score_delta,
        "alpha_delta_pct": alpha_delta,
        "max_drawdown_delta_pct": drawdown_delta,
        "fee_friction_delta_pct": friction_delta,
        "trade_delta": trade_delta,
        "final_review_max_drawdown_delta_pct": final_drawdown_delta,
        "candidate_warnings": list(warnings),
        "reasons": reasons,
    }


def _numeric_delta(candidate: dict[str, Any], baseline: dict[str, Any], field: str) -> float:
    return _number(candidate.get(field)) - _number(baseline.get(field))


def _number(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _season_compare_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for row in payload.get("seasons", []) if isinstance(payload, dict) else []:
        if isinstance(row, dict):
            result[str(row.get("name", "-"))] = row.get("aggressiveness_multiplier")
    if isinstance(payload, dict):
        result["tick_offset_minutes"] = payload.get("tick_offset_minutes", 0)
    return result


def _markdown_compare_table(rows: list[dict[str, Any]]) -> str:
    lines = ["| Parameter | Candidate | Baseline | Delta |", "|---|---:|---:|---:|"]
    for row in rows:
        lines.append(
            "| {name} | {candidate} | {baseline} | {delta} |".format(
                name=row.get("name", "-"),
                candidate=_format_compare_value(row.get("candidate")),
                baseline=_format_compare_value(row.get("baseline")),
                delta=_format_compare_value(row.get("delta")),
            )
        )
    return "\n".join(lines)


def _html_compare_panel(title: str, rows: list[dict[str, Any]]) -> str:
    rendered_rows = []
    for row in rows:
        delta = row.get("delta")
        delta_class = ""
        if isinstance(delta, (int, float)) and not isinstance(delta, bool):
            delta_class = "positive" if delta > 0 else "negative" if delta < 0 else ""
        rendered_rows.append(
            "<tr>"
            f"<td>{_html_text(row.get('name', '-'))}</td>"
            f"<td>{_html_text(_format_compare_value(row.get('candidate')))}</td>"
            f"<td>{_html_text(_format_compare_value(row.get('baseline')))}</td>"
            f'<td class="{delta_class}">{_html_text(_format_compare_value(delta))}</td>'
            "</tr>"
        )
    return (
        f'<section class="panel"><h2>{_html_text(title)}</h2><table><thead>'
        "<tr><th>Parameter</th><th>Candidate</th><th>Baseline</th><th>Delta</th></tr>"
        f"</thead><tbody>{''.join(rendered_rows)}</tbody></table></section>"
    )


def _html_promotion_review(review: dict[str, Any]) -> str:
    reasons = review.get("reasons", [])
    reason_rows = "".join(
        f"<tr><td>reason</td><td>{_html_text(reason)}</td></tr>"
        for reason in reasons
    )
    metrics = {
        "verdict": review.get("verdict"),
        "blocks_promotion": review.get("blocks_promotion"),
        "score_delta": review.get("score_delta"),
        "alpha_delta_pct": review.get("alpha_delta_pct"),
        "max_drawdown_delta_pct": review.get("max_drawdown_delta_pct"),
        "fee_friction_delta_pct": review.get("fee_friction_delta_pct"),
        "trade_delta": review.get("trade_delta"),
    }
    metric_rows = "".join(
        f"<tr><td>{_html_text(name)}</td><td>{_html_text(_format_compare_value(value))}</td></tr>"
        for name, value in metrics.items()
    )
    verdict = str(review.get("verdict", "review"))
    verdict_class = "positive" if verdict == "pass" else "negative" if verdict == "fail" else ""
    return (
        '<section class="panel wide"><h2>Promotion Review</h2><table><tbody>'
        f'<tr><td>verdict</td><td class="{verdict_class}">{_html_text(verdict)}</td></tr>'
        f"{metric_rows}{reason_rows}</tbody></table></section>"
    )


def _format_compare_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _render_evolution_state_html(
    title: str,
    sections: dict[str, Any],
    editable: bool,
    preset_payload: dict[str, Any] | None,
) -> str:
    bounds = sections.get("bounds") if isinstance(sections.get("bounds"), dict) else _evolution_preset_bounds()
    payload = preset_payload or sections
    identity = sections.get("identity", {})
    score = sections.get("score", {})
    final_review = sections.get("final_review", {})
    generation_history = sections.get("generation_history", [])
    crucible_windows = sections.get("crucible_windows", [])
    population_plan = sections.get("population_plan", {})
    run_context = sections.get("run_context", {})
    action_panel = ""
    script = ""
    if editable:
        action_panel = """
      <section class="panel actions">
        <div>
          <h2>Preset JSON</h2>
          <p>Export this edited preset before running <code>crypto-evolve --preset</code>.</p>
        </div>
        <div class="buttonbar">
          <button data-profile="conservative" type="button">Conservative</button>
          <button data-profile="balanced" type="button">Balanced</button>
          <button data-profile="aggressive" type="button">Aggressive</button>
          <button id="downloadPreset" type="button">Export JSON</button>
        </div>
        <textarea id="jsonPreview" spellcheck="false"></textarea>
      </section>"""
        script = _evolution_state_html_script(payload)
    score_panel = ""
    if isinstance(score, dict) and score:
        score_panel = _html_readonly_panel("Score", score)
    final_panel = ""
    if isinstance(final_review, dict) and final_review:
        final_rows = {
            key: final_review.get(key)
            for key in (
                "alpha_pct",
                "total_return_pct",
                "max_drawdown_pct",
                "trades",
                "win_rate",
                "risk_rejected",
            )
            if key in final_review
        }
        final_panel = _html_readonly_panel("Final Review", final_rows)
        inventory_bridge = final_review.get("inventory_bridge")
        if isinstance(inventory_bridge, dict):
            final_panel += _html_readonly_panel("Inventory Bridge", inventory_bridge)
        monte_carlo = final_review.get("monte_carlo")
        if isinstance(monte_carlo, dict):
            final_panel += _html_readonly_panel("Monte Carlo", monte_carlo)
    history_panel = ""
    if isinstance(generation_history, list) and generation_history:
        history_panel = _html_generation_history(generation_history)
    crucible_panel = ""
    if isinstance(crucible_windows, list) and crucible_windows:
        crucible_panel = _html_crucible_windows(crucible_windows)
    population_panel = ""
    if isinstance(population_plan, dict) and population_plan:
        population_panel = _html_readonly_panel("Population Initialization", population_plan)
    identity_panel = _html_readonly_panel("Identity", identity)
    run_context_panel = ""
    if isinstance(run_context, dict) and run_context:
        run_context_panel = _html_readonly_panel("Run Context", _flatten_for_display(run_context))
    environment_panel = _html_parameter_panel(
        "Environment",
        sections.get("environment", {}),
        bounds.get("environment", {}),
        editable,
        "environment",
    )
    season_panel = _html_season_panel(
        sections.get("season_regime", {}),
        bounds.get("season_regime", {}),
        editable,
    )
    macro_panel = _html_parameter_panel(
        "Macro Genes",
        sections.get("macro_genes", {}),
        bounds.get("macro_genes", {}),
        editable,
        "seed_genome",
    )
    timing_panel = _html_parameter_panel(
        "Timing Genes",
        sections.get("timing_genes", {}),
        bounds.get("timing_genes", {}),
        editable,
        "seed_genome",
    )
    micro_panel = _html_parameter_panel(
        "Micro Genes",
        sections.get("micro_genes", {}),
        bounds.get("micro_genes", {}),
        editable,
        "seed_genome",
    )
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_html_text(title)}</title>
  <style>
    :root {{ color-scheme: light; --ink:#14212b; --muted:#64727f; --line:#d9e1e8; --panel:#ffffff; --band:#f4f8fb; --accent:#0f766e; --warn:#a15c00; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family: Inter, Segoe UI, Arial, sans-serif; background:var(--band); color:var(--ink); }}
    main {{ width:min(1180px, calc(100% - 32px)); margin:0 auto; padding:28px 0 40px; }}
    header {{ display:flex; align-items:flex-end; justify-content:space-between; gap:20px; margin-bottom:18px; }}
    h1 {{ margin:0; font-size:30px; line-height:1.1; letter-spacing:0; }}
    h2 {{ margin:0 0 12px; font-size:18px; letter-spacing:0; }}
    code {{ background:#eaf1f6; padding:2px 6px; border-radius:6px; }}
    .badge {{ border:1px solid var(--line); background:#fff; color:var(--muted); padding:8px 10px; border-radius:8px; white-space:nowrap; }}
    .grid {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:14px; }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; box-shadow:0 1px 2px rgba(20,33,43,.04); }}
    .wide {{ grid-column:1 / -1; }}
    table {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
    th, td {{ border-bottom:1px solid var(--line); padding:9px 8px; text-align:left; vertical-align:middle; font-size:14px; }}
    th:last-child, td:last-child {{ text-align:right; }}
    tr:last-child td {{ border-bottom:0; }}
    .param {{ display:grid; grid-template-columns:minmax(130px, 1fr) minmax(180px, 2fr) 104px; gap:10px; align-items:center; padding:9px 0; border-bottom:1px solid var(--line); }}
    .param:last-child {{ border-bottom:0; }}
    .param label {{ font-size:14px; color:var(--ink); overflow-wrap:anywhere; }}
    input[type=range] {{ width:100%; accent-color:var(--accent); }}
    input[type=number] {{ width:100%; height:34px; border:1px solid var(--line); border-radius:6px; padding:4px 8px; text-align:right; font:inherit; }}
    .value {{ color:var(--muted); font-variant-numeric:tabular-nums; }}
    .actions {{ display:grid; grid-template-columns:1fr auto; gap:14px; align-items:start; margin-top:14px; }}
    .actions p {{ margin:0; color:var(--muted); }}
    .buttonbar {{ display:flex; flex-wrap:wrap; gap:8px; justify-content:flex-end; }}
    button {{ height:38px; border:0; border-radius:8px; padding:0 14px; background:var(--accent); color:white; font-weight:650; cursor:pointer; }}
    textarea {{ grid-column:1 / -1; width:100%; min-height:220px; resize:vertical; border:1px solid var(--line); border-radius:8px; padding:12px; font:13px Consolas, monospace; background:#fbfdff; color:#172530; }}
    .fixed {{ color:var(--warn); font-weight:650; }}
    @media (max-width: 820px) {{ .grid, .actions {{ grid-template-columns:1fr; }} header {{ display:block; }} .badge {{ display:inline-block; margin-top:12px; }} .param {{ grid-template-columns:1fr; }} th, td {{ font-size:13px; }} }}
  </style>
</head>
<body data-editable="{str(editable).lower()}">
  <main>
    <header>
      <div>
        <h1>{_html_text(title)}</h1>
      </div>
      <div class="badge">research only | live orders disabled</div>
    </header>
    <div class="grid">
      {identity_panel}
      {run_context_panel}
      {environment_panel}
      {season_panel}
      {population_panel}
      {macro_panel}
      {timing_panel}
      {micro_panel}
      {score_panel}
      {crucible_panel}
      {history_panel}
      {final_panel}
    </div>
    {action_panel}
  </main>
  {script}
</body>
</html>
"""
    return body


def _html_parameter_panel(
    title: str,
    payload: dict[str, Any],
    bounds: dict[str, Any],
    editable: bool,
    path_prefix: str,
) -> str:
    rows = []
    for name, value in payload.items():
        spec = bounds.get(name, {}) if isinstance(bounds, dict) else {}
        path = f"{path_prefix}.{name}"
        rows.append(_html_control_row(name, value, spec, editable, path))
    return f'<section class="panel"><h2>{_html_text(title)}</h2>{"".join(rows)}</section>'


def _html_season_panel(
    payload: dict[str, Any],
    bounds: dict[str, Any],
    editable: bool,
) -> str:
    rows = []
    seasons = payload.get("seasons", []) if isinstance(payload, dict) else []
    multiplier_bounds = bounds.get("aggressiveness_multiplier", {}) if isinstance(bounds, dict) else {}
    for index, row in enumerate(seasons):
        if not isinstance(row, dict):
            continue
        rows.append(
            _html_control_row(
                str(row.get("name", f"season_{index}")),
                row.get("aggressiveness_multiplier"),
                multiplier_bounds,
                editable,
                f"season_regime.seasons.{index}.aggressiveness_multiplier",
            )
        )
    rows.append(
        _html_control_row(
            "tick_offset_minutes",
            payload.get("tick_offset_minutes", 0) if isinstance(payload, dict) else 0,
            bounds.get("tick_offset_minutes", {}) if isinstance(bounds, dict) else {},
            editable,
            "season_regime.tick_offset_minutes",
        )
    )
    return f'<section class="panel"><h2>Season Regime</h2>{"".join(rows)}</section>'


def _html_control_row(
    name: str,
    value: Any,
    spec: dict[str, Any],
    editable: bool,
    path: str,
) -> str:
    if isinstance(spec, dict) and "fixed" in spec:
        return (
            f'<div class="param"><label>{_html_text(name)}</label>'
            f'<div class="value fixed">fixed</div><div class="value">{_html_text(spec["fixed"])}</div></div>'
        )
    if not editable or not isinstance(value, (int, float)) or isinstance(value, bool):
        return (
            f'<div class="param"><label>{_html_text(name)}</label>'
            f'<div class="value">{_html_text(value)}</div><div></div></div>'
        )
    low = spec.get("min", 0) if isinstance(spec, dict) else 0
    high = spec.get("max", max(float(value), 1.0)) if isinstance(spec, dict) else max(float(value), 1.0)
    is_int = isinstance(value, int)
    step = 1 if is_int else 0.000001
    data_type = "int" if is_int else "float"
    current = _html_number(value)
    return (
        f'<div class="param"><label for="{_html_attr(path)}">{_html_text(name)}</label>'
        f'<input id="{_html_attr(path)}" data-path="{_html_attr(path)}" data-type="{data_type}" '
        f'type="range" min="{_html_number(low)}" max="{_html_number(high)}" step="{step}" value="{current}">'
        f'<input data-path="{_html_attr(path)}" data-type="{data_type}" type="number" '
        f'min="{_html_number(low)}" max="{_html_number(high)}" step="{step}" value="{current}"></div>'
    )


def _html_readonly_panel(title: str, payload: dict[str, Any]) -> str:
    rows = []
    for name, value in payload.items():
        if isinstance(value, (dict, list, tuple)):
            rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            rendered = value
        rows.append(
            f"<tr><td>{_html_text(name)}</td><td>{_html_text(_format_html_value(rendered))}</td></tr>"
        )
    return f'<section class="panel"><h2>{_html_text(title)}</h2><table><tbody>{"".join(rows)}</tbody></table></section>'


def _html_generation_history(history: list[Any]) -> str:
    rows = []
    for item in history:
        if not isinstance(item, dict):
            continue
        rows.append(
            "<tr>"
            f"<td>{_html_text(item.get('generation', '-'))}</td>"
            f"<td>{_html_text(_format_html_value(item.get('best_score')))}</td>"
            f"<td>{_html_text(_format_html_value(item.get('average_score')))}</td>"
            f"<td>{_html_text(_format_html_value(item.get('mutation_probability')))}</td>"
            f"<td>{_html_text('yes' if item.get('improved') else 'no')}</td>"
            "</tr>"
        )
    return (
        '<section class="panel wide"><h2>Generation History</h2><table><thead>'
        "<tr><th>Gen</th><th>Best</th><th>Average</th><th>Mutation</th><th>Improved</th></tr>"
        f"</thead><tbody>{''.join(rows)}</tbody></table></section>"
    )


def _html_crucible_windows(windows: list[Any]) -> str:
    rows = []
    for item in windows:
        if not isinstance(item, dict):
            continue
        rows.append(
            "<tr>"
            f"<td>{_html_text(item.get('name', '-'))}</td>"
            f"<td>{_html_text(_format_html_value(item.get('weight')))}</td>"
            f"<td>{_html_text(_format_html_value(item.get('bars')))}</td>"
            f"<td>{_html_text(item.get('start_time') or '-')}</td>"
            f"<td>{_html_text(item.get('end_time') or '-')}</td>"
            "</tr>"
        )
    return (
        '<section class="panel wide"><h2>Crucible Windows</h2><table><thead>'
        "<tr><th>Window</th><th>Weight</th><th>Bars</th><th>Start</th><th>End</th></tr>"
        f"</thead><tbody>{''.join(rows)}</tbody></table></section>"
    )


def _evolution_state_html_script(payload: dict[str, Any]) -> str:
    state_json = json.dumps(payload, ensure_ascii=False, indent=2).replace("</", "<\\/")
    return f"""<script type="application/json" id="initialState">{state_json}</script>
<script>
const state = JSON.parse(document.getElementById('initialState').textContent);
const preview = document.getElementById('jsonPreview');
const profiles = {{
  conservative: {{
    environment: {{ dead_reserve_ratio: 0.35, global_stop_loss: 0.12 }},
    seasons: [0.55, 0.75, 0.90, 1.00],
    seed_genome: {{
      beta_threshold: 0.24,
      moon_phase_pressure: 0.10,
      deadline_force_pct: 0.05,
      min_trade_threshold: 0.12,
      micro_reserve_rate: 0.05,
      ka: 1.20
    }}
  }},
  balanced: {{
    environment: {{ dead_reserve_ratio: 0.20, global_stop_loss: 0.20 }},
    seasons: [0.70, 0.90, 1.00, 1.15],
    seed_genome: {{
      beta_threshold: 0.18,
      moon_phase_pressure: 0.20,
      deadline_force_pct: 0.10,
      min_trade_threshold: 0.08,
      micro_reserve_rate: 0.10,
      ka: 1.00
    }}
  }},
  aggressive: {{
    environment: {{ dead_reserve_ratio: 0.10, global_stop_loss: 0.28 }},
    seasons: [0.85, 1.05, 1.25, 1.45],
    seed_genome: {{
      beta_threshold: 0.12,
      moon_phase_pressure: 0.45,
      deadline_force_pct: 0.18,
      min_trade_threshold: 0.05,
      micro_reserve_rate: 0.18,
      ka: 2.40
    }}
  }}
}};
function setPath(root, path, value) {{
  const parts = path.split('.');
  let target = root;
  for (let i = 0; i < parts.length - 1; i += 1) {{
    const key = /^\\d+$/.test(parts[i]) ? Number(parts[i]) : parts[i];
    target = target[key];
  }}
  const last = parts[parts.length - 1];
  target[/^\\d+$/.test(last) ? Number(last) : last] = value;
}}
function getPath(root, path) {{
  return path.split('.').reduce((target, part) => target[/^\\d+$/.test(part) ? Number(part) : part], root);
}}
function renderPreview() {{
  preview.value = JSON.stringify(state, null, 2);
}}
function syncControls() {{
  document.querySelectorAll('[data-path]').forEach((input) => {{
    const value = getPath(state, input.dataset.path);
    input.value = typeof value === 'number' ? value : String(value ?? '');
  }});
}}
document.querySelectorAll('[data-path]').forEach((input) => {{
  input.addEventListener('input', () => {{
    const type = input.dataset.type;
    const value = type === 'int' ? parseInt(input.value, 10) : parseFloat(input.value);
    setPath(state, input.dataset.path, Number.isFinite(value) ? value : 0);
    document.querySelectorAll(`[data-path="${{input.dataset.path}}"]`).forEach((peer) => {{
      if (peer !== input) peer.value = input.value;
    }});
    renderPreview();
  }});
}});
document.querySelectorAll('[data-profile]').forEach((button) => {{
  button.addEventListener('click', () => {{
    const profile = profiles[button.dataset.profile];
    Object.assign(state.environment, profile.environment);
    profile.seasons.forEach((value, index) => {{
      state.season_regime.seasons[index].aggressiveness_multiplier = value;
    }});
    Object.assign(state.seed_genome, profile.seed_genome);
    syncControls();
    renderPreview();
  }});
}});
document.getElementById('downloadPreset').addEventListener('click', () => {{
  const blob = new Blob([JSON.stringify(state, null, 2) + '\\n'], {{ type: 'application/json' }});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'evolution-preset.json';
  link.click();
  URL.revokeObjectURL(url);
}});
syncControls();
renderPreview();
</script>"""


def _format_html_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6f}"
    if value is None:
        return "-"
    return str(value)


def _html_number(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    try:
        return f"{float(value):.6f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "0"


def _html_text(value: Any) -> str:
    return html.escape(_format_html_value(value), quote=False)


def _html_attr(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _markdown_mapping_table(payload: dict[str, Any]) -> str:
    lines = ["| Parameter | Value |", "|---|---:|"]
    for name, value in payload.items():
        if isinstance(value, float):
            formatted = f"{value:.6f}"
        elif value is None:
            formatted = "-"
        else:
            formatted = str(value)
        lines.append(f"| {name} | {formatted} |")
    return "\n".join(lines)


def _flatten_for_display(payload: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for name, value in payload.items():
        key = f"{prefix}.{name}" if prefix else str(name)
        if isinstance(value, dict):
            flattened.update(_flatten_for_display(value, key))
        elif isinstance(value, (list, tuple)):
            flattened[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            flattened[key] = value
    return flattened


def _evolution_preset_bounds() -> dict[str, Any]:
    return {
        "environment": {
            name: {"min": bounds[0], "max": bounds[1]}
            for name, bounds in _ENV_FLOAT_BOUNDS.items()
        }
        | {"max_leverage": {"fixed": 1}},
        "season_regime": {
            "aggressiveness_multiplier": {
                "min": _SEASON_MULTIPLIER_BOUNDS[0],
                "max": _SEASON_MULTIPLIER_BOUNDS[1],
            },
            "tick_offset_minutes": {
                "min": _TICK_OFFSET_BOUNDS[0],
                "max": _TICK_OFFSET_BOUNDS[1],
            },
        },
        "macro_genes": _gene_bounds(_MACRO_GENE_NAMES),
        "timing_genes": _gene_bounds(_TIMING_GENE_NAMES),
        "micro_genes": _gene_bounds(_MICRO_GENE_NAMES),
    }


def _evolution_preset_safety() -> dict[str, Any]:
    return {
        "mode": "research_only",
        "places_live_orders": False,
        "max_leverage_fixed": 1,
        "preset_is_not_a_champion": True,
    }


def _gene_bounds(names: tuple[str, ...]) -> dict[str, dict[str, float | int]]:
    bounds: dict[str, dict[str, float | int]] = {}
    for name in names:
        if name in _INT_GENE_BOUNDS:
            low, high = _INT_GENE_BOUNDS[name]
            bounds[name] = {"min": low, "max": high}
        else:
            low, high = _FLOAT_GENE_BOUNDS[name]
            bounds[name] = {"min": low, "max": high}
    return bounds


def _genome_from_payload(payload: Any) -> LunarGenome:
    if not isinstance(payload, dict):
        raise ValueError("Evolution preset seed_genome must be an object.")
    return LunarGenome(**payload).project()


def _environment_from_payload(payload: Any) -> EvolutionEnvironment:
    if not isinstance(payload, dict):
        raise ValueError("Evolution preset environment must be an object.")
    return EvolutionEnvironment(**payload).project()


def _season_regime_from_payload(payload: Any) -> SeasonRegime:
    if not isinstance(payload, dict):
        raise ValueError("Evolution preset season_regime must be an object.")
    if "aggressiveness_multipliers" in payload:
        multipliers = tuple(float(value) for value in payload["aggressiveness_multipliers"])
    else:
        rows = payload.get("seasons")
        if not isinstance(rows, list):
            raise ValueError("Evolution preset season_regime must include seasons.")
        by_name: dict[str, float] = {}
        positional: list[float] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Evolution preset season rows must be objects.")
            multiplier = float(row["aggressiveness_multiplier"])
            name = row.get("name")
            if isinstance(name, str) and name in SEASON_NAMES:
                by_name[name] = multiplier
            else:
                positional.append(multiplier)
        if by_name:
            missing = [name for name in SEASON_NAMES if name not in by_name]
            if missing:
                raise ValueError(f"Evolution preset season_regime is missing: {', '.join(missing)}.")
            multipliers = tuple(by_name[name] for name in SEASON_NAMES)
        else:
            multipliers = tuple(positional)
    tick_offset = int(payload.get("tick_offset_minutes", 0))
    return SeasonRegime(
        aggressiveness_multipliers=multipliers,
        tick_offset_minutes=tick_offset,
    ).project()


def _genome_from_record(candidate: dict[str, Any]) -> LunarGenome:
    payload = candidate.get("genome")
    if not isinstance(payload, dict):
        raise ValueError("Champion archive record is missing a genome payload.")
    return LunarGenome(**payload).project()


def _environment_from_record(candidate: dict[str, Any]) -> EvolutionEnvironment:
    payload = candidate.get("environment")
    if not isinstance(payload, dict):
        raise ValueError("Champion archive record is missing an environment payload.")
    return EvolutionEnvironment(**payload).project()


def _runtime_season_multiplier(candidate: dict[str, Any], season: str) -> float:
    normalized = _normalize_season(season)
    payload = candidate.get("season_regime", {})
    rows = payload.get("seasons") if isinstance(payload, dict) else None
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("name") == normalized:
                try:
                    return float(row["aggressiveness_multiplier"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"Champion season multiplier is invalid for {normalized}.") from exc
    default = SeasonRegime()
    index = SEASON_NAMES.index(normalized)
    return default.aggressiveness_multipliers[index]


def _normalize_season(season: str) -> str:
    normalized = season.strip().lower()
    if normalized not in SEASON_NAMES:
        valid = ", ".join(SEASON_NAMES)
        raise ValueError(f"season must be one of: {valid}.")
    return normalized


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _truncated_normal(
    mean: float,
    sigma: float,
    bounds: tuple[float, float],
    rng: random.Random,
) -> float:
    if sigma <= 0:
        return _clamp(mean, bounds[0], bounds[1])
    for _ in range(20):
        sampled = rng.gauss(mean, sigma)
        if bounds[0] <= sampled <= bounds[1]:
            return sampled
    return _clamp(mean, bounds[0], bounds[1])


class _CachedBacktestClient:
    def __init__(self, delegate, candles_by_symbol: dict[str, list[Candle]]):
        self.delegate = delegate
        self.candles_by_symbol = candles_by_symbol

    def get_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        return self.candles_by_symbol[symbol][-limit:]

    def get_symbol_rules(self, symbol: str) -> SymbolRules:
        return self.delegate.get_symbol_rules(symbol)
