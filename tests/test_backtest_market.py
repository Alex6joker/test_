from __future__ import annotations

import unittest
import os
import sys
import types

core_pkg = types.ModuleType("core")
core_pkg.__path__ = [os.path.join(os.path.dirname(__file__), "..", "core")]
sys.modules.setdefault("core", core_pkg)

from datetime import datetime, timedelta

from core.backtest_bar import Bar
from core.backtest_market import Market


def make_bar(minute: int, **overrides) -> Bar:
    values = {
        "datetime": datetime(2026, 1, 1, 10, 0) + timedelta(minutes=minute),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 100,
    }
    values.update(overrides)
    return Bar(**values)


class BacktestMarketTests(unittest.TestCase):
    def test_empty_market_has_no_bars(self):
        market = Market()

        self.assertIsNone(market.current_bar)
        self.assertIsNone(market.previous_bar)
        self.assertEqual(market.bar_index, 0)
        self.assertFalse(market.has_current_bar)
        self.assertFalse(market.has_previous_bar)

    def test_first_observed_bar_becomes_current(self):
        market = Market()
        first = make_bar(0)

        market.observe(first)

        self.assertIs(market.current_bar, first)
        self.assertIsNone(market.previous_bar)
        self.assertEqual(market.bar_index, 1)
        self.assertTrue(market.has_current_bar)
        self.assertFalse(market.has_previous_bar)

    def test_second_observed_bar_moves_first_to_previous(self):
        market = Market()
        first = make_bar(0)
        second = make_bar(1)

        market.observe(first)
        market.observe(second)

        self.assertIs(market.current_bar, second)
        self.assertIs(market.previous_bar, first)
        self.assertEqual(market.bar_index, 2)
        self.assertTrue(market.has_previous_bar)

    def test_third_observed_bar_keeps_only_previous_and_current(self):
        market = Market()
        first = make_bar(0)
        second = make_bar(1)
        third = make_bar(2)

        market.observe(first)
        market.observe(second)
        market.observe(third)

        self.assertIs(market.current_bar, third)
        self.assertIs(market.previous_bar, second)
        self.assertEqual(market.bar_index, 3)

    def test_current_bar_is_never_exposed_as_previous_bar(self):
        market = Market()
        first = make_bar(0)
        second = make_bar(1)

        market.observe(first)
        self.assertIsNone(market.previous_bar)

        market.observe(second)
        self.assertIs(market.previous_bar, first)
        self.assertIsNot(market.previous_bar, second)

    def test_previous_bar_means_previous_available_bar_not_previous_minute(self):
        market = Market()
        bar_1000 = make_bar(0)
        bar_1001 = make_bar(1)
        bar_1005 = make_bar(5)

        market.observe(bar_1000)
        market.observe(bar_1001)
        market.observe(bar_1005)

        self.assertIs(market.previous_bar, bar_1001)
        self.assertIs(market.current_bar, bar_1005)
        self.assertEqual(market.bar_index, 3)

    def test_market_preserves_observation_order_and_does_not_sort(self):
        market = Market()
        later = make_bar(5)
        earlier = make_bar(1)

        market.observe(later)
        market.observe(earlier)

        self.assertIs(market.previous_bar, later)
        self.assertIs(market.current_bar, earlier)

    def test_market_does_not_fill_missing_bars(self):
        market = Market()
        bar_1000 = make_bar(0)
        bar_1005 = make_bar(5)

        market.observe(bar_1000)
        market.observe(bar_1005)

        self.assertEqual(market.bar_index, 2)
        self.assertIs(market.previous_bar, bar_1000)
        self.assertIs(market.current_bar, bar_1005)

    def test_market_does_not_deduplicate_identical_observations(self):
        market = Market()
        bar = make_bar(0)

        market.observe(bar)
        market.observe(bar)

        self.assertEqual(market.bar_index, 2)
        self.assertIs(market.current_bar, bar)
        self.assertIs(market.previous_bar, bar)

    def test_market_preserves_bar_object_identity(self):
        market = Market()
        first = make_bar(0)
        second = make_bar(1)

        market.observe(first)
        market.observe(second)

        self.assertIs(market.previous_bar, first)
        self.assertIs(market.current_bar, second)

    def test_market_does_not_modify_bar_values(self):
        market = Market()
        bar = make_bar(
            0,
            open=100.123456,
            high=101.987654,
            low=99.111111,
            close=100.555555,
            volume=123456,
        )

        market.observe(bar)

        current = market.current_bar
        self.assertEqual(current.open, 100.123456)
        self.assertEqual(current.high, 101.987654)
        self.assertEqual(current.low, 99.111111)
        self.assertEqual(current.close, 100.555555)
        self.assertEqual(current.volume, 123456)

    def test_bar_index_increments_exactly_once_per_observation(self):
        market = Market()

        for expected_index in range(1, 11):
            market.observe(make_bar(expected_index))
            self.assertEqual(market.bar_index, expected_index)

    def test_large_sequence_keeps_correct_previous_and_current(self):
        market = Market()
        bars = [make_bar(i) for i in range(100)]

        for bar in bars:
            market.observe(bar)

        self.assertEqual(market.bar_index, 100)
        self.assertIs(market.current_bar, bars[-1])
        self.assertIs(market.previous_bar, bars[-2])

    def test_first_bar_has_no_previous_bar(self):
        market = Market()
        market.observe(make_bar(0))

        self.assertFalse(market.has_previous_bar)
        self.assertIsNone(market.previous_bar)

    def test_second_bar_has_previous_bar(self):
        market = Market()
        market.observe(make_bar(0))
        self.assertFalse(market.has_previous_bar)

        market.observe(make_bar(1))
        self.assertTrue(market.has_previous_bar)
        self.assertIsNotNone(market.previous_bar)

    def test_strategy_previous_candle_semantics(self):
        market = Market()
        first = make_bar(
            0,
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=500,
        )
        second = make_bar(
            1,
            open=110.0,
            high=111.0,
            low=109.0,
            close=110.5,
            volume=700,
        )

        market.observe(first)
        self.assertIsNone(market.previous_bar)

        market.observe(second)

        self.assertIs(market.previous_bar, first)
        self.assertIs(market.current_bar, second)
        self.assertEqual(market.previous_bar.open, 100.0)
        self.assertEqual(market.previous_bar.high, 102.0)
        self.assertEqual(market.previous_bar.low, 99.0)
        self.assertEqual(market.previous_bar.close, 101.0)
        self.assertEqual(market.previous_bar.volume, 500)
        self.assertEqual(market.current_bar.open, 110.0)

    def test_doji_ohlc_is_preserved_without_execution_semantics(self):
        market = Market()
        doji = make_bar(
            0,
            open=100.0,
            high=103.0,
            low=99.0,
            close=100.0,
        )

        market.observe(doji)

        self.assertEqual(market.current_bar.open, 100.0)
        self.assertEqual(market.current_bar.high, 103.0)
        self.assertEqual(market.current_bar.low, 99.0)
        self.assertEqual(market.current_bar.close, 100.0)

    def test_observe_rejects_non_bar_objects(self):
        market = Market()

        with self.assertRaises(TypeError):
            market.observe(object())

        self.assertEqual(market.bar_index, 0)
        self.assertIsNone(market.current_bar)
        self.assertIsNone(market.previous_bar)


if __name__ == "__main__":
    unittest.main(verbosity=2)
