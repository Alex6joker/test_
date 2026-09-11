"""Stage 8.1 cProfile entry point for the current Backtrader runtime."""
from __future__ import annotations

import cProfile
import importlib.util
import os
import pstats
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BENCHMARK_DIR.parents[1]
RESULTS_DIR = BENCHMARK_DIR / "results"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_config():
    path = PROJECT_ROOT / "03_BRENT" / "config.py"
    spec = importlib.util.spec_from_file_location("stage8_profile_config", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load config: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    try:
        import backtrader  # noqa: F401
    except ImportError:
        print("Backtrader is not installed in this Python environment.")
        return 2

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()

    from core.backtest_data import export_backtrader_adapter, load_and_prepare_backtest_dataframe
    from core.backtest_logger import BacktestLogger
    from core.backtest_runner import run_backtest
    from core.backtest_validation import validate_backtest_dataframe

    source_csv = PROJECT_ROOT / "03_BRENT" / cfg.TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH
    prepared = load_and_prepare_backtest_dataframe(str(source_csv))
    validation_logger = BacktestLogger(str(RESULTS_DIR / "_profile_validation.log"), reset=True, mode="NONE")
    try:
        validate_backtest_dataframe(prepared, validation_logger)
    finally:
        validation_logger.close()

    base = datetime.now().strftime("%d_%m_%Y_%H_%M_%S")
    timestamp = base
    index = 2
    while (RESULTS_DIR / f"profile_{timestamp}.prof").exists() or (RESULTS_DIR / f"profile_{timestamp}.txt").exists():
        timestamp = f"{base}_{index:02d}"
        index += 1
    prof_path = RESULTS_DIR / f"profile_{timestamp}.prof"
    txt_path = RESULTS_DIR / f"profile_{timestamp}.txt"

    fd, processed_path = tempfile.mkstemp(prefix="stage8a1_profile_", suffix=".csv")
    os.close(fd)
    logger = None
    try:
        export_backtrader_adapter(prepared, processed_path)
        logger = BacktestLogger(str(RESULTS_DIR / "_profile_run.log"), reset=True, mode="NONE")
        profiler = cProfile.Profile()
        start = time.perf_counter()
        profiler.enable()
        try:
            result = run_backtest(
                processed_path=processed_path,
                cfg=cfg,
                logger=logger,
                precision_money=cfg.PRECISION_NUM_DEPO_RUB,
            )
        finally:
            profiler.disable()
            logger.close()
        elapsed = time.perf_counter() - start

        profiler.dump_stats(str(prof_path))
        with txt_path.open("w", encoding="utf-8") as f:
            f.write(f"profile_timestamp = {timestamp}\n")
            f.write(f"bars = {len(prepared)}\n")
            f.write(f"wall_seconds = {elapsed:.6f}\n")
            f.write(f"result = {result!r}\n\n")
            stats = pstats.Stats(profiler, stream=f)
            stats.sort_stats("cumulative")
            stats.print_stats(100)

        print(f"Profile saved: {prof_path.name}")
        print(f"Profile report saved: {txt_path.name}")
        return 0
    finally:
        if logger is not None:
            try:
                logger.close()
            except Exception:
                pass
        try:
            os.remove(processed_path)
        except FileNotFoundError:
            pass
        for temporary in (RESULTS_DIR / "_profile_validation.log", RESULTS_DIR / "_profile_run.log"):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
