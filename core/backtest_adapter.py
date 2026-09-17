"""Backtrader boundary for the virtual backtester.

Only this module owns conversion between Backtrader data/feed objects and the
virtual Bar model.  Virtual-market code must consume Bar/Market objects and
must not read Backtrader data lines directly.
"""
from __future__ import annotations

from typing import Any

from .backtest_bar import Bar


class BacktraderBarAdapter:
    """Convert one Backtrader data observation into an immutable Bar."""

    @staticmethod
    def to_bar(data: Any, index: int = 0) -> Bar:
        """Return the OHLCV observation at the requested Backtrader index."""
        return Bar(
            datetime=data.datetime.datetime(index),
            open=float(data.open[index]),
            high=float(data.high[index]),
            low=float(data.low[index]),
            close=float(data.close[index]),
            volume=int(data.volume[index]),
        )


class BacktraderFeedAdapter:
    """Build the Backtrader feed used by the backtest runner."""

    @staticmethod
    def create_feed(processed_path: str) -> Any:
        """Create the Backtrader CSV feed without altering source data."""
        import backtrader as bt

        return bt.feeds.GenericCSVData(
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


try:
    import backtrader as bt
except ImportError:  # Native production path must remain Backtrader-free.
    bt = None

if bt is not None:
    from core.backtest_accounting import BacktestAccountingMixin
    from core.backtest_execution import BacktestExecutionContext, ExecutionEngine
    from core.backtest_logger import BacktestLogger
    from core.backtest_market import Market
    from core.backtest_numeric import BacktestNumericMixin
    from core.backtest_signal import BacktestSignalMixin, SignalEngine
    from core.backtest_state import BacktestState
    from core.backtest_trade import BacktestTradeMixin
    from core.backtest_trade_ledger import TradeLedger
    from core.backtest_trailing import BacktestTrailingMixin
    from core.backtest_compat import ContractVolumeAnalyzer

if bt is not None:
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
            self.state = BacktestState(virtual_cash=initial_cash)
            self._trade_ledger = TradeLedger()
            self.market = Market()
            self._bar_adapter = BacktraderBarAdapter()
            self._execution_engine = ExecutionEngine()
            self._execution_context = BacktestExecutionContext(self)
            self._signal_engine = SignalEngine(self.params, self.logger, self._price)
            from core.backtest_loop import BacktestEngine
            self._backtest_engine = BacktestEngine(self)

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
