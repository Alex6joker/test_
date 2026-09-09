from __future__ import annotations

import os
import sys
import types
import unittest
from dataclasses import dataclass
from datetime import datetime

core_pkg = types.ModuleType("core")
core_pkg.__path__ = [os.path.join(os.path.dirname(__file__), "..", "core")]
sys.modules.setdefault("core", core_pkg)

from core.backtest_bar import Bar
from core.backtest_signal import EntryIntent, SignalEngine


@dataclass
class Params:
    trigger: float = 0.16
    sl: float = 1.70
    risk: float = 0.03
    real_mult: float = 777.0
    real_margin: float = 18200.0
    safety_factor: float = 1.15


class Logger:
    def __init__(self):
        self.events = []

    def wants_debug_event(self, _name):
        return True

    def debug_event(self, name, **kwargs):
        self.events.append((name, kwargs))


class SignalEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = SignalEngine(Params(), Logger(), lambda x: round(float(x), 2))
        self.dt = datetime(2026, 1, 1)

    def bar(self, o, h, l, c, volume=100):
        return Bar(self.dt, o, h, l, c, volume)

    def test_entry_intent_is_immutable(self):
        intent = EntryIntent(1, 4, 7)
        with self.assertRaises(Exception):
            intent.size = 5

    def test_long_signal(self):
        previous = self.bar(100.0, 101.0, 100.0, 100.5)
        current = self.bar(100.5, 101.0, 100.0, 100.2)
        intent = self.engine.evaluate(
            current_bar=current,
            previous_bar=previous,
            bar_index=2,
            position_size=0,
            virtual_cash=264000.0,
        )
        self.assertEqual(intent.direction, 1)
        self.assertEqual(intent.signal_bar_index, 2)

    def test_short_signal(self):
        previous = self.bar(100.5, 101.0, 100.0, 100.0)
        current = self.bar(100.0, 100.2, 99.5, 99.8)
        intent = self.engine.evaluate(
            current_bar=current,
            previous_bar=previous,
            bar_index=2,
            position_size=0,
            virtual_cash=264000.0,
        )
        self.assertEqual(intent.direction, -1)

    def test_no_signal_for_small_range(self):
        previous = self.bar(100.0, 100.1, 100.0, 100.05)
        current = self.bar(100.05, 100.2, 99.9, 100.0)
        intent = self.engine.evaluate(
            current_bar=current,
            previous_bar=previous,
            bar_index=2,
            position_size=0,
            virtual_cash=264000.0,
        )
        self.assertIsNone(intent)

    def test_existing_position_does_not_create_entry_intent(self):
        previous = self.bar(100.0, 101.0, 100.0, 100.5)
        current = self.bar(100.5, 101.0, 100.0, 100.2)
        intent = self.engine.evaluate(
            current_bar=current,
            previous_bar=previous,
            bar_index=2,
            position_size=4,
            virtual_cash=264000.0,
        )
        self.assertIsNone(intent)

    def test_existing_position_logs_evaluation_but_not_decision(self):
        previous = self.bar(100.5, 101.0, 100.0, 100.0)
        current = self.bar(100.0, 100.2, 99.5, 99.8)
        self.engine.evaluate(
            current_bar=current,
            previous_bar=previous,
            bar_index=2,
            position_size=-5,
            virtual_cash=264000.0,
        )
        names = [name for name, _ in self.engine.logger.events]
        self.assertEqual(names, ["SIGNAL_EVALUATION"])

    def test_position_sizing(self):
        size = self.engine.calculate_position_size(
            virtual_cash=264000.0,
            previous_volume=100,
            bar_index=2,
        )
        self.assertEqual(size, 5)


if __name__ == "__main__":
    unittest.main()
