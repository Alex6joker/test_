from __future__ import annotations

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
RESULTS_DIR = BENCHMARK_DIR / 'results'
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXPECTED = {
    'final_portfolio_value': 214018.69,
    'real_net_profit': -49981.31,
    'total_closed_trades': 151,
    'total_contracts': 1500,
    'total_commission': 15319.34,
    'open_position_size': 4,
    'virtual_cash': 217437.49,
}


def load_config():
    path = PROJECT_ROOT / '03_BRENT' / 'config.py'
    spec = importlib.util.spec_from_file_location('stage8_observer_config', path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Unable to load config: {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def result_dict(result):
    return {
        'final_portfolio_value': result.final_portfolio_value,
        'real_net_profit': result.real_net_profit,
        'total_closed_trades': result.total_closed_trades,
        'total_contracts': result.total_contracts,
        'total_commission': result.total_commission,
        'open_position_size': result.open_position_size,
        'virtual_cash': result.virtual_cash,
    }


def run_variant(cfg, processed_path: str, stdstats: bool):
    import backtrader as bt
    from core.backtest_adapter import BacktraderFeedAdapter
    from core.backtest_engine import RealisticFuturesStrategy
    from core.backtest_logger import BacktestLogger
    from core.backtest_result import BacktestResult

    logger = BacktestLogger(
        str(RESULTS_DIR / '_observer_none.log'), reset=True, mode='NONE'
    )
    cerebro = bt.Cerebro(stdstats=stdstats)
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
    )
    cerebro.adddata(BacktraderFeedAdapter.create_feed(processed_path))
    cerebro.broker.setcash(cfg.INITIAL_CASH)
    cerebro.broker.setcommission(
        commission=cfg.REAL_COMMISSION / 2,
        margin=cfg.REAL_MARGIN,
        mult=cfg.REAL_MULT,
        stocklike=False,
        commtype=bt.CommInfoBase.COMM_FIXED,
    )

    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    try:
        strategies = cerebro.run()
    finally:
        logger.close()
    wall = time.perf_counter() - wall_start
    cpu = time.process_time() - cpu_start
    s = strategies[0]
    result = BacktestResult(
        final_portfolio_value=round(float(s.state.final_virtual_equity), cfg.PRECISION_NUM_DEPO_RUB),
        real_net_profit=round(float(s.state.final_virtual_equity) - float(cfg.INITIAL_CASH), cfg.PRECISION_NUM_DEPO_RUB),
        total_closed_trades=int(s.state.closed_trades),
        total_contracts=int(s.state.total_contracts),
        total_commission=round(float(s.state.total_commission), cfg.PRECISION_NUM_DEPO_RUB),
        open_position_size=s.state.virtual_position_size,
        virtual_cash=round(float(s.state.virtual_cash), cfg.PRECISION_NUM_DEPO_RUB),
    )
    actual = result_dict(result)
    if actual != EXPECTED:
        raise RuntimeError(f'Invariant failed stdstats={stdstats}: {actual} != {EXPECTED}')
    return wall, cpu, actual


def stamp(prefix: str) -> str:
    base = datetime.now().strftime('%d_%m_%Y_%H_%M_%S')
    p = RESULTS_DIR / f'{prefix}_{base}.json'
    if not p.exists():
        return base
    i = 2
    while (RESULTS_DIR / f'{prefix}_{base}_{i:02d}.json').exists():
        i += 1
    return f'{base}_{i:02d}'


def save_variant(variant, stdstats, run_number, wall, cpu, bars, source_csv, result):
    ts = stamp(f'observers_{variant}')
    payload = {
        'stage': '8.1',
        'experiment': 'backtrader_stdstats',
        'variant': variant,
        'stdstats': stdstats,
        'benchmark_timestamp': ts,
        'instrument': 'BRU6',
        'run_number': run_number,
        'source_csv': str(source_csv),
        'bars': bars,
        'wall_seconds': wall,
        'cpu_seconds': cpu,
        'result': result,
        'invariants_passed': True,
    }
    path = RESULTS_DIR / f'observers_{variant}_{ts}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def main():
    import argparse
    from core.backtest_data import export_backtrader_adapter, load_and_prepare_backtest_dataframe
    from core.backtest_logger import BacktestLogger
    from core.backtest_validation import validate_backtest_dataframe

    parser = argparse.ArgumentParser()
    parser.add_argument('--warmup', type=int, default=2)
    parser.add_argument('--runs', type=int, default=10)
    args = parser.parse_args()
    if args.warmup < 0 or args.runs < 1:
        raise SystemExit('warmup >= 0 and runs >= 1 required')

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    source_csv = PROJECT_ROOT / '03_BRENT' / cfg.TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH
    prepared = load_and_prepare_backtest_dataframe(str(source_csv))
    vlogger = BacktestLogger(str(RESULTS_DIR / '_observer_validation.log'), reset=True, mode='NONE')
    try:
        validate_backtest_dataframe(prepared, vlogger)
    finally:
        vlogger.close()
    bars = len(prepared)
    fd, processed_path = tempfile.mkstemp(prefix='stage8a_observers_', suffix='.csv')
    os.close(fd)
    try:
        export_backtrader_adapter(prepared, processed_path)
        all_samples = {}
        for variant, stdstats in [('ON', True), ('OFF', False)]:
            for _ in range(args.warmup):
                run_variant(cfg, processed_path, stdstats)
            samples = []
            cpus = []
            for n in range(1, args.runs + 1):
                wall, cpu, result = run_variant(cfg, processed_path, stdstats)
                samples.append(wall)
                cpus.append(cpu)
                saved = save_variant(variant, stdstats, n, wall, cpu, bars, source_csv, result)
                print(f'{variant} run {n}/{args.runs}: wall={wall:.6f}s cpu={cpu:.6f}s -> {saved.name}')
            stats = {
                'min': min(samples),
                'median': statistics.median(samples),
                'mean': statistics.mean(samples),
                'max': max(samples),
                'stdev': statistics.stdev(samples) if len(samples) > 1 else 0.0,
                'cv_pct': statistics.stdev(samples) / statistics.mean(samples) * 100 if len(samples) > 1 else 0.0,
                'cpu_median': statistics.median(cpus),
            }
            all_samples[variant] = stats
            print(f'{variant}: min={stats["min"]:.6f}s median={stats["median"]:.6f}s mean={stats["mean"]:.6f}s max={stats["max"]:.6f}s CV={stats["cv_pct"]:.3f}%')

        on = all_samples['ON']['median']
        off = all_samples['OFF']['median']
        saving = on - off
        saving_pct = saving / on * 100
        print(f'OBSERVER COMPARISON: stdstats ON median={on:.6f}s; OFF median={off:.6f}s')
        print(f'OBSERVERS SAVING: {saving:.6f}s ({saving_pct:.3f}%)')
    finally:
        try:
            os.unlink(processed_path)
        except OSError:
            pass


if __name__ == '__main__':
    raise SystemExit(main())
