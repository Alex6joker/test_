from __future__ import annotations

from types import SimpleNamespace

from core.backtest_accounting import BacktestAccountingMixin
from core.backtest_bar import Bar
from core.backtest_execution import BacktestExecutionContext, ExecutionEngine
from core.backtest_loop import BacktestEngine
from core.backtest_market import Market
from core.backtest_numeric import BacktestNumericMixin
from core.backtest_signal import SignalEngine
from core.backtest_state import BacktestState
from core.backtest_trade import BacktestTradeMixin
from core.backtest_trailing import BacktestTrailingMixin
from core.backtest_result import BacktestResult


class NativeBacktestRuntime(
    BacktestNumericMixin,
    BacktestTrailingMixin,
    BacktestAccountingMixin,
    BacktestTradeMixin,
):
    """Framework-independent production host for the virtual backtest model."""

    def __init__(self, params, logger) -> None:
        self.params = params
        self.logger = logger
        initial_cash = self._money(params.initial_cash)
        self.state = BacktestState(
            virtual_cash=initial_cash,
            final_virtual_equity=initial_cash,
        )
        self.market = Market()
        self._execution_engine = ExecutionEngine()
        self._execution_context = BacktestExecutionContext(self)
        self._signal_engine = SignalEngine(params, logger, self._price)
        self._accounting_engine = self._make_accounting_engine()
        self._backtest_engine = BacktestEngine(self)

        # Preserve the established diagnostic contract from the Backtrader
        # runner: BROKER_START is a virtual-broker lifecycle marker and is
        # emitted before STRATEGY_INIT. The virtual model remains the source
        # of truth for fills/P&L.
        if self.logger.wants_event("BROKER_START"):
            self.logger.event(
                "BROKER_START",
                cash=self.state.virtual_cash,
                value=self.state.virtual_cash,
                commission_per_side=float(self.params.real_commission_per_side),
                margin=self.params.real_margin,
                mult=self.params.real_mult,
                source_of_truth="VIRTUAL_STRATEGY",
            )

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

    def _make_accounting_engine(self):
        from core.backtest_accounting import AccountingEngine
        return AccountingEngine(
            self.state,
            self.params,
            self._money,
            float(self.params.real_commission_per_side),
        )

    def _get_commission_per_side(self) -> float:
        return float(self.params.real_commission_per_side)

    def process_bar(self, bar: Bar) -> None:
        self._backtest_engine.process_bar(bar)

    def finish(self) -> BacktestResult:
        # Use the same accounting stop lifecycle as the production strategy.
        self.stop()
        precision_money = self.params.precision_money
        return BacktestResult(
            final_portfolio_value=round(float(self.state.final_virtual_equity), precision_money),
            real_net_profit=round(
                float(self.state.final_virtual_equity) - float(self.params.initial_cash),
                precision_money,
            ),
            total_closed_trades=int(self.state.closed_trades),
            total_contracts=int(self.state.total_contracts),
            total_commission=round(float(self.state.total_commission), precision_money),
            open_position_size=int(self.state.virtual_position_size),
            virtual_cash=round(float(self.state.virtual_cash), precision_money),
        )


def params_from_config(cfg, precision_money: int):
    return SimpleNamespace(
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
        initial_cash=cfg.INITIAL_CASH,
        real_commission_per_side=cfg.REAL_COMMISSION / 2,
    )


def bars_from_dataframe(prepared):
    """Convert prepared rows directly to immutable Bar objects."""
    for row in prepared.itertuples(index=False, name=None):
        yield Bar(
            datetime=row[0].to_pydatetime(),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=int(round(float(row[5]))),
        )


def run_native_backtest(prepared, cfg, logger, precision_money: int) -> BacktestResult:
    """Run the production backtest without a framework/feed/temp CSV."""
    params = params_from_config(cfg, precision_money)
    runtime = NativeBacktestRuntime(params, logger)
    for bar in bars_from_dataframe(prepared):
        runtime.process_bar(bar)
    return runtime.finish()
