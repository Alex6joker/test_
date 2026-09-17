from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

core_pkg = types.ModuleType("core")
core_pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "core")]
sys.modules.setdefault("core", core_pkg)

from core.backtest_accounting import AccountingEngine
from core.backtest_bar import Bar
from core.backtest_execution import BacktestExecutionContext, ExecutionEngine
from core.backtest_loop import BacktestEngine
from core.backtest_market import Market
from core.backtest_native import NativeBacktestRuntime
from core.backtest_numeric import BacktestNumericMixin
from core.backtest_signal import SignalEngine
from core.backtest_state import BacktestState
from core.backtest_trade import TradeAccountingEngine
from core.backtest_trade_ledger import TradeLedger


class DummyLogger:
    is_diagnostic = False
    def wants_event(self, _name): return False
    def wants_debug_event(self, _name): return False
    def wants_trade(self): return False
    def wants_warning_or_error(self): return False
    def event(self, *_args, **_kwargs): pass
    def debug_event(self, *_args, **_kwargs): pass
    def trade(self, *_args, **_kwargs): pass


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


class NativeHarness(BacktestNumericMixin):
    def __init__(self, *, position=0, entry=0.0, sl=0.0, tp=0.0):
        self.params = Params()
        self.logger = DummyLogger()
        self.state = BacktestState(virtual_cash=self.params.initial_cash)
        self.market = Market()
        self._trade_ledger = TradeLedger()
        self._accounting_engine = AccountingEngine(
            self.state, self.params, self._money, self.params.real_commission_per_side
        )
        self._trade_accounting_engine = TradeAccountingEngine(self)
        self._execution_engine = ExecutionEngine()
        self._execution_context = BacktestExecutionContext(self)
        self._signal_engine = SignalEngine(self.params, self.logger, self._price)
        self.state.virtual_position_size = position
        self.state.virtual_entry_price = entry
        self.state.sl_level = sl
        self.state.tp_level = tp
        self.state.virtual_entry_commission = (
            self._money(self.params.real_commission_per_side * abs(position)) if position else 0.0
        )
        self.state.total_commission = self.state.virtual_entry_commission
        if position:
            self.state.virtual_cash = self._money(self.state.virtual_cash - self.state.virtual_entry_commission)
        if position:
            self._trade_ledger.open_trade(
                direction="LONG" if position > 0 else "SHORT",
                size=abs(position),
                bar_index=2,
                entry_datetime=datetime(2026, 1, 1),
                entry_price=entry,
                entry_commission=self.state.virtual_entry_commission,
            )

    def _ensure_trade_ledger(self): return self._trade_ledger
    def _ensure_accounting_engine(self): return self._accounting_engine
    def _ensure_trade_accounting_engine(self): return self._trade_accounting_engine

    def _trail_trigger_levels(self, direction):
        entry = self.state.virtual_entry_price
        if entry is None:
            return []
        tp_distance = self._price(self.params.tp)
        levels = []
        for i, (trigger_pct, stop_pct) in enumerate(self.params.dynamic_trail_steps):
            trigger_distance = self._price(tp_distance * trigger_pct)
            if direction > 0:
                trigger = self._price(entry + trigger_distance)
                new_sl = self._price(entry + tp_distance * stop_pct)
            else:
                trigger = self._price(entry - trigger_distance)
                new_sl = self._price(entry - tp_distance * stop_pct)
            levels.append((i, trigger, new_sl))
        step = self.state.current_trail_step
        return levels if step < 0 else levels[step + 1:]

    def _apply_trail_step(self, step_idx, new_sl, current_price):
        old_sl = self.state.sl_level
        if old_sl is None:
            return
        if self.state.virtual_position_size > 0:
            if new_sl <= old_sl:
                self.state.current_trail_step = max(self.state.current_trail_step, step_idx)
                return
        else:
            if new_sl >= old_sl:
                self.state.current_trail_step = max(self.state.current_trail_step, step_idx)
                return
        self.state.sl_level = new_sl
        self.state.current_trail_step = step_idx

    def _open_virtual_position(self, signal, size, bar_index):
        self._trade_accounting_engine.open(signal, size, bar_index, self.market.current_bar)

    def _close_virtual_position(self, reason, target_exec_price, detected_price, bar_index, phase_index):
        self._trade_accounting_engine.close(reason, target_exec_price, bar_index, phase_index)
        self._trade_accounting_engine.reset_position()

    def _process_monotonic_segment(self, start, end, bar_index, phase_index):
        return self._execution_engine._process_monotonic_segment(
            context=self._execution_context,
            start_price=start,
            end_price=end,
            bar_index=bar_index,
            phase_index=phase_index,
        ) is not None


class BacktestContractTests(unittest.TestCase):
    def test_position_size_uses_previous_volume_and_all_limits(self):
        h = NativeHarness()
        size = h._signal_engine.calculate_position_size(
            virtual_cash=264000.0, previous_volume=100, bar_index=10
        )
        self.assertEqual(size, 5)

    def test_entry_contract_sets_levels_and_charges_entry_commission(self):
        h = NativeHarness()
        h.market.observe(Bar(datetime(2026,1,1,10,0),100,101,99,100.5,100))
        h._open_virtual_position(1, 5, 10)
        self.assertEqual(h.state.virtual_position_size, 5)
        self.assertEqual(h.state.virtual_entry_price, 100.0)
        self.assertEqual(h.state.tp_level, 101.9)
        self.assertEqual(h.state.sl_level, 98.3)
        self.assertEqual(h.state.virtual_entry_commission, 50.93)
        self.assertEqual(h.state.virtual_cash, 263949.07)

    def test_exit_contract_realizes_gross_pnl_and_both_commissions(self):
        h = NativeHarness(position=5, entry=100.0, sl=98.0, tp=102.0)
        h._close_virtual_position("TAKE_PROFIT", 101.98, 102.0, 11, 2)
        record = h._trade_ledger.records[0]
        self.assertEqual(record["exit_price"], 101.98)
        self.assertEqual(record["gross_pnl"], 7692.3)
        self.assertEqual(record["entry_commission"], 50.93)
        self.assertEqual(record["exit_commission"], 50.93)
        self.assertEqual(record["net_pnl"], 7590.44)
        self.assertEqual(h.state.virtual_position_size, 0)

    def test_accounting_check_matches_closed_trade_ledger(self):
        h = NativeHarness(position=5, entry=100.0, sl=98.0, tp=102.0)
        h.market.observe(Bar(datetime(2026,1,1),100,101,99,100.5,100))
        h._close_virtual_position("TAKE_PROFIT", 101.98, 102.0, 11, 2)
        ledger = h._trade_ledger
        errors = ledger.check(h.state.virtual_position_size)
        self.assertEqual(errors, [])
        closed_net = h._money(sum(float(r["net_pnl"]) for r in ledger.closed_records))
        equity = h._money(h.state.virtual_cash + h._accounting_engine.unrealized_pnl(100.5))
        expected = h._money(h.params.initial_cash + closed_net)
        self.assertEqual(equity, expected)

    def test_trail_cannot_retroactively_protect_an_earlier_phase(self):
        h = NativeHarness(position=10, entry=97.0, sl=98.0, tp=110.0)
        self.assertTrue(h._process_monotonic_segment(99.0, 97.90, 10, 1))
        self.assertEqual(h._trade_ledger.records[0]["exit_reason"], "STOP_LOSS")

    def test_short_trail_is_used_only_by_subsequent_movement(self):
        h = NativeHarness(position=-10, entry=105.0, sl=105.0, tp=100.0)
        h.params.dynamic_trail_steps = [(0.50, 0.25)]
        self.assertFalse(h._process_monotonic_segment(104.62, 103.25, 10, 2))
        self.assertEqual(h.state.current_trail_step, 0)
        self.assertEqual(h.state.sl_level, 104.53)
        self.assertTrue(h._process_monotonic_segment(103.25, 104.62, 10, 3))
        self.assertEqual(h._trade_ledger.records[0]["exit_phase"], 3)

    def test_multiple_trail_steps_are_only_activated_when_crossed(self):
        h = NativeHarness(position=1, entry=100.0, sl=98.0, tp=110.0)
        h.params.dynamic_trail_steps = [(0.20,0.05),(0.50,0.15),(0.90,0.65)]
        h._process_monotonic_segment(100.0, 106.0, 1, 0)
        self.assertEqual(h.state.current_trail_step, 2)
        self.assertEqual(h.state.sl_level, 101.23)

    def test_unreached_trail_steps_remain_unactivated(self):
        h = NativeHarness(position=1, entry=100.0, sl=98.0, tp=110.0)
        h.params.dynamic_trail_steps = [(0.20,0.05),(0.50,0.15)]
        h._process_monotonic_segment(100.0, 100.8, 1, 0)
        self.assertEqual(h.state.current_trail_step, 0)
        self.assertEqual(h.state.sl_level, 100.09)

    def test_first_bar_skips_signal_evaluation(self):
        runtime = NativeBacktestRuntime(Params(), DummyLogger())
        runtime.process_bar(Bar(datetime(2026,1,1,10,0),100,101,99,100.5,100))
        self.assertEqual(runtime.state.virtual_position_size, 0)

    def test_signal_uses_previous_available_bar_and_entry_is_current_open(self):
        runtime = NativeBacktestRuntime(Params(), DummyLogger())
        bars=[
            Bar(datetime(2026,1,1,10,0),100,100.1,100,100.05,100),
            Bar(datetime(2026,1,1,10,1),100,101,99,100.5,100),
            Bar(datetime(2026,1,1,10,2),102,102.5,101.8,102.2,100),
        ]
        for bar in bars: runtime.process_bar(bar)
        self.assertEqual(runtime.state.virtual_entry_price,102.0)
        self.assertEqual(runtime._trade_ledger.records[-1]["entry_bar"],3)

    def test_entry_is_exposed_to_remainder_of_same_bar(self):
        runtime = NativeBacktestRuntime(Params(), DummyLogger())
        bars=[
            Bar(datetime(2026,1,1,10,0),100,100.1,100,100.05,100),
            Bar(datetime(2026,1,1,10,1),100,101,99,100.5,100),
            Bar(datetime(2026,1,1,10,2),102,104,101.5,103,100),
        ]
        for bar in bars: runtime.process_bar(bar)
        self.assertEqual(runtime._trade_ledger.records[-1]["entry_price"],102.0)
        self.assertEqual(runtime._trade_ledger.records[-1]["exit_reason"],"TAKE_PROFIT")

    def test_long_sl_crossing_closes_position(self):
        h=NativeHarness(position=5,entry=100,sl=99,tp=102)
        h._process_monotonic_segment(100,98.5,11,0)
        self.assertEqual(h._trade_ledger.records[0]["exit_reason"],"STOP_LOSS")
        self.assertEqual(h.state.virtual_position_size,0)

    def test_short_tp_crossing_closes_position(self):
        h=NativeHarness(position=-5,entry=100,sl=102,tp=98)
        h._process_monotonic_segment(100,97.5,11,0)
        self.assertEqual(h._trade_ledger.records[0]["exit_reason"],"TAKE_PROFIT")

    def test_short_sl_crossing_closes_position(self):
        h=NativeHarness(position=-5,entry=100,sl=101,tp=98)
        h._process_monotonic_segment(100,102,11,0)
        self.assertEqual(h._trade_ledger.records[0]["exit_reason"],"STOP_LOSS")

    def test_exit_has_priority_over_trail_at_same_crossing_price(self):
        h=NativeHarness(position=1,entry=100,sl=99,tp=101)
        h.params.dynamic_trail_steps=[(1.0/1.9,0.5)]
        h._process_monotonic_segment(100,101,11,0)
        self.assertEqual(h._trade_ledger.records[0]["exit_reason"],"TAKE_PROFIT")

    def test_zero_length_phase_does_not_cross_levels(self):
        h=NativeHarness(position=5,entry=100,sl=98,tp=102)
        self.assertFalse(h._process_monotonic_segment(100,100,11,0))
        self.assertEqual(h.state.virtual_position_size,5)
        self.assertEqual(h.state.current_trail_step,-1)

    def test_close_clears_position_levels_and_entry_state(self):
        h=NativeHarness(position=5,entry=100,sl=98,tp=102)
        h._close_virtual_position("TAKE_PROFIT",102,102,11,2)
        self.assertEqual(h.state.virtual_position_size,0)
        self.assertIsNone(h.state.virtual_entry_price)
        self.assertIsNone(h.state.tp_level)
        self.assertIsNone(h.state.sl_level)
        self.assertEqual(h.state.current_trail_step,-1)

    def test_dynamic_slippage_schedule_is_stable(self):
        self.assertEqual({n:ExecutionEngine.get_backtest_dynamic_slippage(n) for n in (1,5,6,15,16,30,31)}, {1:0.02,5:0.02,6:0.04,15:0.04,16:0.07,30:0.07,31:0.15})

    def test_doji_path_is_open_low_high_close(self):
        h=NativeHarness(position=1,entry=100,sl=98,tp=105)
        seen=[]
        def spy(start,end,bar_index,phase_index):
            seen.append((start,end)); return False
        bar=Bar(datetime(2026,1,1),100,103,99,100,100)
        h.market.observe(bar)
        h._execution_engine.process_bar(context=h._execution_context,bar=bar,bar_index=7,segment_processor=spy)
        self.assertEqual(seen,[(100,99),(99,103),(103,100)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
