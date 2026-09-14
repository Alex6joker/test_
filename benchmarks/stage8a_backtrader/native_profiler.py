"""Profile the framework-independent native backtest path.

This is a measurement-only helper. It does not change production backtester
behavior and does not replace the existing benchmark.

The measured region is the same run_native() path used by native_benchmark.py,
so the profile can be compared directly with the ~2.22 s native benchmark.
"""
from __future__ import annotations

import argparse
import cProfile
import importlib.util
import os
import pstats
import sys
from datetime import datetime
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BENCHMARK_DIR.parents[1]
RESULTS_DIR = BENCHMARK_DIR / "results"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_config():
    path = PROJECT_ROOT / "03_BRENT" / "config.py"
    spec = importlib.util.spec_from_file_location("native_profile_config", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load config: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--top", type=int, default=100)
    args = parser.parse_args()

    if args.warmup < 0 or args.top < 1:
        raise SystemExit("warmup >= 0 and top >= 1 required")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()

    from core.backtest_data import load_and_prepare_backtest_dataframe
    from core.backtest_logger import BacktestLogger
    from core.backtest_validation import validate_backtest_dataframe
    from benchmarks.stage8a_backtrader.native_benchmark import run_native

    source_csv = PROJECT_ROOT / "03_BRENT" / cfg.TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH
    prepared = load_and_prepare_backtest_dataframe(str(source_csv))

    validation_logger = BacktestLogger(
        str(RESULTS_DIR / "_native_profile_validation.log"),
        reset=True,
        mode="NONE",
    )
    try:
        validate_backtest_dataframe(prepared, validation_logger)
    finally:
        validation_logger.close()

    # Warmups are intentionally outside the measured profile.
    for _ in range(args.warmup):
        run_native(cfg, prepared)

    timestamp = datetime.now().strftime("%d_%m_%Y_%H_%M_%S")
    prof_path = RESULTS_DIR / f"native_profile_{timestamp}.prof"
    txt_path = RESULTS_DIR / f"native_profile_{timestamp}.txt"

    profiler = cProfile.Profile()
    try:
        profiler.enable()
        result = run_native(cfg, prepared)
        profiler.disable()

        profiler.dump_stats(str(prof_path))

        with txt_path.open("w", encoding="utf-8") as report:
            report.write(f"profile_timestamp = {timestamp}\n")
            report.write(f"bars = {len(prepared)}\n")
            report.write(f"result = {result!r}\n\n")
            report.write("=== cumulative ===\n")
            stats = pstats.Stats(profiler, stream=report)
            stats.sort_stats("cumulative")
            stats.print_stats(args.top)

            report.write("\n=== internal time ===\n")
            stats = pstats.Stats(profiler, stream=report)
            stats.sort_stats("tottime")
            stats.print_stats(args.top)

        # Print a compact report to the console so the user does not need to
        # send the generated files unless deeper inspection is necessary.
        stats = pstats.Stats(profiler)
        stats.sort_stats("cumulative")
        print(f"Profile saved: {prof_path.name}")
        print(f"Profile report saved: {txt_path.name}")
        print(f"Bars: {len(prepared)}")
        print(f"Result: {result}")
        print("")
        print(f"Top {args.top} functions by cumulative time:")
        stats.print_stats(args.top)
        return 0
    finally:
        profiler.disable()
        for temporary in (RESULTS_DIR / "_native_profile_validation.log",):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
