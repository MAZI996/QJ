# Hyperliquid Evolution Lab

The evolution lab is the research layer above historical replay. It keeps the
TradingAgents crypto extension in analysis mode and produces challenger
parameter packages only.

It does not call the execution router, does not use private keys, and does not
place orders.

## Run

Small deterministic smoke run:

```powershell
python -m cli.main crypto-evolve --mainnet --symbols BTC,HYPE --interval 5m --bars 800 --population 4 --generations 1 --seed 7 --output reports/hyperliquid-evolution
```

Longer research run:

```powershell
python -m cli.main crypto-evolve --mainnet --symbols BTC,ETH,SOL,HYPE --interval 5m --bars 1500 --population 12 --generations 4 --fee-bps 20 --slippage-bps 2 --monte-carlo-simulations 250 --output reports/hyperliquid-evolution
```

The output base path writes:

- `reports/hyperliquid-evolution.json`
- `reports/hyperliquid-evolution.md`
- `reports/hyperliquid-evolution.html`

The JSON, Markdown, HTML, and archive record all include `run_context`: fee and
slippage assumptions, Monte Carlo path count, environment/season/seed sources,
archive seed settings, preset validation summary, and the safety flags needed by
a front-end task dashboard.

Each `crypto-evolve` run is also recorded in the local job ledger. The CLI
writes a `running` record before the GA starts, then updates that same `run_id`
to `completed` or `failed`:

```text
%USERPROFILE%\.tradingagents\crypto\evolution_runs.json
```

List those task records, or export a dashboard for the front end:

```powershell
python -m cli.main crypto-evolution-jobs
python -m cli.main crypto-evolution-jobs --html-output reports/evolution-jobs.html
python -m cli.main crypto-evolution-job-inspect evo-run-xxxxxxxxxxxx --html-output reports/evolution-job.html
```

By default, the challenger is also written to:

```text
%USERPROFILE%\.tradingagents\crypto\evolution_archive.json
```

Disable archive writes only for throwaway experiments:

```powershell
python -m cli.main crypto-evolve --mainnet --symbols BTC,HYPE --interval 5m --bars 800 --population 4 --generations 1 --no-archive
```

## Archive And Promotion

Generate a pre-run editable preset:

```powershell
python -m cli.main crypto-evolution-preset --output reports/evolution-preset.json
```

Validate and preview the layered launch state after editing the preset:

```powershell
python -m cli.main crypto-evolution-preset --input reports/evolution-preset.json --markdown-output reports/evolution-launch-state.md
```

The preset command prints a validation panel before any run. Values outside the
legal parameter boxes are projected back into range and listed explicitly;
`max_leverage` is always forced to `1`, and any preset safety flag claiming live
orders is ignored.

Generate a local editable HTML state window:

```powershell
python -m cli.main crypto-evolution-preset --input reports/evolution-preset.json --html-output reports/evolution-launch-state.html
```

Run evolution with that preset after reviewing or editing it:

```powershell
python -m cli.main crypto-evolve --mainnet --symbols BTC,HYPE --interval 5m --bars 800 --population 4 --generations 1 --preset reports/evolution-preset.json
```

By default, `crypto-evolve` also injects archived `champion` and `challenger`
genomes into the next initial population as old elite seeds. When a champion
exists, its `Environment` becomes the anchor for the next epoch environment
sample unless the run uses an explicit preset environment. Disable archive
seeds for a clean exploration run:

```powershell
python -m cli.main crypto-evolve --mainnet --symbols BTC,HYPE --interval 5m --bars 800 --population 10 --generations 2 --no-archive-seeds
```

List local records:

```powershell
python -m cli.main crypto-evolution-archive
python -m cli.main crypto-evolution-archive --html-output reports/evolution-archive.html
```

Inspect the current champion as a layered state window:

```powershell
python -m cli.main crypto-evolution-inspect
```

Inspect or export a specific package:

```powershell
python -m cli.main crypto-evolution-inspect challenger-xxxxxxxxxxxx --json-output reports/evolution-package.json --markdown-output reports/evolution-package.md
```

Export the same package as a read-only local HTML state window:

```powershell
python -m cli.main crypto-evolution-inspect challenger-xxxxxxxxxxxx --html-output reports/evolution-package.html
```

Compare one challenger against the current champion before promotion:

```powershell
python -m cli.main crypto-evolution-compare challenger-xxxxxxxxxxxx --html-output reports/evolution-compare.html
```

The comparison includes a deterministic Promotion Review verdict
(`pass` / `review` / `fail`). It is advisory and read-only; it never promotes
or blocks a package by itself.

Promote one archived challenger to champion:

```powershell
python -m cli.main crypto-evolution-promote challenger-xxxxxxxxxxxx
```

Promotion prints and archives the same deterministic review against the
previous champion when one exists. The review is stored as
`promotion_review_at_promote` on the promoted record, then refreshes the local
read-only champion cache:

```powershell
python -m cli.main crypto-evolution-champion-cache --refresh
```

Promotion changes archive state only:

- the promoted challenger becomes `champion`,
- the previous champion becomes `retired`,
- other challengers remain unchanged,
- no order router or live trading path is called.

## Runtime Champion Read

Runtime commands do not load GA output by default. Use `--champion` only when
you want analysis or paper runs to use the current champion package:

```powershell
python -m cli.main crypto-regime --mainnet --symbols BTC,ETH,SOL,HYPE --interval 1h --bars 96
python -m cli.main crypto-scan --mode analysis --champion --champion-season auto
python -m cli.main crypto-workflow --mode paper --champion --champion-season autumn
python -m cli.main crypto-autopilot --mode paper --champion --cycles 1
```

Safety rules for runtime champion loading:

- allowed only in `analysis` or `paper` mode,
- reads `evolution_champion_cache.json` first when it is present and current,
  then falls back to the archive champion,
- refused in `testnet` and `live` mode,
- `--champion-season auto` uses the deterministic Regime Engine to select
  winter, spring, summer, or autumn from current candle water level,
- keeps Hyperliquid leverage fixed at `1`,
- cannot raise the operator-configured `max_position_pct`,
- cannot lower the operator-configured `min_confidence`,
- falls back to built-in defaults when no champion exists.

## Current Model

- `LunarGenome` evolves macro genes, timing genes, and micro combat genes as one
  chromosome.
- `EvolutionEnvironment` is fixed inside one epoch and currently keeps
  `MaxLeverage` locked at `1`.
- Cross-epoch environment sampling anchors on the current champion package when
  one exists; explicit preset environment values override that anchor.
- `SeasonRegime` samples four fixed aggressiveness multipliers for winter,
  spring, summer, and autumn. Runtime commands can either receive one of those
  seasons manually or ask `RegimeEngine` to classify the market from candles.
- Each evolution report records the actual initial population mix: incumbent
  elites, targeted mutants, explorers, seed pool size, and whether an explicit
  preset seed genome was used.
- Each candidate is replayed through weighted crucible windows (`all`, `long`,
  `medium`, `short` where enough history exists) plus seed-stable random
  in-sample windows, then through four season slices inside each window, and
  scored against a Ghost DCA baseline.
- The generated JSON/Markdown/HTML package records the actual crucible window
  plan for that epoch, including each window's weight, bar count, and time
  span, so a challenger can be inspected against the slices it survived.
- Evolution replay enables the optional inventory bridge: macro DCA purchases
  enter dead inventory, acceleration spikes unlock that inventory into float
  inventory, and the simulated micro sell realizes PnL with the same pessimistic
  fee/slippage model.
- Macro/timing genes directly shape that bridge: `BetaThreshold` gates DCA
  against the EMA anchor, `MoonPhasePressure` changes DCA budget, `DeadlineForcePct`
  can force reserve deployment, `GCThresholdMonths`/`GCMaxRatio` release stale
  dead inventory, and `TMacro`/`EMAAnchor` control cadence and anchor length.
- The default fee assumption is pessimistic: `--fee-bps 20` charged on entry and
  exit.
- The final challenger gets one full-sample review and Monte Carlo path
  stability report. This is written to the challenger package but does not
  change fitness or block archive writes.
- Challenger score and final review include inventory-bridge telemetry:
  event count, macro buys, acceleration unlocks, micro sells, realized bridge
  PnL, LOT_SIZE rejections, and ending dead/float inventory balances.
- Every generation records best score, average score, elite count, mutation
  probability, mutation scale, stale-generation count, and whether that
  generation improved.

## Fitness

The first scoring model rewards alpha over Ghost DCA and realized PnL, then
penalizes:

- estimated taker-fee friction,
- max drawdown,
- risk rejections,
- no-trade genomes,
- environment stop-loss violations.

The score is a research ranking signal. It is not a live-trade approval.

## Final Review

After the epoch finishes, only the final challenger receives an extra
full-sample replay plus Monte Carlo trade-path review. The report includes:

- full-sample alpha versus Ghost DCA,
- full-sample return, drawdown, trades, and win rate,
- Monte Carlo P05/P50/P95 return,
- bankruptcy probability,
- global stop-loss breach probability,
- warnings such as negative P05 return or stop-loss breach probability above
  the current alert threshold.

This terminal review is advisory. It does not rewrite the challenger's fitness
score and does not automatically reject the challenger.

## Promotion Boundary

Epoch output status is `challenger`.

Runtime trading instances must continue to load the current champion package or
the built-in defaults. A challenger requires explicit human promotion before it
can become a champion seed.
