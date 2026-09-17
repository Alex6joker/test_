from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta

from core.backtest_accounting import AccountingEngine
from core.backtest_bar import Bar
from core.backtest_execution import ExecutionEngine, ExecutionExit, NativeExecutionContext
from core.backtest_loop import BacktestEngine
from core.backtest_market import Market
from core.backtest_numeric import money, price
from core.backtest_signal import SignalEngine
from core.backtest_state import BacktestState
from core.backtest_trade import TradeAccountingEngine
from core.backtest_trade_ledger import TradeLedger
from core.backtest_trailing import TrailingEngine


@dataclass
class Params:
    trigger: float = 0.16
    tp: float = 1.90
    sl: float = 1.70
    risk: float = 0.03
    real_mult: float = 777.0
    real_margin: float = 18200.0
    safety_factor: float = 1.15
    precision_num: int = 2
    precision_money: int = 2
    dynamic_trail_steps: list[tuple[float, float]] | None = None
    initial_cash: float = 264000.0
    real_commission_per_side: float = 10.185

    def __post_init__(self):
        if self.dynamic_trail_steps is None:
            self.dynamic_trail_steps = [(0.35, 0.05), (0.75, 0.15), (0.95, 0.65)]


class DummyLogger:
    is_diagnostic = False

    def wants_event(self, _name): return False
    def wants_debug_event(self, _name): return False
    def wants_trade(self): return False
    def wants_warning_or_error(self): return False
    def event(self, *_args, **_kwargs): pass
    def debug_event(self, *_args, **_kwargs): pass
    def trade(self, *_args, **_kwargs): pass
    def warning(self, *_args, **_kwargs): pass


class NativeHarness:
    def __init__(self, *, bar: Bar, position: int, entry: float, sl: float, tp: float):
        self.params = Params()
        self.logger = DummyLogger()
        self.state = BacktestState(virtual_cash=self.params.initial_cash)
        self.market = Market()
        self.trade_ledger = TradeLedger()
        self.accounting = AccountingEngine(
            self.state, self.params, self._money, self.params.real_commission_per_side
        )
        self.trailing = TrailingEngine(
            self.state, self.params, self._price, self.logger, self.trade_ledger
        )
        self.trade_accounting = TradeAccountingEngine(
            state=self.state, params=self.params, accounting=self.accounting,
            ledger=self.trade_ledger, price_fn=self._price,
        )
        self.execution_engine = ExecutionEngine()
        self.execution_context = NativeExecutionContext(
            state=self.state, market=self.market, ledger=self.trade_ledger,
            trailing=self.trailing, trade_accounting=self.trade_accounting,
            logger=self.logger, price_fn=self._price,
        )
        self.signal_engine = SignalEngine(self.params, self.logger, self._price)
        self.backtest_engine = BacktestEngine(self)

        if position:
            self.trade_accounting.open(
                1 if position > 0 else -1, abs(position), 2,
                Bar(datetime(2026, 1, 1), entry, entry, entry, entry, 100),
            )
            self.state.sl_level = sl
            self.state.tp_level = tp
        else:
            self.state.virtual_entry_price = None

        self.test_bar = bar

    def _money(self, value): return money(value, self.params.precision_money)
    def _price(self, value): return price(value, self.params.precision_num)

    def log_virtual_portfolio(self, bar_index): pass

    def open_virtual_position(self, signal, size, bar_index):
        self.trade_accounting.open(signal, size, bar_index, self.market.current_bar)

    def process_segment(self, start_price, end_price, bar_index, phase_index):
        snapshot = self.execution_context.execution_snapshot()
        phase = self.execution_engine._process_segment(
            snapshot, self._price(start_price), self._price(end_price), bar_index, phase_index
        )
        if phase is None:
            return False
        for update in phase.trail_updates:
            self.execution_context.apply_trail_update(update, update.trigger_price)
        if phase.exit is not None:
            self.execution_context.apply_execution_exit(phase.exit)
        return phase.exit is not None


class BacktestContractTests(unittest.TestCase):
    def test_position_size_uses_previous_volume_and_all_limits(self):
        s = NativeHarness(bar=Bar(datetime.now(), 100, 101, 99, 100.5, 100), position=0, entry=0, sl=98, tp=105)
        s.market.observe(Bar(datetime(2026,1,1,9,59),99,100,98,99.5,100))
        s.market.observe(Bar(datetime(2026,1,1,10),100,101,99,100.5,100))
        self.assertEqual(s.signal_engine.calculate_position_size(virtual_cash=264000, previous_volume=100, bar_index=10), 5)

    def test_entry_contract_sets_levels_and_charges_entry_commission(self):
        s = NativeHarness(bar=Bar(datetime.now(),100,101,99,100.5,100), position=0, entry=0, sl=98.3, tp=101.9)
        s.market.observe(s.test_bar)
        s.open_virtual_position(1,5,10)
        self.assertEqual(s.state.virtual_position_size,5)
        self.assertEqual(s.state.virtual_entry_price,100.0)
        self.assertEqual(s.state.tp_level,101.9)
        self.assertEqual(s.state.sl_level,98.3)
        self.assertEqual(s.state.virtual_entry_commission,50.93)
        self.assertEqual(s.state.virtual_cash,263949.07)

    def test_exit_contract_realizes_gross_pnl_and_both_commissions(self):
        s=NativeHarness(bar=Bar(datetime.now(),100,101,99,100.5,100),position=5,entry=100,sl=98,tp=102)
        s.execution_context.apply_execution_exit(ExecutionExit('TAKE_PROFIT',11,2,1,5,102,101.98,0.02,'TAKE_PROFIT'))
        r=s.trade_ledger.records[0]
        self.assertEqual(r['exit_price'],101.98); self.assertEqual(r['gross_pnl'],7692.3); self.assertEqual(r['entry_commission'],50.93); self.assertEqual(r['exit_commission'],50.93); self.assertEqual(r['net_pnl'],7590.44)
        self.assertEqual(s.state.virtual_position_size,0); self.assertEqual(s.trade_ledger.closed_trades,1); self.assertEqual(s.trade_ledger.total_contracts,10)

    def test_accounting_check_matches_closed_trade_ledger(self):
        s=NativeHarness(bar=Bar(datetime.now(),100,101,99,100.5,100),position=5,entry=100,sl=98,tp=102)
        s.market.observe(s.test_bar)
        s.execution_context.apply_execution_exit(ExecutionExit('TAKE_PROFIT',11,2,1,5,102,101.98,0.02,'TAKE_PROFIT'))
        self.assertEqual(s.trade_ledger.check(s.state.virtual_position_size),[])
        self.assertEqual(s.state.virtual_cash, 271590.44)

    def test_trail_cannot_retroactively_protect_an_earlier_phase(self):
        s=NativeHarness(bar=Bar(datetime.now(),99,100,97.9,99.19,100),position=10,entry=97,sl=98,tp=110)
        self.assertTrue(s.process_segment(99,97.90,10,1)); self.assertEqual(s.trade_ledger.records[0]['exit_reason'],'STOP_LOSS')

    def test_short_trail_is_used_only_by_subsequent_movement(self):
        s=NativeHarness(bar=Bar(datetime.now(),104.62,104.62,103.25,104,100),position=-10,entry=105,sl=105,tp=100)
        s.params.dynamic_trail_steps=[(0.50,0.25)]
        self.assertFalse(s.process_segment(104.62,103.25,10,2)); self.assertEqual(s.state.current_trail_step,0); self.assertEqual(s.state.sl_level,104.53)
        self.assertTrue(s.process_segment(103.25,104.62,10,3)); self.assertEqual(s.trade_ledger.records[0]['exit_phase'],3)

    def test_multiple_trail_steps_are_only_activated_when_crossed(self):
        s=NativeHarness(bar=Bar(datetime.now(),100,103,100,103,100),position=1,entry=100,sl=98,tp=110)
        s.params.dynamic_trail_steps=[(0.20,0.05),(0.50,0.15),(0.90,0.65)]
        self.assertFalse(s.process_segment(100,106,1,0)); self.assertEqual(s.state.current_trail_step,2); self.assertEqual(s.state.sl_level,101.23)

    def test_unreached_trail_steps_remain_unactivated(self):
        s=NativeHarness(bar=Bar(datetime.now(),100,100.8,100,100.8,100),position=1,entry=100,sl=98,tp=110)
        s.params.dynamic_trail_steps=[(0.20,0.05),(0.50,0.15)]
        self.assertFalse(s.process_segment(100,100.8,1,0)); self.assertEqual(s.state.current_trail_step,0); self.assertEqual(s.state.sl_level,100.09)

    def test_first_bar_skips_signal_evaluation(self):
        s=NativeHarness(bar=Bar(datetime.now(),100,101,99,100.5,100),position=0,entry=0,sl=0,tp=0)
        s.backtest_engine.process_bar(s.test_bar); self.assertEqual(s.state.virtual_position_size,0); self.assertEqual(s.trade_ledger.trade_id,0)

    def test_second_bar_uses_previous_candle_and_enters_at_current_open(self):
        s=NativeHarness(bar=Bar(datetime.now(),102,102.2,101.8,102,100),position=0,entry=0,sl=0,tp=0)
        s.params.sl=1.7; s.params.tp=1.9
        b1=Bar(datetime(2026,1,1),100,101,99,100.5,100); b2=Bar(datetime(2026,1,1,0,1),102,102.2,101.8,102,100)
        s.backtest_engine.process_bar(b1); s.backtest_engine.process_bar(b2)
        self.assertEqual(s.state.virtual_entry_price,102.0); self.assertEqual(s.trade_ledger.records[-1]['entry_bar'],2)

    def test_entry_is_exposed_to_remainder_of_same_bar(self):
        s=NativeHarness(bar=Bar(datetime.now(),100,101,99,100.5,100),position=0,entry=0,sl=0,tp=0)
        b1=Bar(datetime(2026,1,1),100,101,99,100.5,100); b2=Bar(datetime(2026,1,1,0,1),102,104,101.5,103,100)
        s.backtest_engine.process_bar(b1); s.backtest_engine.process_bar(b2)
        self.assertEqual(s.trade_ledger.records[-1]['entry_price'],102.0); self.assertEqual(s.trade_ledger.records[-1]['exit_reason'],'TAKE_PROFIT')

    def test_long_sl_crossing_closes_position(self):
        s=NativeHarness(bar=Bar(datetime.now(),100,101,98,99,100),position=5,entry=100,sl=99,tp=102); s.process_segment(100,98.5,11,0); self.assertEqual(s.trade_ledger.records[0]['exit_reason'],'STOP_LOSS'); self.assertEqual(s.state.virtual_position_size,0)

    def test_short_tp_crossing_closes_position(self):
        s=NativeHarness(bar=Bar(datetime.now(),100,101,98,99,100),position=-5,entry=100,sl=102,tp=98); s.process_segment(100,97.5,11,0); self.assertEqual(s.trade_ledger.records[0]['exit_reason'],'TAKE_PROFIT'); self.assertEqual(s.state.virtual_position_size,0)

    def test_short_sl_crossing_closes_position(self):
        s=NativeHarness(bar=Bar(datetime.now(),100,102,99,101,100),position=-5,entry=100,sl=101,tp=98); s.process_segment(100,102,11,0); self.assertEqual(s.trade_ledger.records[0]['exit_reason'],'STOP_LOSS'); self.assertEqual(s.state.virtual_position_size,0)

    def test_exit_has_priority_over_trail_at_same_crossing_price(self):
        s=NativeHarness(bar=Bar(datetime.now(),100,102,99,102,100),position=1,entry=100,sl=99,tp=101); s.params.dynamic_trail_steps=[(1.0/1.9,0.5)]; s.process_segment(100,101,11,0); self.assertEqual(s.trade_ledger.records[0]['exit_reason'],'TAKE_PROFIT')

    def test_zero_length_phase_does_not_cross_levels(self):
        s=NativeHarness(bar=Bar(datetime.now(),100,101,99,100,100),position=5,entry=100,sl=98,tp=102); self.assertFalse(s.process_segment(100,100,11,0)); self.assertEqual(s.state.virtual_position_size,5); self.assertEqual(s.state.current_trail_step,-1)

    def test_close_clears_position_levels_and_entry_state(self):
        s=NativeHarness(bar=Bar(datetime.now(),100,101,99,100,100),position=5,entry=100,sl=98,tp=102); s.execution_context.apply_execution_exit(__import__('core.backtest_execution',fromlist=['ExecutionExit']).ExecutionExit('TAKE_PROFIT',11,2,1,5,102,102,0.02,'TAKE_PROFIT')); self.assertEqual(s.state.virtual_position_size,0); self.assertIsNone(s.state.virtual_entry_price); self.assertIsNone(s.state.tp_level); self.assertIsNone(s.state.sl_level); self.assertEqual(s.state.current_trail_step,-1)

    def test_dynamic_slippage_schedule_is_stable(self):
        self.assertEqual({n:ExecutionEngine.get_backtest_dynamic_slippage(n) for n in (1,5,6,15,16,30,31)},{1:0.02,5:0.02,6:0.04,15:0.04,16:0.07,30:0.07,31:0.15})

    def test_doji_path_is_open_low_high_close(self):
        s=NativeHarness(bar=Bar(datetime.now(),100,103,99,100,100),position=1,entry=100,sl=98,tp=105)
        result=s.execution_engine.process(s.execution_context.execution_snapshot(),s.test_bar,7)
        self.assertEqual([(p.start_price,p.end_price) for p in result.phases],[(100,99),(99,103),(103,100)])


if __name__ == '__main__': unittest.main(verbosity=2)
