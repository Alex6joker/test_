from __future__ import annotations

from types import SimpleNamespace

from core.backtest_accounting import AccountingEngine
from core.backtest_bar import Bar
from core.backtest_execution import NativeExecutionContext, ExecutionEngine
from core.backtest_loop import BacktestEngine
from core.backtest_market import Market
from core.backtest_numeric import money, price
from core.backtest_result import BacktestResult
from core.backtest_signal import SignalEngine
from core.backtest_state import BacktestState
from core.backtest_trade import TradeAccountingEngine
from core.backtest_trade_ledger import TradeLedger
from core.backtest_trailing import TrailingEngine


class NativeBacktestRuntime:
    """Production composition root for the framework-independent backtest."""

    def __init__(self, params, logger) -> None:
        self.params = params
        self.logger = logger
        self._precision_money = params.precision_money
        self._precision_num = params.precision_num

        initial_cash = self._money(params.initial_cash)
        self.state = BacktestState(virtual_cash=initial_cash)
        self.trade_ledger = TradeLedger()
        self.market = Market()
        self.accounting = AccountingEngine(
            self.state,
            self.params,
            self._money,
            float(self.params.real_commission_per_side),
        )
        self.trailing = TrailingEngine(
            self.state,
            self.params,
            self._price,
            self.logger,
            self.trade_ledger,
        )
        self.trade_accounting = TradeAccountingEngine(
            state=self.state,
            params=self.params,
            accounting=self.accounting,
            ledger=self.trade_ledger,
            price_fn=self._price,
        )
        self.execution_engine = ExecutionEngine()
        self.execution_context = NativeExecutionContext(
            state=self.state,
            market=self.market,
            ledger=self.trade_ledger,
            trailing=self.trailing,
            trade_accounting=self.trade_accounting,
            logger=self.logger,
            price_fn=self._price,
        )
        self.signal_engine = SignalEngine(params, logger, self._price)
        self.backtest_engine = BacktestEngine(self)

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

    def _money(self, value: float) -> float:
        return money(value, self._precision_money)

    def _price(self, value: float) -> float:
        return price(value, self._precision_num)

    def unrealized_pnl(self, mark_price: float | None = None) -> float:
        if mark_price is None:
            current_bar = self.market.current_bar
            if current_bar is None:
                return 0.0
            mark_price = current_bar.close
        return self.accounting.unrealized_pnl(mark_price)

    def log_virtual_portfolio(self, bar_index: int) -> None:
        current_bar = self.market.current_bar
        if current_bar is None:
            return
        unrealized = self.unrealized_pnl()
        equity = self._money(self.state.virtual_cash + unrealized)
        if self.logger.wants_debug_event("PORTFOLIO_STATE"):
            self.logger.debug_event(
                "PORTFOLIO_STATE",
                bar_index=bar_index,
                datetime=current_bar.datetime,
                cash=self.state.virtual_cash,
                unrealized_pnl=unrealized,
                portfolio_value=equity,
                position_size=self.state.virtual_position_size,
                position_price=self.state.virtual_entry_price,
                mark_price=current_bar.close,
                trade_id=self.trade_ledger.trade_id,
            )

    def open_virtual_position(self, signal: int, size: int, bar_index: int) -> None:
        current_bar = self.market.current_bar
        if current_bar is None:
            raise RuntimeError("Market.current_bar is required to open a virtual position")

        record = self.trade_accounting.open(signal, size, bar_index, current_bar)
        trade_id = record["trade_id"]
        direction = record["direction"]
        entry_price = record["entry_price"]
        commission = record["entry_commission"]

        if self.logger.wants_trade():
            self.logger.trade(
                f"ENTRY_SIGNAL trade_id = {trade_id}; "
                f"bar_index = {bar_index}; signal = {signal}; dynamic_size = {size}; "
                f"execution_model = VIRTUAL_OPEN; entry_price = {entry_price}; "
                f"entry_slippage = 0.0; commission = {commission}"
            )
        if self.logger.wants_debug_event("ORDER_SUBMITTED"):
            self.logger.debug_event(
                "ORDER_SUBMITTED",
                trade_id=trade_id,
                bar_index=bar_index,
                signal=signal,
                order_ref=None,
                order_type="VIRTUAL_OPEN",
                requested_size=size,
                reference_open=current_bar.open,
                execution_price=entry_price,
                dynamic_slip=0.0,
                execution_model="VIRTUAL",
            )
        if self.logger.wants_trade():
            self.logger.trade(
                f"ENTRY_EXECUTED trade_id = {trade_id}; direction = {direction}; "
                f"execution_model = VIRTUAL_OPEN; execution_price = {entry_price}; "
                f"executed_size = {size}; entry_commission = {commission}; "
                f"tp_level = {self.state.tp_level}; sl_level = {self.state.sl_level}"
            )

    def _check_negative_cash(self) -> None:
        passed = self.state.virtual_cash >= 0.0
        if self.logger.wants_event("NEGATIVE_CASH_CHECK"):
            self.logger.event(
                "NEGATIVE_CASH_CHECK",
                virtual_cash=self.state.virtual_cash,
                passed=passed,
            )
        if not passed:
            raise RuntimeError(
                f"Virtual cash became negative: {self.state.virtual_cash}"
            )

    def _check_trade_lifecycle(self) -> None:
        errors = self.trade_ledger.check(self.state.virtual_position_size)
        if self.logger.wants_event("TRADE_LIFECYCLE_CHECK"):
            self.logger.event(
                "TRADE_LIFECYCLE_CHECK",
                trade_count=len(self.trade_ledger.records),
                closed_trade_count=self.trade_ledger.closed_trades,
                open_position_size=self.state.virtual_position_size,
                errors=errors,
                passed=not errors,
            )
        if errors:
            raise RuntimeError(
                "Trade lifecycle self-check failed: " + "; ".join(errors)
            )

    def _check_accounting(self) -> None:
        closed_net = self._money(
            sum(float(r["net_pnl"]) for r in self.trade_ledger.closed_records)
        )
        open_entry_commission = (
            self.state.virtual_entry_commission
            if self.state.virtual_position_size
            else 0.0
        )
        unrealized = self.unrealized_pnl()
        expected_equity = self._money(
            self.params.initial_cash
            + closed_net
            - open_entry_commission
            + unrealized
        )
        actual_equity = self._money(self.state.virtual_cash + unrealized)
        difference = self._money(actual_equity - expected_equity)

        if self.logger.wants_event("ACCOUNTING_CHECK"):
            self.logger.event(
                "ACCOUNTING_CHECK",
                initial_cash=self._money(self.params.initial_cash),
                closed_net_pnl=closed_net,
                open_entry_commission=open_entry_commission,
                unrealized_pnl=unrealized,
                expected_equity=expected_equity,
                actual_equity=actual_equity,
                difference=difference,
                passed=(difference == 0.0),
            )

        if difference != 0.0:
            raise RuntimeError(
                f"Accounting self-check failed: difference = {difference}"
            )

    def process_bar(self, bar: Bar) -> None:
        self.backtest_engine.process_bar(bar)

    def stop(self) -> float:
        final_bar = self.market.current_bar
        final_close = final_bar.close if final_bar is not None else None
        unrealized = self.unrealized_pnl(final_close)

        self.state.virtual_cash = self._money(self.state.virtual_cash)
        final_virtual_equity = self._money(
            self.state.virtual_cash + unrealized
        )

        self._check_negative_cash()
        self._check_trade_lifecycle()
        self._check_accounting()

        closed_net = self._money(
            sum(float(r["net_pnl"]) for r in self.trade_ledger.closed_records)
        )

        if self.logger.wants_event("BACKTEST_SELF_CHECK"):
            self.logger.event(
                "BACKTEST_SELF_CHECK",
                final_equity=final_virtual_equity,
                initial_cash=self._money(self.params.initial_cash),
                closed_net_pnl=closed_net,
                total_commission=self.state.total_commission,
                closed_trades=self.trade_ledger.closed_trades,
                total_contracts=self.trade_ledger.total_contracts,
                open_position_size=self.state.virtual_position_size,
                passed=True,
            )

        if self.logger.wants_event("BACKTEST_STOP"):
            self.logger.event(
                "BACKTEST_STOP",
                bar_index=self.market.bar_index,
                datetime=final_bar.datetime if final_bar else None,
                position_size=self.state.virtual_position_size,
                entry_price=self.state.virtual_entry_price,
                tp_level=self.state.tp_level,
                sl_level=self.state.sl_level,
                trade_id=self.trade_ledger.trade_id,
                virtual_cash=self.state.virtual_cash,
                unrealized_pnl=unrealized,
                final_virtual_equity=final_virtual_equity,
                total_commission=self.state.total_commission,
            )

        if self.state.virtual_position_size and self.logger.wants_warning_or_error():
            self.logger.warning(
                f"OPEN_POSITION_AT_END trade_id = {self.trade_ledger.trade_id}; "
                f"position_size = {self.state.virtual_position_size}; "
                f"entry_price = {self.state.virtual_entry_price}; "
                f"mark_price = {final_close}; "
                f"unrealized_pnl = {unrealized}"
            )

        return final_virtual_equity

    def finish(self) -> BacktestResult:
        final_virtual_equity = self.stop()
        precision_money = self.params.precision_money
        return BacktestResult(
            final_portfolio_value=round(float(final_virtual_equity), precision_money),
            real_net_profit=round(
                float(final_virtual_equity) - float(self.params.initial_cash),
                precision_money,
            ),
            total_closed_trades=int(self.trade_ledger.closed_trades),
            total_contracts=int(self.trade_ledger.total_contracts),
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


def run_native_backtest(bars, cfg, logger, precision_money: int) -> BacktestResult:
    """Run the production backtest from an iterable of immutable Bars."""
    params = params_from_config(cfg, precision_money)
    runtime = NativeBacktestRuntime(params, logger)
    for bar in bars:
        runtime.process_bar(bar)
    return runtime.finish()
