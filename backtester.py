from __future__ import annotations

import os
import uuid

from core.backtest_data import export_backtrader_adapter, load_and_prepare_backtest_dataframe
from core.backtest_logger import BacktestLogger
from core.backtest_result import BacktestResult
from core.backtest_runner import run_backtest
from core.backtest_validation import validate_backtest_dataframe


def _log_backtest_result(logger: BacktestLogger, result: BacktestResult) -> None:
    """Write the final result in the established diagnostic format."""
    logger.event(
        "BACKTEST_RESULT",
        execution_model="VIRTUAL",
        final_portfolio_value=result.final_portfolio_value,
        real_net_profit=result.real_net_profit,
        total_closed_trades=result.total_closed_trades,
        total_contracts=result.total_contracts,
        total_commission=result.total_commission,
        open_position_size=result.open_position_size,
        virtual_cash=result.virtual_cash,
    )


def run_instrument_backtest(instrument_folder, cfg):
    """Orchestrate the virtual futures backtest for one instrument."""
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
        validate_backtest_dataframe(prepared, logger)

        # This is only a temporary adapter for Backtrader. The source CSV is
        # never sorted, filled, or otherwise modified.
        export_backtrader_adapter(prepared, processed_path)

        result = run_backtest(
            processed_path=processed_path,
            cfg=cfg,
            logger=logger,
            precision_money=precision_money,
        )
        _log_backtest_result(logger, result)

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
