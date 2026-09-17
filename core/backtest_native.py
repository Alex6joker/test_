from __future__ import annotations

from types import SimpleNamespace

from core.backtest_bar import Bar
from core.backtest_execution import BacktestExecutionContext, ExecutionEngine
from core.backtest_loop import BacktestEngine
from core.backtest_market import Market
from core.backtest_numeric import BacktestNumericMixin
from core.backtest_signal import SignalEngine
from core.backtest_state import BacktestState
from core.backtest_trade_ledger import TradeLedger
from core.backtest_result import BacktestResult


class NativeBacktestRuntime(BacktestNumericMixin):
    """Framework-independent production host for the virtual backtest model."""

    def __init__(self, params, logger) -> None:
        self.params = params
        self.logger = logger
        initial_cash = self._money(params.initial_cash)
        self.state = BacktestState(virtual_cash=initial_cash)
        self._trade_ledger = TradeLedger()
        self._trade_accounting_engine = None
        self.market = Market()
        self._execution_engine = ExecutionEngine()
        self._execution_context = BacktestExecutionContext(self)
        self._signal_engine = SignalEngine(params, logger, self._price)
        self._accounting_engine = self._make_accounting_engine()
        self._backtest_engine = BacktestEngine(self)

        # Preserve the established diagnostic lifecycle contract from the
        # historical runner: BROKER_START is a virtual-broker lifecycle marker and is
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

    def _ensure_trade_ledger(self):
        return self._trade_ledger

    def _ensure_accounting_engine(self):
        return self._accounting_engine

    def _ensure_trade_accounting_engine(self):
        from core.backtest_trade import TradeAccountingEngine
        if self._trade_accounting_engine is None:
            self._trade_accounting_engine = TradeAccountingEngine(self)
        return self._trade_accounting_engine

    def _unrealized_pnl(self, mark_price=None):
        if mark_price is None:
            mark_price = self.market.current_bar.close
        return self._accounting_engine.unrealized_pnl(mark_price)

    def _log_virtual_portfolio(self, bar_index):
        ledger = self._trade_ledger
        unrealized = self._unrealized_pnl()
        equity = self._money(self.state.virtual_cash + unrealized)
        if self.logger.wants_debug_event("PORTFOLIO_STATE"):
            self.logger.debug_event("PORTFOLIO_STATE", bar_index=bar_index, datetime=self.market.current_bar.datetime,
                cash=self.state.virtual_cash, unrealized_pnl=unrealized, portfolio_value=equity,
                position_size=self.state.virtual_position_size, position_price=self.state.virtual_entry_price,
                mark_price=self.market.current_bar.close, trade_id=ledger.trade_id)

    def _open_virtual_position(self, signal, size, bar_index):
        current_bar=self.market.current_bar
        if current_bar is None: raise RuntimeError("Market.current_bar is required to open a virtual position")
        record=self._ensure_trade_accounting_engine().open(signal,size,bar_index,current_bar)
        trade_id=record["trade_id"]; direction=record["direction"]; entry_price=record["entry_price"]; commission=record["entry_commission"]
        if self.logger.wants_trade():
            self.logger.trade(f"ENTRY_SIGNAL trade_id = {trade_id}; bar_index = {bar_index}; signal = {signal}; dynamic_size = {size}; execution_model = VIRTUAL_OPEN; entry_price = {entry_price}; entry_slippage = 0.0; commission = {commission}")
        if self.logger.wants_debug_event("ORDER_SUBMITTED"):
            self.logger.debug_event("ORDER_SUBMITTED", trade_id=trade_id, bar_index=bar_index, signal=signal, order_ref=None, order_type="VIRTUAL_OPEN", requested_size=size, reference_open=current_bar.open, execution_price=entry_price, dynamic_slip=0.0, execution_model="VIRTUAL")
        if self.logger.wants_trade():
            self.logger.trade(f"ENTRY_EXECUTED trade_id = {trade_id}; direction = {direction}; execution_model = VIRTUAL_OPEN; execution_price = {entry_price}; executed_size = {size}; entry_commission = {commission}; tp_level = {self.state.tp_level}; sl_level = {self.state.sl_level}")

    def _close_virtual_position(self, reason, target_exec_price, detected_price, bar_index, phase_index):
        result=self._ensure_trade_accounting_engine().close(reason,target_exec_price,bar_index,phase_index)
        trade_id=result["trade_id"]; direction=result["direction"]; size=result["size"]; entry_price=result["entry_price"]; entry_commission=result["entry_commission"]
        exit_price=result["exit_price"]; exit_commission=result["exit_commission"]; gross_pnl=result["gross_pnl"]; net_trade_pnl=result["net_pnl"]
        if self.logger.wants_trade():
            self.logger.trade(f"EXIT_SIGNAL trade_id = {trade_id}; reason = {reason}; bar_index = {bar_index}; phase_index = {phase_index}; detected_price = {detected_price}; level = {self.state.sl_level if reason == 'STOP_LOSS' else self.state.tp_level}; execution_model = VIRTUAL_INTRABAR; target_exec_price = {exit_price}")
            self.logger.trade(f"EXIT_EXECUTED trade_id = {trade_id}; reason = {reason}; execution_model = VIRTUAL_INTRABAR; broker_executed_price = None; execution_price = {exit_price}; target_exec_price = {exit_price}; exit_slippage = {self._execution_engine.get_backtest_dynamic_slippage(size)}; executed_size = {size}; exit_commission = {exit_commission}")
            self.logger.trade(f"TRADE_CLOSED trade_id = {trade_id}; direction = {direction}; size = {size}; entry_price = {entry_price}; exit_price = {exit_price}; gross_pnl = {gross_pnl}; entry_commission = {entry_commission}; exit_commission = {exit_commission}; net_pnl = {net_trade_pnl}; reason = {reason}; execution_model = VIRTUAL")
        if self.logger.wants_debug_event("TRADE_UPDATE"):
            self.logger.debug_event("TRADE_UPDATE", trade_id=trade_id,status="CLOSED",direction=direction,size=size,entry_price=entry_price,exit_price=exit_price,commission=self._money(entry_commission+exit_commission),pnl=gross_pnl,pnl_comm=net_trade_pnl,bar_index=bar_index,phase_index=phase_index,datetime=self.market.current_bar.datetime if self.market.current_bar else None)
        self._ensure_trade_accounting_engine().reset_position()

    def _trail_trigger_levels(self, direction):
        entry=self.state.virtual_entry_price
        if entry is None: return []
        direction=1 if direction>0 else -1
        cache_entry=getattr(self,"_trail_levels_cache_entry",None); cache_direction=getattr(self,"_trail_levels_cache_direction",None); levels=getattr(self,"_trail_levels_cache",None)
        if cache_entry!=entry or cache_direction!=direction or levels is None:
            tp_distance=self._price(self.params.tp); levels=[]
            for step_idx,(trigger_pct,stop_pct) in enumerate(self.params.dynamic_trail_steps):
                trigger_distance=self._price(tp_distance*trigger_pct)
                if direction>0: trigger_price=self._price(entry+trigger_distance); new_sl=self._price(entry+tp_distance*stop_pct)
                else: trigger_price=self._price(entry-trigger_distance); new_sl=self._price(entry-tp_distance*stop_pct)
                levels.append((step_idx,trigger_price,new_sl))
            self._trail_levels_cache_entry=entry; self._trail_levels_cache_direction=direction; self._trail_levels_cache=levels
        step=self.state.current_trail_step
        return levels.copy() if step<0 else levels[step+1:].copy()

    def _apply_trail_step(self,step_idx,new_sl,current_price):
        old_sl=self.state.sl_level
        if old_sl is None:return
        if self.state.virtual_position_size>0:
            if new_sl<=old_sl: self.state.current_trail_step=max(self.state.current_trail_step,step_idx); return
        else:
            if new_sl>=old_sl: self.state.current_trail_step=max(self.state.current_trail_step,step_idx); return
        self.state.sl_level=new_sl; self.state.current_trail_step=step_idx
        if self.logger.wants_trade():
            self.logger.trade(f"TRAIL_UPDATE trade_id = {self._trade_ledger.trade_id}; step_idx = {step_idx}; trigger_pct = {self.params.dynamic_trail_steps[step_idx][0]}; stop_pct = {self.params.dynamic_trail_steps[step_idx][1]}; old_sl_level = {old_sl}; new_sl_level = {new_sl}; trigger_cross_price = {current_price}")

    def _check_accounting(self):
        ledger=self._trade_ledger; closed_net=self._money(sum(float(r["net_pnl"]) for r in ledger.closed_records)); open_entry_commission=self.state.virtual_entry_commission if self.state.virtual_position_size else 0.0
        expected=self._money(self.params.initial_cash+closed_net-open_entry_commission+self._unrealized_pnl()); actual=self._money(self.state.virtual_cash+self._unrealized_pnl()); difference=self._money(actual-expected)
        if self.logger.wants_event("ACCOUNTING_CHECK"):
            self.logger.event("ACCOUNTING_CHECK",initial_cash=self._money(self.params.initial_cash),closed_net_pnl=closed_net,open_entry_commission=open_entry_commission,unrealized_pnl=self._unrealized_pnl(),expected_equity=expected,actual_equity=actual,difference=difference,passed=(difference==0.0))
        if difference!=0.0: raise RuntimeError(f"Accounting self-check failed: difference = {difference}")

    def _check_trade_lifecycle(self):
        ledger=self._trade_ledger; errors=ledger.check(self.state.virtual_position_size)
        if self.logger.wants_event("TRADE_LIFECYCLE_CHECK"):
            self.logger.event("TRADE_LIFECYCLE_CHECK",trade_count=len(ledger.records),closed_trade_count=ledger.closed_trades,open_position_size=self.state.virtual_position_size,errors=errors,passed=not errors)
        if errors: raise RuntimeError("Trade lifecycle self-check failed: "+"; ".join(errors))

    def _check_negative_cash(self):
        passed=self.state.virtual_cash>=0.0
        if self.logger.wants_event("NEGATIVE_CASH_CHECK"): self.logger.event("NEGATIVE_CASH_CHECK",virtual_cash=self.state.virtual_cash,passed=passed)
        if not passed: raise RuntimeError(f"Virtual cash became negative: {self.state.virtual_cash}")

    def stop(self):
        ledger=self._trade_ledger; final_bar=self.market.current_bar; final_close=final_bar.close if final_bar else None; unrealized=self._unrealized_pnl(final_close)
        self.state.virtual_cash=self._money(self.state.virtual_cash); final_virtual_equity=self._money(self.state.virtual_cash+unrealized)
        self._check_negative_cash(); self._check_trade_lifecycle(); self._check_accounting()
        closed_net=self._money(sum(float(r["net_pnl"]) for r in ledger.closed_records))
        if self.logger.wants_event("BACKTEST_SELF_CHECK"):
            self.logger.event("BACKTEST_SELF_CHECK",final_equity=final_virtual_equity,initial_cash=self._money(self.params.initial_cash),closed_net_pnl=closed_net,total_commission=self.state.total_commission,closed_trades=ledger.closed_trades,total_contracts=ledger.total_contracts,open_position_size=self.state.virtual_position_size,passed=True)
        if self.logger.wants_event("BACKTEST_STOP"):
            self.logger.event("BACKTEST_STOP",bar_index=self.market.bar_index,datetime=self.market.current_bar.datetime if self.market.current_bar else None,position_size=self.state.virtual_position_size,entry_price=self.state.virtual_entry_price,tp_level=self.state.tp_level,sl_level=self.state.sl_level,trade_id=ledger.trade_id,virtual_cash=self.state.virtual_cash,unrealized_pnl=unrealized,final_virtual_equity=final_virtual_equity,total_commission=self.state.total_commission)
        if self.state.virtual_position_size and self.logger.wants_warning_or_error(): self.logger.warning(f"OPEN_POSITION_AT_END trade_id = {ledger.trade_id}; position_size = {self.state.virtual_position_size}; entry_price = {self.state.virtual_entry_price}; mark_price = {final_close}; unrealized_pnl = {unrealized}")

    def _get_commission_per_side(self) -> float:
        return float(self.params.real_commission_per_side)

    def process_bar(self, bar: Bar) -> None:
        self._backtest_engine.process_bar(bar)

    def finish(self) -> BacktestResult:
        # Use the same accounting stop lifecycle as the production strategy.
        self.stop()
        precision_money = self.params.precision_money
        final_virtual_equity = self._money(
            self.state.virtual_cash + self._unrealized_pnl()
        )
        return BacktestResult(
            final_portfolio_value=round(float(final_virtual_equity), precision_money),
            real_net_profit=round(
                float(final_virtual_equity) - float(self.params.initial_cash),
                precision_money,
            ),
            total_closed_trades=int(self._trade_ledger.closed_trades),
            total_contracts=int(self._trade_ledger.total_contracts),
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
