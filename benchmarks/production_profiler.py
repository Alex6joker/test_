from __future__ import annotations

import builtins
import cProfile
import io
import os
import pstats
import runpy
import sys
import time


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _run_production_path() -> None:
    """Execute the real production CLI path inside the profiler."""
    old_argv = sys.argv
    old_input = builtins.input
    try:
        sys.argv = [
            "main.py",
            "--work-mode",
            "1",
            "--selected-forts",
            "03_BRENT",
        ]
        builtins.input = lambda _prompt="": "NONE"
        runpy.run_path(
            os.path.join(PROJECT_ROOT, "main.py"),
            run_name="__main__",
        )
    finally:
        sys.argv = old_argv
        builtins.input = old_input


def _print_targeted_stats(stats: pstats.Stats) -> None:
    """Print the main production-path stages with their cumulative cost."""
    targets = {
        "load_and_prepare_backtest_dataframe",
        "validate_backtest_dataframe",
        "run_instrument_backtest",
        "run_native_backtest",
        "bars_from_dataframe",
        "process_bar",
        "finish",
        "_log_backtest_result",
    }

    rows = []
    for func, values in stats.stats.items():
        _cc, nc, tt, ct, _callers = values
        filename, lineno, name = func
        if name in targets:
            rows.append((ct, nc, tt, filename, lineno, name))

    print("\n===== PRODUCTION PATH TARGETS =====")
    print("ncalls       tottime     cumtime  function")
    print("-----------------------------------------------")
    for ct, nc, tt, filename, lineno, name in sorted(rows, reverse=True):
        short_file = os.path.relpath(filename, PROJECT_ROOT)
        print(
            f"{nc:8d} {tt:11.4f} {ct:11.4f} "
            f"{short_file}:{lineno}({name})"
        )


def main() -> None:
    print("=== PRODUCTION BACKTEST PROFILE ===")
    print("Path: main.py -> mode 1 -> 03_BRENT -> NONE")
    print("This is a cProfile diagnostic, not a speed benchmark.")

    profiler = cProfile.Profile()
    wall_start = time.perf_counter()
    profiler.enable()
    try:
        _run_production_path()
    finally:
        profiler.disable()
    wall_elapsed = time.perf_counter() - wall_start

    stats = pstats.Stats(profiler).strip_dirs()
    stats.sort_stats("cumulative")

    print(f"\nPROFILE WALL TIME (with cProfile overhead): {wall_elapsed:.3f} sec")
    _print_targeted_stats(stats)

    print("\n===== TOP 40 BY CUMULATIVE TIME =====")
    stream = io.StringIO()
    stats.stream = stream
    stats.print_stats(40)
    print(stream.getvalue())


if __name__ == "__main__":
    main()
