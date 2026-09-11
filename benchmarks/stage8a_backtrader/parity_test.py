"""Stage 8.2.1: Backtrader-vs-Native parity test.

Production code is not modified. This test runs both paths on the same
prepared data and compares:
- complete TradeLedger records;
- all FAST-mode TRADE lifecycle lines, including TRAIL_UPDATE;
- final virtual accounting invariants.

FAST mode is intentional: it records trade/lifecycle events without creating
the full multi-million-line DIAGNOSTIC log.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
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


def load_config():
    path = PROJECT_ROOT / "03_BRENT" / "config.py"
    spec = importlib.util.spec_from_file_location("stage82_parity_config", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load config: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        real_commission_per_side=cfg.REAL_COMMISSION / 2,
    )


def result_dict(state, params):
    return {
        "final_portfolio_value": round(float(state.final_virtual_equity), 2),
        "real_net_profit": round(
            float(state.final_virtual_equity) - float(params.initial_cash), 2
        ),
        "total_closed_trades": int(state.closed_trades),
        "total_contracts": int(state.total_contracts),
        "total_commission": round(float(state.total_commission), 2),
        "open_position_size": int(state.virtual_position_size),
        "virtual_cash": round(float(state.virtual_cash), 2),
    }


def read_trade_lines(path: Path) -> list[str]:
    lines = []
    if not path.exists():
        return lines
    for line in path.read_text(encoding="utf-8").splitlines():
        marker = " | TRADE | "
        if marker in line:
            lines.append(line.split(marker, 1)[1])
    return lines


def normalize_record(record: dict) -> dict:
    result = dict(record)
    for key in ("entry_datetime",):
        value = result.get(key)
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat(sep=" ")
    return result


def normalize_records(records: list[dict]) -> list[dict]:
    return [normalize_record(record) for record in records]


def compare_records(bt_records, native_records):
    bt_norm = normalize_records(bt_records)
    native_norm = normalize_records(native_records)
    if bt_norm == native_norm:
        return True, None

    limit = min(len(bt_norm), len(native_norm))
    for index in range(limit):
        if bt_norm[index] != native_norm[index]:
            return False, {
                "trade_number": index + 1,
                "backtrader": bt_norm[index],
                "native": native_norm[index],
            }

    return False, {
        "reason": "different record count",
        "backtrader_count": len(bt_norm),
        "native_count": len(native_norm),
    }


def compare_lists(name, expected, actual):
    if expected == actual:
        return True, None
    limit = min(len(expected), len(actual))
    for index in range(limit):
        if expected[index] != actual[index]:
            return False, {
                "sequence": name,
                "item_number": index + 1,
                "backtrader": expected[index],
                "native": actual[index],
            }
    return False, {
        "sequence": name,
        "reason": "different length",
        "backtrader_count": len(expected),
        "native_count": len(actual),
    }


def run_native(cfg, prepared, log_path):
    from core.backtest_logger import BacktestLogger
    from core.backtest_bar import Bar
    spec = importlib.util.spec_from_file_location(
        "stage82_native_benchmark",
        BENCHMARK_DIR / "native_benchmark.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load native_benchmark.py")
    native_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = native_module
    spec.loader.exec_module(native_module)
    NativeBacktestContext = native_module.NativeBacktestContext

    logger = BacktestLogger(str(log_path), reset=True, mode="FAST")
    params = make_params(cfg)
    ctx = NativeBacktestContext(params, logger)
    try:
        for row in prepared.itertuples(index=False):
            ctx.process_bar(
                Bar(
                    datetime=row.DATETIME.to_pydatetime(),
                    open=float(row.OPEN),
                    high=float(row.HIGH),
                    low=float(row.LOW),
                    close=float(row.CLOSE),
                    volume=int(round(float(row.VOLUME))),
                )
            )
        result = ctx.finish()
        records = list(ctx.state.trade_records)
    finally:
        logger.close()
    return result, records


def run_backtrader(cfg, processed_path, log_path):
    import backtrader as bt
    from core.backtest_adapter import BacktraderFeedAdapter
    from core.backtest_engine import RealisticFuturesStrategy
    from core.backtest_logger import BacktestLogger

    logger = BacktestLogger(str(log_path), reset=True, mode="FAST")
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
    cerebro.broker.setcommission(
        commission=cfg.REAL_COMMISSION / 2,
        margin=cfg.REAL_MARGIN,
        mult=cfg.REAL_MULT,
        stocklike=False,
        commtype=bt.CommInfoBase.COMM_FIXED,
    )
    try:
        strategies = cerebro.run()
    finally:
        logger.close()

    strategy = strategies[0]
    result = result_dict(strategy.state, strategy.params)
    records = list(strategy.state.trade_records)
    return result, records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="stage82_parity_result.json")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()

    from core.backtest_data import export_backtrader_adapter, load_and_prepare_backtest_dataframe
    from core.backtest_logger import BacktestLogger
    from core.backtest_validation import validate_backtest_dataframe

    source_csv = PROJECT_ROOT / "03_BRENT" / cfg.TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH
    prepared = load_and_prepare_backtest_dataframe(str(source_csv))

    validation_logger = BacktestLogger(
        str(RESULTS_DIR / "_stage82_parity_validation.log"),
        reset=True,
        mode="NONE",
    )
    try:
        validate_backtest_dataframe(prepared, validation_logger)
    finally:
        validation_logger.close()

    fd, processed_path = tempfile.mkstemp(prefix="stage82_parity_", suffix=".csv")
    os.close(fd)

    bt_log = RESULTS_DIR / "_stage82_parity_backtrader_fast.log"
    native_log = RESULTS_DIR / "_stage82_parity_native_fast.log"
    output_path = RESULTS_DIR / args.output

    try:
        export_backtrader_adapter(prepared, processed_path)

        bt_result, bt_records = run_backtrader(cfg, processed_path, bt_log)
        native_result, native_records = run_native(cfg, prepared, native_log)

        checks = {}

        checks["FINAL_RESULT"] = bt_result == native_result == EXPECTED
        checks["TRADE_LEDGER"] = compare_records(
            bt_records, native_records
        )[0]

        bt_trade_lines = read_trade_lines(bt_log)
        native_trade_lines = read_trade_lines(native_log)
        checks["TRADE_EVENT_SEQUENCE"] = compare_lists(
            "TRADE_EVENT_SEQUENCE",
            bt_trade_lines,
            native_trade_lines,
        )[0]

        all_pass = all(checks.values())

        diagnostics = {}
        if not checks["TRADE_LEDGER"]:
            diagnostics["TRADE_LEDGER"] = compare_records(
                bt_records, native_records
            )[1]
        if not checks["TRADE_EVENT_SEQUENCE"]:
            diagnostics["TRADE_EVENT_SEQUENCE"] = compare_lists(
                "TRADE_EVENT_SEQUENCE",
                bt_trade_lines,
                native_trade_lines,
            )[1]
        if not checks["FINAL_RESULT"]:
            diagnostics["FINAL_RESULT"] = {
                "expected": EXPECTED,
                "backtrader": bt_result,
                "native": native_result,
            }

        payload = {
            "stage": "8.2.1",
            "experiment": "backtrader_off_vs_native_parity",
            "instrument": "BRU6",
            "bars": int(len(prepared)),
            "checks": checks,
            "passed": all_pass,
            "backtrader": {
                "result": bt_result,
                "trade_count": len(bt_records),
                "trade_event_lines": len(bt_trade_lines),
            },
            "native": {
                "result": native_result,
                "trade_count": len(native_records),
                "trade_event_lines": len(native_trade_lines),
            },
            "diagnostics": diagnostics,
        }
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        print("STAGE 8.2.1 PARITY")
        for name, passed in checks.items():
            print(f"{name:<28} {'PASS' if passed else 'FAIL'}")
        print(f"BACKTRADER trades: {len(bt_records)}")
        print(f"NATIVE trades:     {len(native_records)}")
        print(f"BACKTRADER trade events: {len(bt_trade_lines)}")
        print(f"NATIVE trade events:     {len(native_trade_lines)}")
        print(f"PARITY: {'PASS' if all_pass else 'FAIL'}")
        print(f"Result: {output_path}")

        return 0 if all_pass else 1
    finally:
        for path in (processed_path, bt_log, native_log,
                     RESULTS_DIR / "_stage82_parity_validation.log"):
            try:
                Path(path).unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
