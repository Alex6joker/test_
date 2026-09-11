# BACKTEST EXECUTION MODEL
# Virtual execution model for historical backtests.
#
# Rules:
# - Signal uses the previous available candle.
# - Entry is at the current candle Open.
# - The newly opened position is exposed to the remainder of that same candle.
# - Intrabar processing follows a deterministic monotonic price path:
#     bullish/doji: Open -> Low -> High -> Close
#     bearish:      Open -> High -> Low -> Close
# - Trail/SL/TP are processed in chronological price-crossing order.
# - ATR is diagnostic only and MUST NOT affect strategy warm-up.
# - Virtual accounting is the source of truth; Backtrader analyzers are not used.
from __future__ import annotations

from typing import Any

import backtrader as bt

from core.backtest_accounting import BacktestAccountingMixin
from core.backtest_adapter import BacktraderBarAdapter
from core.backtest_market import Market
from core.backtest_execution import (
    BacktestExecutionContext,
    ExecutionEngine,
)
from core.backtest_logger import BacktestLogger
from core.backtest_numeric import BacktestNumericMixin
from core.backtest_signal import BacktestSignalMixin, SignalEngine
from core.backtest_state import BacktestState
from core.backtest_trade import BacktestTradeMixin
from core.backtest_trailing import BacktestTrailingMixin


class RealisticFuturesStrategy(
    BacktestNumericMixin,
    BacktestSignalMixin,
    BacktestTrailingMixin,
    BacktestTradeMixin,
    BacktestAccountingMixin,
    bt.Strategy,
):
    """Virtual intrabar futures backtester."""

    params = (
        ("trigger", None),
        ("tp", None),
        ("sl", None),
        ("risk", None),
        ("real_mult", None),
        ("real_margin", None),
        ("safety_factor", 1.1),
        ("precision_num", 2),
        ("precision_money", 2),
        ("slippage_points", 0.02),
        ("debug", False),
        ("dynamic_trail_steps", []),
        ("logger", None),
        ("initial_cash", 0.0),
        ("real_commission_per_side", 0.0),
    )
    def __init__(self):
        self.logger = self.params.logger or BacktestLogger()

        initial_cash = self._money(self.params.initial_cash)
        self.state = BacktestState(
            virtual_cash=initial_cash,
            final_virtual_equity=initial_cash,
        )
        self.market = Market()
        self._bar_adapter = BacktraderBarAdapter()
        self._execution_engine = ExecutionEngine()
        self._execution_context = BacktestExecutionContext(self)
        self._signal_engine = SignalEngine(self.params, self.logger, self._price)

        # BacktestState is the single owner of mutable virtual-backtest state.
        # Deliberately no ATR indicator:
        # any Backtrader indicator can impose a minperiod and delay the strategy.

        if self.logger.wants_event("STRATEGY_INIT"):
            self.logger.event(
                "STRATEGY_INIT",
                trigger=self.params.trigger,
                tp=self.params.tp,
                sl=self.params.sl,
                risk=self.params.risk,
                real_mult=self.params.real_mult,
                real_margin=self.params.real_margin,
                safety_factor=self.params.safety_factor,
                precision_num=self.params.precision_num,
                precision_money=self.params.precision_money,
                dynamic_trail_steps=self.params.dynamic_trail_steps,
                execution_model="VIRTUAL",
                entry_model="CURRENT_BAR_OPEN",
                signal_model="PREVIOUS_AVAILABLE_BAR",
                intrabar_model="ORDERED_MONOTONIC_PATH",
                atr_role="DIAGNOSTIC_ONLY",
                initial_cash=self.state.virtual_cash,
            )
