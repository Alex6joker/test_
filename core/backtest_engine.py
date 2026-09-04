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
from core.backtest_commission import BacktestCommissionMixin
from core.backtest_compat import ContractVolumeAnalyzer, PreviousBarRange
from core.backtest_execution import BacktestExecutionMixin
from core.backtest_logger import BacktestLogger
from core.backtest_numeric import BacktestNumericMixin
from core.backtest_signal import BacktestSignalMixin
from core.backtest_trade import BacktestTradeMixin
from core.backtest_trailing import BacktestTrailingMixin


class RealisticFuturesStrategy(
    BacktestNumericMixin,
    BacktestSignalMixin,
    BacktestExecutionMixin,
    BacktestTrailingMixin,
    BacktestTradeMixin,
    BacktestAccountingMixin,
    BacktestCommissionMixin,
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
    )
    def __init__(self):
        self.logger = self.params.logger or BacktestLogger()

        self.trade_id = 0
        self.last_trade_bar = -1

        self.virtual_cash = self._money(self.params.initial_cash)
        self.virtual_position_size = 0
        self.virtual_entry_price = None
        self.virtual_entry_commission = 0.0
        self.virtual_gross_pnl = 0.0
        self.virtual_exit_commission = 0.0

        self.entry_price = None
        self.tp_level = None
        self.sl_level = None
        self.current_trail_step = -1

        self.closed_trades = 0
        self.total_contracts = 0
        self.total_commission = 0.0
        self.final_virtual_equity = self.virtual_cash

        self._trade_records: list[dict[str, Any]] = []
        self._closed_trade_records: list[dict[str, Any]] = []

        # Deliberately no ATR indicator:
        # any Backtrader indicator can impose a minperiod and delay the strategy.
        self.atr_diagnostic = None

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
            initial_cash=self.virtual_cash,
        )
