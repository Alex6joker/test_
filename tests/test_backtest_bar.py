from __future__ import annotations

import unittest
import os
import sys
import types

core_pkg = types.ModuleType("core")
core_pkg.__path__ = [os.path.join(os.path.dirname(__file__), "..", "core")]
sys.modules.setdefault("core", core_pkg)

from dataclasses import FrozenInstanceError
from datetime import datetime

from core.backtest_bar import Bar


def make_bar(**overrides):
    values = {
        "datetime": datetime(2026, 1, 1, 10, 0),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 100,
    }
    values.update(overrides)
    return Bar(**values)


class BacktestBarTests(unittest.TestCase):
    def test_bar_stores_all_market_values(self):
        dt = datetime(2026, 1, 1, 10, 15, 30)
        bar = Bar(dt, 100.123456, 101.987654, 99.111111, 100.555555, 123456)

        self.assertEqual(bar.datetime, dt)
        self.assertEqual(bar.open, 100.123456)
        self.assertEqual(bar.high, 101.987654)
        self.assertEqual(bar.low, 99.111111)
        self.assertEqual(bar.close, 100.555555)
        self.assertEqual(bar.volume, 123456)

    def test_bar_is_immutable(self):
        bar = make_bar()

        with self.assertRaises(FrozenInstanceError):
            bar.close = 999.0

    def test_equal_bars_are_equal(self):
        self.assertEqual(make_bar(), make_bar())

    def test_bar_is_hashable(self):
        bar = make_bar()
        self.assertEqual(len({bar}), 1)

    def test_bar_contains_only_market_observation_fields(self):
        bar = make_bar()

        for name in (
            "position_size",
            "entry_price",
            "tp_level",
            "sl_level",
            "current_trail_step",
            "trade_id",
            "commission",
            "cash",
            "equity",
        ):
            self.assertFalse(hasattr(bar, name), name)

    def test_bar_has_expected_public_fields_only(self):
        self.assertEqual(
            set(Bar.__dataclass_fields__),
            {"datetime", "open", "high", "low", "close", "volume"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
