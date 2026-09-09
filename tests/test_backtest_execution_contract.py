from __future__ import annotations

import os
import sys
import types
import unittest
from types import SimpleNamespace

core_pkg = types.ModuleType("core")
core_pkg.__path__ = [os.path.join(os.path.dirname(__file__), "..", "core")]
sys.modules.setdefault("core", core_pkg)

from core.backtest_execution import BacktestExecutionMixin, ExecutionEngine
from core.backtest_state import BacktestState
from core.backtest_bar import Bar
from core.backtest_market import Market
from datetime import datetime
from core.backtest_trailing import BacktestTrailingMixin


class FakeLogger:
    def wants_debug_event(self, _name):
        return False

    def wants_trade(self):
        return False


class ExecutionHarness(BacktestTrailingMixin, BacktestExecutionMixin):
    def __init__(self, *, direction=1, size=5, entry=100.0, sl=98.0, tp=103.0):
        self.state = BacktestState(
            virtual_position_size=direction * size,
            virtual_entry_price=entry,
            entry_price=entry,
            sl_level=sl,
            tp_level=tp,
            current_trail_step=-1,
            trade_id=1,
        )
        self.params = SimpleNamespace(
            tp=tp - entry,
            dynamic_trail_steps=[(0.35, 0.05), (0.75, 0.15), (0.95, 0.65)],
        )
        self.logger = FakeLogger()
        self.closed = []

    @staticmethod
    def _price(value):
        return round(float(value), 2)

    def _close_virtual_position(self, **kwargs):
        self.closed.append(kwargs)
        self.state.virtual_position_size = 0


class BacktestExecutionContractTests(unittest.TestCase):
    def test_dynamic_slippage_boundaries(self):
        self.assertEqual(ExecutionEngine.get_backtest_dynamic_slippage(5), 0.02)
        self.assertEqual(ExecutionEngine.get_backtest_dynamic_slippage(6), 0.04)
        self.assertEqual(ExecutionEngine.get_backtest_dynamic_slippage(15), 0.04)
        self.assertEqual(ExecutionEngine.get_backtest_dynamic_slippage(16), 0.07)
        self.assertEqual(ExecutionEngine.get_backtest_dynamic_slippage(30), 0.07)
        self.assertEqual(ExecutionEngine.get_backtest_dynamic_slippage(31), 0.15)

    def test_long_stop_loss_crossing_produces_execution_price_below_stop(self):
        engine = ExecutionHarness(direction=1, size=5, sl=98.0, tp=103.0)

        closed = engine._process_monotonic_segment(100.0, 97.0, 10, 1)

        self.assertTrue(closed)
        self.assertEqual(len(engine.closed), 1)
        result = engine.closed[0]
        self.assertEqual(result["reason"], "STOP_LOSS")
        self.assertEqual(result["detected_price"], 98.0)
        self.assertEqual(result["target_exec_price"], 97.98)
        self.assertEqual(result["bar_index"], 10)
        self.assertEqual(result["phase_index"], 1)

    def test_short_stop_loss_crossing_produces_execution_price_above_stop(self):
        engine = ExecutionHarness(direction=-1, size=5, sl=102.0, tp=97.0)

        closed = engine._process_monotonic_segment(100.0, 103.0, 11, 2)

        self.assertTrue(closed)
        result = engine.closed[0]
        self.assertEqual(result["reason"], "STOP_LOSS")
        self.assertEqual(result["detected_price"], 102.0)
        self.assertEqual(result["target_exec_price"], 102.02)

    def test_long_take_profit_crossing_produces_execution_price_below_tp(self):
        engine = ExecutionHarness(direction=1, size=5, sl=98.0, tp=103.0)

        closed = engine._process_monotonic_segment(100.0, 104.0, 12, 0)

        self.assertTrue(closed)
        result = engine.closed[0]
        self.assertEqual(result["reason"], "TAKE_PROFIT")
        self.assertEqual(result["detected_price"], 103.0)
        self.assertEqual(result["target_exec_price"], 102.98)

    def test_short_take_profit_crossing_produces_execution_price_above_tp(self):
        engine = ExecutionHarness(direction=-1, size=5, sl=102.0, tp=97.0)

        closed = engine._process_monotonic_segment(100.0, 96.0, 13, 0)

        self.assertTrue(closed)
        result = engine.closed[0]
        self.assertEqual(result["reason"], "TAKE_PROFIT")
        self.assertEqual(result["detected_price"], 97.0)
        self.assertEqual(result["target_exec_price"], 97.02)

    def test_equal_price_phase_does_not_generate_execution(self):
        engine = ExecutionHarness()

        closed = engine._process_monotonic_segment(100.0, 100.0, 14, 0)

        self.assertFalse(closed)
        self.assertEqual(engine.closed, [])

    def test_favorable_long_phase_applies_trail_before_later_adverse_phase(self):
        engine = ExecutionHarness(direction=1, size=5, entry=100.0, sl=98.0, tp=110.0)

        self.assertFalse(engine._process_monotonic_segment(100.0, 108.0, 15, 1))
        self.assertEqual(engine.state.current_trail_step, 1)
        self.assertEqual(engine.state.sl_level, 101.5)

        self.assertTrue(engine._process_monotonic_segment(108.0, 101.0, 15, 2))
        self.assertEqual(engine.closed[0]["reason"], "STOP_LOSS")
        self.assertEqual(engine.closed[0]["detected_price"], 101.5)

    def test_exit_at_same_price_has_priority_over_trail(self):
        engine = ExecutionHarness(direction=1, size=5, entry=100.0, sl=100.0, tp=103.0)

        closed = engine._process_monotonic_segment(100.0, 101.0, 16, 0)

        self.assertTrue(closed)
        self.assertEqual(engine.closed[0]["reason"], "STOP_LOSS")
        self.assertEqual(engine.state.current_trail_step, -1)

    def test_multiple_trail_steps_are_processed_in_traversal_order(self):
        engine = ExecutionHarness(direction=1, size=5, entry=100.0, sl=98.0, tp=110.0)

        closed = engine._process_monotonic_segment(100.0, 109.0, 17, 0)

        self.assertFalse(closed)
        self.assertEqual(engine.state.current_trail_step, 1)
        self.assertEqual(engine.state.sl_level, 101.5)

    def test_doji_path_is_open_low_high_close(self):
        engine = ExecutionHarness(direction=1, size=5, entry=100.0, sl=98.0, tp=103.0)
        market = Market()
        market.observe(Bar(datetime(2026, 1, 1), 100.0, 102.0, 99.0, 100.0, 1))
        engine.market = market

        phases = []
        original = engine._process_monotonic_segment

        def capture(start_price, end_price, bar_index, phase_index):
            phases.append((start_price, end_price, phase_index))
            return original(start_price, end_price, bar_index, phase_index)

        engine._process_monotonic_segment = capture
        engine._process_open_position_bar(18)

        self.assertEqual(
            phases,
            [(100.0, 99.0, 0), (99.0, 102.0, 1), (102.0, 100.0, 2)],
        )

    def test_processing_after_position_is_closed_stops(self):
        engine = ExecutionHarness(direction=1, size=5, sl=98.0, tp=103.0)
        market = Market()
        market.observe(Bar(datetime(2026, 1, 1), 100.0, 104.0, 97.0, 101.0, 1))
        engine.market = market

        phases = []
        original = engine._process_monotonic_segment

        def capture(start_price, end_price, bar_index, phase_index):
            phases.append(phase_index)
            return original(start_price, end_price, bar_index, phase_index)

        engine._process_monotonic_segment = capture
        engine._process_open_position_bar(19)

        self.assertEqual(phases, [0])
        self.assertEqual(len(engine.closed), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
