"""Stage 8.2 experiment: Backtrader runtime vs headless native runner.

Production code is not modified. The native runner reuses the existing
framework-independent Market, SignalEngine, ExecutionEngine, AccountingEngine
and TradeLedger blocks and reproduces the current BacktestSignalMixin.next()
control flow.

The purpose is measurement only: determine how much runtime remains after the
Backtrader Cerebro/feed/strategy loop is removed. Result invariants are checked
on every measured run.
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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

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


@dataclass(frozen=True)
class RunSample:
    variant: str
    run_number: int
    wall_seconds: float
    cpu_seconds: float
    result: dict


def load_config():
    path = PROJECT_ROOT / "03_BRENT" / "config.py"
    spec = importlib.util.spec_from_file_location("stage8_native_config", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load config: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def result_dict(state, params) -> dict:
    return {
        "final_portfolio_value": round(float(state.final_virtual_equity), 2),
        "real_net_profit": round(float(state.final_virtual_equity) - float(params.initial_cash), 2),
        "total_closed_trades": int(state.closed_trades),
        "total_contracts": int(state.total_contracts),
        "total_commission": round(float(state.total_commission), 2),
        "open_position_size": int(state.virtual_position_size),
        "virtual_cash": round(float(state.virtual_cash), 2),
    }


def validate_result(actual: dict, variant: str) -> None:
    if actual != EXPECTED:
        raise RuntimeError(
            f"Invariant failed variant={variant}: {actual} != {EXPECTED}"
        )


def make_params(cfg):
    return SimpleNamespace(
        trigger=cfg.TRIGGER_SPREAD,
        tp=cfg.TAKE_PROFIT,
        sl=cfg.STOP_LOSS,
        risk=cfg.OFFER_RISK,
        real_mult=cfg.REAL_MULT,
        real_margin=cfg.REAL_MARGIN,
        safety_factor=cfg.SAFETY_FACTOR,
        precision_num=cfg.PRECISION_NUM,
        precision_money=cfg.PRECISION_NUM_DEPO_RUB,
        dynamic_trail_steps=cfg.DYNAMIC_TRAIL_STEPS,
        initial_cash=cfg.INITIAL_CASH,
    )


class NativeBacktestContext:
    """Headless host for the already extracted virtual-backtest components."""

    def __init__(self, params, logger):
        from core.backtest_accounting import AccountingEngine, BacktestAccountingMixin
        from core.backtest_execution import BacktestExecutionContext, ExecutionEngine
        from core.backtest_market import Market
        from core.backtest_numeric import BacktestNumericMixin
        from core.backtest_signal import SignalEngine
        from core.backtest_state import BacktestState
        from core.backtest_trade import BacktestTradeMixin
        from core.backtest_trailing import BacktestTrailingMixin

        class _Host(
            BacktestNumericMixin,
            BacktestTrailingMixin,
            BacktestAccountingMixin,
            BacktestTradeMixin,
        ):
            def _get_commission_per_side(self) -> float:
                return float(self.params.real_commission_per_side)

        self._host = _Host()
        self._host.params = params
        self._host.logger = logger
        initial_cash = self._host._money(params.initial_cash)
        state = BacktestState(
            virtual_cash=initial_cash,
            final_virtual_equity=initial_cash,
        )
        self._host.state = state
        self._host.market = Market()
        self._host._execution_engine = ExecutionEngine()
        self._host._execution_context = BacktestExecutionContext(self._host)
        self._host._signal_engine = SignalEngine(
            params, logger, self._host._price
        )
        self._host._accounting_engine = AccountingEngine(
            state,
            params,
            self._host._money,
            float(params.real_commission_per_side),
        )

        # NativeBacktestContext delegates the lifecycle methods used by the
        # execution context while keeping all production algorithms intact.
        self.state = state
        self.market = self._host.market
        self._execution_engine = self._host._execution_engine
        self._execution_context = self._host._execution_context
        self._signal_engine = self._host._signal_engine
        from core.backtest_loop import BacktestEngine
        self._backtest_engine = BacktestEngine(self._host)

    def process_bar(self, bar) -> None:
        self._backtest_engine.process_bar(bar)

    @property
    def logger(self):
        return self._host.logger

    def finish(self):
        # Replicate BacktestAccountingMixin.stop() without requiring a
        # Backtrader Strategy.stop() call.
        final_bar = self.market.current_bar
        final_close = final_bar.close if final_bar is not None else None
        unrealized = self._host._unrealized_pnl(final_close)
        self.state.virtual_cash = self._host._money(self.state.virtual_cash)
        self.state.final_virtual_equity = self._host._money(
            self.state.virtual_cash + unrealized
        )
        self._host._check_negative_cash()
        self._host._check_trade_lifecycle()
        self._host._check_accounting()
        return result_dict(self.state, self._host.params)


def bars_from_dataframe(prepared):
    from core.backtest_bar import Bar
    for row in prepared.itertuples(index=False):
        yield Bar(
            datetime=row.DATETIME.to_pydatetime(),
            open=float(row.OPEN),
            high=float(row.HIGH),
            low=float(row.LOW),
            close=float(row.CLOSE),
            volume=int(round(float(row.VOLUME))),
        )


def run_native(cfg, prepared):
    from core.backtest_logger import BacktestLogger

    logger = BacktestLogger(
        str(RESULTS_DIR / "_stage82_native_none.log"), reset=True, mode="NONE"
    )
    params = make_params(cfg)
    params.real_commission_per_side = cfg.REAL_COMMISSION / 2
    ctx = NativeBacktestContext(params, logger)
    try:
        for bar in bars_from_dataframe(prepared):
            ctx.process_bar(bar)
        result = ctx.finish()
    finally:
        logger.close()
    validate_result(result, "NATIVE")
    return result


def run_backtrader_off(cfg, processed_path):
    import backtrader as bt
    from core.backtest_adapter import BacktraderFeedAdapter
    from core.backtest_engine import RealisticFuturesStrategy
    from core.backtest_logger import BacktestLogger

    logger = BacktestLogger(
        str(RESULTS_DIR / "_stage82_bt_none.log"), reset=True, mode="NONE"
    )
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.addstrategy(
        RealisticFuturesStrategy,
        trigger=cfg.TRIGGER_SPREAD,
        tp=cfg.TAKE_PROFIT,
        sl=cfg.STOP_LOSS,
        risk=cfg.OFFER_RISK,
        real_mult=cfg.REAL_MULT,
        real_margin=cfg.REAL_MARGIN,
        safety_factor=cfg.SAFETY_FACTOR,
        precision_num=cfg.PRECISION_NUM,
        precision_money=cfg.PRECISION_NUM_DEPO_RUB,
        dynamic_trail_steps=cfg.DYNAMIC_TRAIL_STEPS,
        logger=logger,
        initial_cash=cfg.INITIAL_CASH,
        real_commission_per_side=cfg.REAL_COMMISSION / 2,
    )
    cerebro.adddata(BacktraderFeedAdapter.create_feed(processed_path))
    cerebro.broker.setcash(cfg.INITIAL_CASH)
    try:
        strategies = cerebro.run()
    finally:
        logger.close()
    s = strategies[0]
    result = {
        "final_portfolio_value": round(float(s.state.final_virtual_equity), 2),
        "real_net_profit": round(float(s.state.final_virtual_equity) - float(cfg.INITIAL_CASH), 2),
        "total_closed_trades": int(s.state.closed_trades),
        "total_contracts": int(s.state.total_contracts),
        "total_commission": round(float(s.state.total_commission), 2),
        "open_position_size": int(s.state.virtual_position_size),
        "virtual_cash": round(float(s.state.virtual_cash), 2),
    }
    validate_result(result, "BACKTRADER_OFF")
    return result


def run_measured(cfg, prepared, processed_path, variant):
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    if variant == "NATIVE":
        result = run_native(cfg, prepared)
    else:
        result = run_backtrader_off(cfg, processed_path)
    return time.perf_counter() - wall_start, time.process_time() - cpu_start, result


def save_sample(sample: RunSample, bars: int, source_csv: Path) -> Path:
    ts = datetime.now().strftime("%d_%m_%Y_%H_%M_%S")
    path = RESULTS_DIR / f"stage82_{sample.variant.lower()}_{ts}.json"
    if path.exists():
        i = 2
        while True:
            candidate = RESULTS_DIR / f"stage82_{sample.variant.lower()}_{ts}_{i:02d}.json"
            if not candidate.exists():
                path = candidate
                break
            i += 1
    payload = {
        "stage": "8.2",
        "experiment": "backtrader_off_vs_native_runner",
        "variant": sample.variant,
        "stdstats": False if sample.variant == "BACKTRADER_OFF" else None,
        "benchmark_timestamp": ts,
        "instrument": "BRU6",
        "run_number": sample.run_number,
        "source_csv": str(source_csv),
        "bars": bars,
        "wall_seconds": sample.wall_seconds,
        "cpu_seconds": sample.cpu_seconds,
        "result": sample.result,
        "invariants_passed": True,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def summarize(samples):
    walls = [s.wall_seconds for s in samples]
    cpus = [s.cpu_seconds for s in samples]
    mean = statistics.mean(walls)
    return {
        "min": min(walls),
        "median": statistics.median(walls),
        "mean": mean,
        "max": max(walls),
        "stdev": statistics.stdev(walls) if len(walls) > 1 else 0.0,
        "cv_pct": statistics.stdev(walls) / mean * 100 if len(walls) > 1 else 0.0,
        "cpu_median": statistics.median(cpus),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--runs", type=int, default=10)
    args = parser.parse_args()
    if args.warmup < 0 or args.runs < 1:
        raise SystemExit("warmup >= 0 and runs >= 1 required")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()

    from core.backtest_data import export_backtrader_adapter, load_and_prepare_backtest_dataframe
    from core.backtest_logger import BacktestLogger
    from core.backtest_validation import validate_backtest_dataframe

    source_csv = PROJECT_ROOT / "03_BRENT" / cfg.TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH
    prepared = load_and_prepare_backtest_dataframe(str(source_csv))
    validation_logger = BacktestLogger(
        str(RESULTS_DIR / "_stage82_validation.log"), reset=True, mode="NONE"
    )
    try:
        validate_backtest_dataframe(prepared, validation_logger)
    finally:
        validation_logger.close()

    bars = int(len(prepared))
    fd, processed_path = tempfile.mkstemp(prefix="stage82_bt_", suffix=".csv")
    os.close(fd)
    try:
        export_backtrader_adapter(prepared, processed_path)

        all_samples = {}
        for variant in ("BACKTRADER_OFF", "NATIVE"):
            for _ in range(args.warmup):
                run_measured(cfg, prepared, processed_path, variant)

            samples = []
            for run_number in range(1, args.runs + 1):
                wall, cpu, result = run_measured(
                    cfg, prepared, processed_path, variant
                )
                sample = RunSample(variant, run_number, wall, cpu, result)
                samples.append(sample)
                saved = save_sample(sample, bars, source_csv)
                print(
                    f"{variant} run {run_number}/{args.runs}: "
                    f"wall={wall:.6f}s cpu={cpu:.6f}s -> {saved.name}"
                )

            stats = summarize(samples)
            all_samples[variant] = stats
            print(
                f"{variant}: min={stats['min']:.6f}s "
                f"median={stats['median']:.6f}s mean={stats['mean']:.6f}s "
                f"max={stats['max']:.6f}s CV={stats['cv_pct']:.3f}%"
            )

        bt_median = all_samples["BACKTRADER_OFF"]["median"]
        native_median = all_samples["NATIVE"]["median"]
        saving = bt_median - native_median
        saving_pct = saving / bt_median * 100
        print(
            f"NATIVE COMPARISON: Backtrader stdstats=False median={bt_median:.6f}s; "
            f"native median={native_median:.6f}s"
        )
        print(
            f"BACKTRADER OVERHEAD REMOVED: {saving:.6f}s ({saving_pct:.3f}%)"
        )
        return 0
    finally:
        try:
            os.remove(processed_path)
        except FileNotFoundError:
            pass
        for temporary in (
            RESULTS_DIR / "_stage82_native_none.log",
            RESULTS_DIR / "_stage82_bt_none.log",
            RESULTS_DIR / "_stage82_validation.log",
        ):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
