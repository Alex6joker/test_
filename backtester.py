from __future__ import annotations

import math
import os
import uuid

import backtrader as bt
import pandas as pd

from core.backtest_data import export_backtrader_adapter, load_and_prepare_backtest_dataframe
from core.backtest_engine import RealisticFuturesStrategy
from core.backtest_logger import BacktestLogger


def _validate_input_dataframe(df: pd.DataFrame, logger: BacktestLogger) -> None:
    """Validate input without sorting, filling, dropping, or rewriting rows."""
    required = {"DATETIME", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"CSV validation failed: missing columns = {sorted(missing)}")

    if df.empty:
        raise ValueError("CSV validation failed: dataset is empty")

    dt = pd.to_datetime(df["DATETIME"], errors="coerce")
    if dt.isna().any():
        bad_rows = (dt.isna()).to_numpy().nonzero()[0][:10].tolist()
        raise ValueError(
            f"CSV validation failed: invalid timestamps at rows = {bad_rows}"
        )

    if not dt.is_monotonic_increasing:
        raise ValueError("CSV validation failed: timestamps are not ordered increasingly")

    if dt.duplicated().any():
        duplicate_rows = dt[dt.duplicated()].index[:10].tolist()
        raise ValueError(
            f"CSV validation failed: duplicate timestamps at rows = {duplicate_rows}"
        )

    numeric_columns = ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]
    for column in numeric_columns:
        values = pd.to_numeric(df[column], errors="coerce")
        if values.isna().any():
            bad_rows = values.isna().to_numpy().nonzero()[0][:10].tolist()
            raise ValueError(
                f"CSV validation failed: invalid {column} at rows = {bad_rows}"
            )
        if not values.map(math.isfinite).all():
            raise ValueError(f"CSV validation failed: non-finite values in {column}")

    opens = pd.to_numeric(df["OPEN"], errors="coerce")
    highs = pd.to_numeric(df["HIGH"], errors="coerce")
    lows = pd.to_numeric(df["LOW"], errors="coerce")
    closes = pd.to_numeric(df["CLOSE"], errors="coerce")
    volumes = pd.to_numeric(df["VOLUME"], errors="coerce")

    if (highs < pd.concat([opens, closes], axis=1).max(axis=1)).any():
        raise ValueError("CSV validation failed: HIGH is below OPEN/CLOSE")
    if (lows > pd.concat([opens, closes], axis=1).min(axis=1)).any():
        raise ValueError("CSV validation failed: LOW is above OPEN/CLOSE")
    if (highs < lows).any():
        raise ValueError("CSV validation failed: HIGH is below LOW")
    if (volumes < 0).any():
        raise ValueError("CSV validation failed: negative VOLUME")

    gap_mask = dt.diff() > pd.Timedelta(minutes=1)
    gap_count = int(gap_mask.sum())
    gap_examples = [
        {
            "previous_datetime": dt.iloc[i - 1],
            "datetime": dt.iloc[i],
            "gap_minutes": (dt.iloc[i] - dt.iloc[i - 1]).total_seconds() / 60.0,
        }
        for i in range(1, len(dt))
        if gap_mask.iloc[i]
    ][:10]

    logger.event(
        "CSV_VALIDATION",
        rows=len(df),
        first_datetime=dt.iloc[0],
        last_datetime=dt.iloc[-1],
        duplicate_timestamps=0,
        timestamp_order="INCREASING",
        gaps_gt_1_minute=gap_count,
        gap_examples=gap_examples,
        data_mutation="NONE",
        previous_available_row_rule=True,
        passed=True,
    )


def run_instrument_backtest(instrument_folder, cfg):
    """Run the virtual futures backtest for one instrument."""
    csv_path = os.path.join(
        instrument_folder,
        cfg.TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH,
    )

    unique_id = uuid.uuid4().hex[:8]
    processed_path = f"temp_bt_ready_{unique_id}.csv"

    precision_money = getattr(
        cfg,
        "PRECISION_NUM_DEPO_RUB",
        cfg.PRECISION_NUM,
    )

    logger = BacktestLogger(
        os.path.join(
            os.path.dirname(__file__),
            "logs",
            "backtest_diagnostic.log",
        ),
        reset=True,
    )

    logger.section(f"BACKTEST START: {cfg.FUT_SEC_CODE}")
    logger.event(
        "INPUT",
        instrument_folder=instrument_folder,
        csv_path=csv_path,
        initial_cash=cfg.INITIAL_CASH,
        trigger=cfg.TRIGGER_SPREAD,
        take_profit=cfg.TAKE_PROFIT,
        stop_loss=cfg.STOP_LOSS,
        offer_risk=cfg.OFFER_RISK,
        precision_money=precision_money,
    )

    if not os.path.exists(csv_path):
        logger.error(f"DATA_FILE_NOT_FOUND csv_path = {csv_path}")
        logger.close()
        return

    try:
        prepared = load_and_prepare_backtest_dataframe(csv_path)
        _validate_input_dataframe(prepared, logger)

        # This is only a temporary adapter for Backtrader. The source CSV is
        # never sorted, filled, or otherwise modified.
        export_backtrader_adapter(prepared, processed_path)

        cerebro = bt.Cerebro()

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
            precision_money=precision_money,
            dynamic_trail_steps=cfg.DYNAMIC_TRAIL_STEPS,
            logger=logger,
            initial_cash=cfg.INITIAL_CASH,
        )

        data = bt.feeds.GenericCSVData(
            dataname=processed_path,
            sep=",",
            dtformat="%Y%m%d %H%M%S",
            timeframe=bt.TimeFrame.Minutes,
            datetime=0,
            time=-1,
            open=1,
            high=2,
            low=3,
            close=4,
            volume=5,
            openinterest=-1,
            headers=True,
        )
        cerebro.adddata(data)

        # Backtrader's broker is deliberately not the source of fills/P&L.
        # Its commission configuration exists only so the strategy can read
        # the same per-side commission value.
        cerebro.broker.setcash(cfg.INITIAL_CASH)
        cerebro.broker.setcommission(
            commission=cfg.REAL_COMMISSION / 2,
            margin=cfg.REAL_MARGIN,
            mult=cfg.REAL_MULT,
            stocklike=False,
            commtype=bt.CommInfoBase.COMM_FIXED,
        )

        logger.event(
            "BROKER_START",
            cash=cerebro.broker.getcash(),
            value=cerebro.broker.getvalue(),
            commission_per_side=cfg.REAL_COMMISSION / 2,
            margin=cfg.REAL_MARGIN,
            mult=cfg.REAL_MULT,
            source_of_truth="VIRTUAL_STRATEGY",
        )

        strategies = cerebro.run()
        first_strat = strategies[0]

        final_portfolio_value = round(
            float(first_strat.final_virtual_equity),
            precision_money,
        )
        real_net_profit = round(
            final_portfolio_value - float(cfg.INITIAL_CASH),
            precision_money,
        )

        total_closed_trades = int(first_strat.closed_trades)
        total_contracts = int(first_strat.total_contracts)
        total_commission = round(
            float(first_strat.total_commission),
            precision_money,
        )

        logger.event(
            "BACKTEST_RESULT",
            execution_model="VIRTUAL",
            final_portfolio_value=final_portfolio_value,
            real_net_profit=real_net_profit,
            total_closed_trades=total_closed_trades,
            total_contracts=total_contracts,
            total_commission=total_commission,
            open_position_size=first_strat.virtual_position_size,
            virtual_cash=round(
                float(first_strat.virtual_cash),
                precision_money,
            ),
        )

    except Exception as exc:
        logger.error(
            f"BACKTEST_EXCEPTION type = {type(exc).__name__}; message = {exc}"
        )
        raise
    finally:
        if os.path.exists(processed_path):
            os.remove(processed_path)
        logger.section("BACKTEST END")
        logger.close()
