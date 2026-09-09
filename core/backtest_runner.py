from __future__ import annotations

import os

import backtrader as bt

from core.backtest_engine import RealisticFuturesStrategy
from core.backtest_result import BacktestResult
from core.backtest_adapter import BacktraderFeedAdapter


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

    data = BacktraderFeedAdapter.create_feed(processed_path)
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

    if logger.wants_event("BROKER_START"):
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
            float(first_strat.state.final_virtual_equity),
            precision_money,
        ),
        real_net_profit=round(
            float(first_strat.state.final_virtual_equity) - float(cfg.INITIAL_CASH),
            precision_money,
        ),
        total_closed_trades=int(first_strat.state.closed_trades),
        total_contracts=int(first_strat.state.total_contracts),
        total_commission=round(
            float(first_strat.state.total_commission),
            precision_money,
        ),
        open_position_size=first_strat.state.virtual_position_size,
        virtual_cash=round(
            float(first_strat.state.virtual_cash),
            precision_money,
        ),
    )
