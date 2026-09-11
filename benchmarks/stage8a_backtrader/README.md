# Stage 8.1 — Backtrader Performance Benchmark

## Purpose

Measure the performance of the current Backtrader-based backtester without modifying production code.

The first question is:

> Is Backtrader a significant runtime bottleneck?

No native runner is introduced in Stage 8.1. That belongs to the later comparison stage.

## Scope

The benchmark imports the production `core/` and current backtester from the project root. It does not copy or modify production modules.

The timed `run_backtest()` path includes:

- Backtrader Cerebro and iteration;
- Backtrader CSV feed;
- Backtrader → Bar adapter;
- Market;
- SignalEngine;
- ExecutionEngine;
- Accounting;
- TradeLedger;
- the current virtual execution model.

Data preparation is performed before timing the measured run.

## Result storage

**Every measured run is a separate file.** Results are never aggregated into one `benchmark_latest.json` and never overwritten intentionally.

Normal filename:

```text
benchmark_DD_MM_YYYY_HH_MM_SS.json
```

If two runs happen to receive the same second, a collision suffix is added automatically, for example:

```text
benchmark_09_09_2026_20_45_12_02.json
```

For `FAST` and `DIAGNOSTIC`, the corresponding log has the same base name:

```text
benchmark_09_09_2026_20_45_12.json
benchmark_09_09_2026_20_45_12.log
```

`NONE` creates no persistent benchmark log.

Warm-up runs are not saved as benchmark results.

## Benchmark command

Run from the project root:

```text
python benchmarks/stage8a_backtrader/benchmark.py
```

Default: 1 warm-up + 5 measured runs, mode `NONE`.

Recommended first measurement:

```text
python benchmarks/stage8a_backtrader/benchmark.py --warmup 2 --runs 10 --modes NONE
```

This produces 10 JSON files.

## Logging overhead

To measure the effect of logging separately:

```text
python benchmarks/stage8a_backtrader/benchmark.py --warmup 2 --runs 5 --modes NONE FAST DIAGNOSTIC
```

Each measured run is still saved independently.

`DIAGNOSTIC` is intentionally not part of the default benchmark because it generates a very large amount of output.

## CPU profile

The profiler is deliberately named `profiler.py`, not `profile.py`, because Python's `cProfile` imports the standard `profile` module and a local `profile.py` causes a module-name collision.

Run:

```text
python benchmarks/stage8a_backtrader/profiler.py
```

It creates:

```text
profile_DD_MM_YYYY_HH_MM_SS.prof
profile_DD_MM_YYYY_HH_MM_SS.txt
```

The profile covers only the timed `run_backtest()` execution with logging disabled.

## Invariants

Every measured run must reproduce the current Stage 7 result exactly:

- final_portfolio_value = 214018.69
- real_net_profit = -49981.31
- total_closed_trades = 151
- total_contracts = 1500
- total_commission = 15319.34
- open_position_size = 4
- virtual_cash = 217437.49

If any value differs, the benchmark fails instead of recording invalid performance data.

## What is measured

For every measured run:

- wall-clock seconds via `time.perf_counter()`;
- CPU seconds via `time.process_time()`;
- number of bars;
- logging mode;
- run number;
- complete backtest result;
- invariant status.

The console additionally prints min / median / max after all runs of each mode. The raw measurements remain in the individual JSON files.

## Production safety

Stage 8.1 benchmark code must not modify production source files.

Temporary adapter CSV files are created in the OS temporary directory and deleted after the run.
