"""Stage 8.1 Backtrader performance benchmark.

The benchmark imports the current production backtester but does not modify it.
Each measured run is saved as a separate timestamped JSON file.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
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

EXPECTED = {
    "final_portfolio_value": 214018.69,
    "real_net_profit": -49981.31,
    "total_closed_trades": 151,
    "total_contracts": 1500,
    "total_commission": 15319.34,
    "open_position_size": 4,
    "virtual_cash": 217437.49,
}


def load_config():
    path = PROJECT_ROOT / "03_BRENT" / "config.py"
    spec = importlib.util.spec_from_file_location("stage8_benchmark_config", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load config: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def result_dict(result) -> dict:
    return {
        "final_portfolio_value": result.final_portfolio_value,
        "real_net_profit": result.real_net_profit,
        "total_closed_trades": result.total_closed_trades,
        "total_contracts": result.total_contracts,
        "total_commission": result.total_commission,
        "open_position_size": result.open_position_size,
        "virtual_cash": result.virtual_cash,
    }


def run_once(cfg, mode: str, processed_path: str):
    from core.backtest_logger import BacktestLogger
    from core.backtest_runner import run_backtest

    logger = None
    log_path = None
    if mode != "NONE":
        # The run-specific log name is assigned by the caller before this
        # function is used for a measured run.
        raise RuntimeError("Non-NONE runs must use run_once_with_log_path()")

    logger = BacktestLogger(str(RESULTS_DIR / "_benchmark_none.log"), reset=True, mode="NONE")
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    try:
        result = run_backtest(
            processed_path=processed_path,
            cfg=cfg,
            logger=logger,
            precision_money=cfg.PRECISION_NUM_DEPO_RUB,
        )
    finally:
        logger.close()
    return result, time.perf_counter() - wall_start, time.process_time() - cpu_start, log_path


def run_once_with_log_path(cfg, mode: str, processed_path: str, log_path: Path):
    from core.backtest_logger import BacktestLogger
    from core.backtest_runner import run_backtest

    logger = BacktestLogger(str(log_path), reset=True, mode=mode)
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    try:
        result = run_backtest(
            processed_path=processed_path,
            cfg=cfg,
            logger=logger,
            precision_money=cfg.PRECISION_NUM_DEPO_RUB,
        )
    finally:
        logger.close()
    return result, time.perf_counter() - wall_start, time.process_time() - cpu_start


def timestamp_name(prefix: str) -> str:
    """Return DD_MM_YYYY_HH_MM_SS, adding a collision suffix only if needed."""
    base = datetime.now().strftime("%d_%m_%Y_%H_%M_%S")
    candidate = RESULTS_DIR / f"{prefix}_{base}.json"
    if not candidate.exists():
        return base
    index = 2
    while (RESULTS_DIR / f"{prefix}_{base}_{index:02d}.json").exists():
        index += 1
    return f"{base}_{index:02d}"


def validate_result(result, mode: str) -> dict:
    actual = result_dict(result)
    if actual != EXPECTED:
        raise RuntimeError(
            f"Benchmark invariant failed in mode {mode}: {actual} != {EXPECTED}"
        )
    return actual


def save_run(*, timestamp: str, cfg, source_csv: Path, bars: int, mode: str,
             run_number: int, wall: float, cpu: float, result: dict,
             log_path: Path | None) -> Path:
    payload = {
        "stage": "8.1",
        "benchmark_timestamp": timestamp,
        "instrument": cfg.FUT_SEC_CODE,
        "mode": mode,
        "run_number": run_number,
        "source_csv": str(source_csv),
        "bars": bars,
        "wall_seconds": wall,
        "cpu_seconds": cpu,
        "result": result,
        "invariants_passed": True,
    }
    if log_path is not None:
        payload["log_file"] = str(log_path)

    path = RESULTS_DIR / f"benchmark_{timestamp}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument(
        "--modes", nargs="+", default=["NONE"],
        choices=["NONE", "FAST", "DIAGNOSTIC"],
    )
    args = parser.parse_args()

    if args.runs < 1 or args.warmup < 0:
        raise SystemExit("--runs must be >= 1 and --warmup must be >= 0")

    try:
        import backtrader  # noqa: F401
    except ImportError:
        print("Backtrader is not installed in this Python environment.")
        print("Run this benchmark in the project environment where the backtester runs.")
        return 2

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()

    from core.backtest_data import export_backtrader_adapter, load_and_prepare_backtest_dataframe
    from core.backtest_logger import BacktestLogger
    from core.backtest_validation import validate_backtest_dataframe

    source_csv = PROJECT_ROOT / "03_BRENT" / cfg.TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH
    prepared = load_and_prepare_backtest_dataframe(str(source_csv))
    validation_logger = BacktestLogger(str(RESULTS_DIR / "_validation.log"), reset=True, mode="NONE")
    try:
        validate_backtest_dataframe(prepared, validation_logger)
    finally:
        validation_logger.close()

    bars = int(len(prepared))
    fd, processed_path = tempfile.mkstemp(prefix="stage8a1_bt_", suffix=".csv")
    os.close(fd)

    try:
        export_backtrader_adapter(prepared, processed_path)

        for mode in args.modes:
            # Warm-up runs are intentionally not saved.
            for _ in range(args.warmup):
                if mode == "NONE":
                    run_once(cfg, mode, processed_path)
                else:
                    warmup_log = RESULTS_DIR / f"_warmup_{mode.lower()}.log"
                    run_once_with_log_path(cfg, mode, processed_path, warmup_log)

            samples = []
            cpu_samples = []

            for run_number in range(1, args.runs + 1):
                timestamp = timestamp_name("benchmark")
                log_path = None
                if mode == "NONE":
                    result, wall, cpu, _ = run_once(cfg, mode, processed_path)
                else:
                    log_path = RESULTS_DIR / f"benchmark_{timestamp}.log"
                    result, wall, cpu = run_once_with_log_path(
                        cfg, mode, processed_path, log_path
                    )

                actual = validate_result(result, mode)
                samples.append(wall)
                cpu_samples.append(cpu)

                saved = save_run(
                    timestamp=timestamp,
                    cfg=cfg,
                    source_csv=source_csv,
                    bars=bars,
                    mode=mode,
                    run_number=run_number,
                    wall=wall,
                    cpu=cpu,
                    result=actual,
                    log_path=log_path,
                )
                print(
                    f"{mode} run {run_number}/{args.runs}: "
                    f"wall={wall:.6f}s cpu={cpu:.6f}s -> {saved.name}"
                )

            print(
                f"{mode}: min={min(samples):.6f}s "
                f"median={statistics.median(samples):.6f}s "
                f"max={max(samples):.6f}s"
            )
            print(
                f"{mode}: cpu_min={min(cpu_samples):.6f}s "
                f"cpu_median={statistics.median(cpu_samples):.6f}s "
                f"cpu_max={max(cpu_samples):.6f}s"
            )

        return 0
    finally:
        try:
            os.remove(processed_path)
        except FileNotFoundError:
            pass
        for temporary in (RESULTS_DIR / "_benchmark_none.log", RESULTS_DIR / "_validation.log"):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
