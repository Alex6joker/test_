from __future__ import annotations

import os
import sys
import importlib.util
import types
import unittest
from datetime import datetime

core_pkg = types.ModuleType("core")
core_pkg.__path__ = [os.path.join(os.path.dirname(__file__), "..", "core")]
sys.modules.setdefault("core", core_pkg)

from core.backtest_adapter import BacktraderBarAdapter, BacktraderFeedAdapter
from core.backtest_bar import Bar
from core.backtest_market import Market


class Line:
    def __init__(self, values):
        self.values = list(values)

    def __getitem__(self, index):
        if index == 0:
            return self.values[-1]
        if index == -1 and len(self.values) >= 2:
            return self.values[-2]
        raise IndexError(index)


class DateLine:
    def __init__(self, values):
        self.values = list(values)

    def datetime(self, index):
        if index == 0:
            return self.values[-1]
        if index == -1 and len(self.values) >= 2:
            return self.values[-2]
        raise IndexError(index)


class DummyBacktraderData:
    def __init__(self, *, datetimes, opens, highs, lows, closes, volumes):
        self.datetime = DateLine(datetimes)
        self.open = Line(opens)
        self.high = Line(highs)
        self.low = Line(lows)
        self.close = Line(closes)
        self.volume = Line(volumes)


class BacktestAdapterTests(unittest.TestCase):
    def make_data(self):
        return DummyBacktraderData(
            datetimes=[datetime(2026, 1, 1, 10, 0), datetime(2026, 1, 1, 10, 1)],
            opens=[100.0, 101.5],
            highs=[101.0, 103.0],
            lows=[99.5, 100.75],
            closes=[100.5, 102.25],
            volumes=[120, 250],
        )

    def test_converts_current_backtrader_observation_to_bar(self):
        data = self.make_data()

        bar = BacktraderBarAdapter.to_bar(data)

        self.assertIsInstance(bar, Bar)
        self.assertEqual(bar.datetime, datetime(2026, 1, 1, 10, 1))
        self.assertEqual(bar.open, 101.5)
        self.assertEqual(bar.high, 103.0)
        self.assertEqual(bar.low, 100.75)
        self.assertEqual(bar.close, 102.25)
        self.assertEqual(bar.volume, 250)

    def test_adapter_converts_numeric_values_to_bar_types(self):
        data = DummyBacktraderData(
            datetimes=[datetime(2026, 1, 1, 10, 1)],
            opens=[101],
            highs=[103],
            lows=[100],
            closes=[102],
            volumes=[250.9],
        )

        bar = BacktraderBarAdapter.to_bar(data)

        self.assertIs(type(bar.open), float)
        self.assertIs(type(bar.high), float)
        self.assertIs(type(bar.low), float)
        self.assertIs(type(bar.close), float)
        self.assertIs(type(bar.volume), int)

    def test_adapter_preserves_doji_ohlc_without_execution_semantics(self):
        data = DummyBacktraderData(
            datetimes=[datetime(2026, 1, 1, 10, 1)],
            opens=[100.0],
            highs=[101.0],
            lows=[99.0],
            closes=[100.0],
            volumes=[500],
        )

        bar = BacktraderBarAdapter.to_bar(data)

        self.assertEqual((bar.open, bar.high, bar.low, bar.close), (100.0, 101.0, 99.0, 100.0))

    def test_adapter_preserves_current_and_previous_available_observations(self):
        data = self.make_data()
        market = Market()

        previous_bar = BacktraderBarAdapter.to_bar(data, index=-1)
        current_bar = BacktraderBarAdapter.to_bar(data, index=0)
        market.observe(previous_bar)
        market.observe(current_bar)

        self.assertIs(market.previous_bar, previous_bar)
        self.assertIs(market.current_bar, current_bar)
        self.assertEqual(market.bar_index, 2)

    @unittest.skipUnless(importlib.util.find_spec("backtrader"), "Backtrader is not installed")
    def test_feed_adapter_owns_backtrader_csv_mapping(self):
        feed = BacktraderFeedAdapter.create_feed("sample.csv")
        self.assertEqual(feed.p.dataname, "sample.csv")
        self.assertEqual(feed.p.datetime, 0)
        self.assertEqual(feed.p.open, 1)
        self.assertEqual(feed.p.high, 2)
        self.assertEqual(feed.p.low, 3)
        self.assertEqual(feed.p.close, 4)
        self.assertEqual(feed.p.volume, 5)
        self.assertEqual(feed.p.openinterest, -1)

    def test_adapter_does_not_modify_source_data(self):
        data = self.make_data()
        expected = (101.5, 103.0, 100.75, 102.25, 250)

        BacktraderBarAdapter.to_bar(data)

        actual = (
            data.open[0],
            data.high[0],
            data.low[0],
            data.close[0],
            data.volume[0],
        )
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
