from __future__ import annotations

import os

import backtrader as bt

from core.backtest_engine import RealisticFuturesStrategy
from core.backtest_result import BacktestResult


def run_backtest(
    processed_path: str,
    cfg,
    logger,
    precision_money: int,
) -> BacktestResult:
    """Configure and run Backtrader; return only virtual source-of-truth results."""
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

    return BacktestResult(
        final_portfolio_value=round(
            float(first_strat.final_virtual_equity),
            precision_money,
        ),
        real_net_profit=round(
            float(first_strat.final_virtual_equity) - float(cfg.INITIAL_CASH),
            precision_money,
        ),
        total_closed_trades=int(first_strat.closed_trades),
        total_contracts=int(first_strat.total_contracts),
        total_commission=round(
            float(first_strat.total_commission),
            precision_money,
        ),
        open_position_size=first_strat.virtual_position_size,
        virtual_cash=round(
            float(first_strat.virtual_cash),
            precision_money,
        ),
    )
