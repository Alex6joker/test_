from __future__ import annotations

import unittest

import core.backtest_accounting as accounting
import core.backtest_execution as execution
import core.backtest_signal as signal
import core.backtest_trade as trade
import core.backtest_trailing as trailing
from core.backtest_loop import BacktestEngine, BacktestRuntime
from core.backtest_native import NativeBacktestRuntime


class NativeArchitectureTests(unittest.TestCase):
    def test_native_runtime_uses_composition_not_legacy_mixins(self):
        self.assertEqual(NativeBacktestRuntime.__bases__, (object,))

    def test_legacy_mixins_are_removed(self):
        self.assertFalse(hasattr(accounting, "BacktestAccountingMixin"))
        self.assertFalse(hasattr(signal, "BacktestSignalMixin"))
        self.assertFalse(hasattr(trade, "BacktestTradeMixin"))
        self.assertFalse(hasattr(trailing, "BacktestTrailingMixin"))
        self.assertFalse(hasattr(execution, "BacktestExecutionMixin"))

    def test_native_execution_context_is_explicit(self):
        self.assertTrue(hasattr(execution, "NativeExecutionContext"))
        self.assertFalse(hasattr(execution, "BacktestExecutionContext"))

    def test_loop_runtime_contract_exposes_native_services(self):
        annotations = getattr(BacktestRuntime, "__annotations__", {})
        self.assertIn("signal_engine", annotations)
        self.assertIn("execution_engine", annotations)
        self.assertIn("execution_context", annotations)

    def test_backtest_engine_has_no_legacy_runtime_method_names(self):
        source = BacktestEngine.process_bar.__code__.co_names
        self.assertNotIn("_open_virtual_position", source)
        self.assertNotIn("_log_virtual_portfolio", source)
        self.assertNotIn("_execution_engine", source)
        self.assertNotIn("_signal_engine", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
