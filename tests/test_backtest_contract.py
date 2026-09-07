from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass
from datetime import datetime

core_pkg = types.ModuleType("core")
core_pkg.__path__ = [__import__("os").path.join(__import__("os").path.dirname(__file__), "..", "core")]
sys.modules.setdefault("core", core_pkg)

from core.backtest_accounting import BacktestAccountingMixin
from core.backtest_commission import BacktestCommissionMixin
from core.backtest_execution import BacktestExecutionMixin
from core.backtest_numeric import BacktestNumericMixin
from core.backtest_signal import BacktestSignalMixin
from core.backtest_state import BacktestState
from core.backtest_trade import BacktestTradeMixin
from core.backtest_trailing import BacktestTrailingMixin


class DummyLogger:
    def wants_event(self, _name):
        return False

    def wants_debug_event(self, _name):
        return False

    def wants_trade(self):
        return False

    def wants_warning_or_error(self):
        return False

    def event(self, *_args, **_kwargs):
        pass

    def debug_event(self, *_args, **_kwargs):
        pass

    def trade(self, *_args, **_kwargs):
        pass


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
    dynamic_trail_steps: list[tuple[float, float]] = None
    initial_cash: float = 264000.0

    def __post_init__(self):
        if self.dynamic_trail_steps is None:
            self.dynamic_trail_steps = [(0.35, 0.05), (0.75, 0.15), (0.95, 0.65)]


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float


class Line:
    def __init__(self, value):
        self.value = value

    def __getitem__(self, index):
        if index in (0, -1):
            return self.value
        raise IndexError(index)


class DateLine:
    def __getitem__(self, index):
        if index in (0, -1):
            return 0.0
        raise IndexError(index)

    def datetime(self, index):
        return datetime(2026, 1, 1)


class DummyData:
    def __init__(self, bar: Bar, previous_volume: int = 100):
        self.open = Line(bar.open)
        self.high = Line(bar.high)
        self.low = Line(bar.low)
        self.close = Line(bar.close)
        self.volume = Line(previous_volume)
        self.datetime = DateLine()


class DummyCommissionInfo:
    class P:
        commission = 10.185

    p = P()


class DummyBroker:
    def getcommissioninfo(self, _data):
        return DummyCommissionInfo()


class MultiLine:
    def __init__(self, values, current_index=0):
        self.values = list(values)
        self.current_index = current_index

    def __getitem__(self, index):
        pos = self.current_index + index
        if pos < 0 or pos >= len(self.values):
            raise IndexError(index)
        return self.values[pos]


class MultiBarData:
    def __init__(self, bars, volumes, current_index=0):
        self._bars = list(bars)
        self.current_index = current_index
        self.open = MultiLine([b.open for b in bars], current_index)
        self.high = MultiLine([b.high for b in bars], current_index)
        self.low = MultiLine([b.low for b in bars], current_index)
        self.close = MultiLine([b.close for b in bars], current_index)
        self.volume = MultiLine(volumes, current_index)
        self.datetime = DateLine()

    def __len__(self):
        return self.current_index + 1


class VirtualStrategyContract(
    BacktestNumericMixin,
    BacktestExecutionMixin,
    BacktestTrailingMixin,
    BacktestTradeMixin,
    BacktestAccountingMixin,
    BacktestCommissionMixin,
    BacktestSignalMixin,
):
    # Test-only compatibility facade. Production mixins access state directly.
    @property
    def trade_id(self): return self.state.trade_id
    @trade_id.setter
    def trade_id(self, value): self.state.trade_id = value

    @property
    def last_trade_bar(self): return self.state.last_trade_bar
    @last_trade_bar.setter
    def last_trade_bar(self, value): self.state.last_trade_bar = value

    @property
    def virtual_cash(self): return self.state.virtual_cash
    @virtual_cash.setter
    def virtual_cash(self, value): self.state.virtual_cash = value

    @property
    def virtual_position_size(self): return self.state.virtual_position_size
    @virtual_position_size.setter
    def virtual_position_size(self, value): self.state.virtual_position_size = value

    @property
    def virtual_entry_price(self): return self.state.virtual_entry_price
    @virtual_entry_price.setter
    def virtual_entry_price(self, value): self.state.virtual_entry_price = value

    @property
    def virtual_entry_commission(self): return self.state.virtual_entry_commission
    @virtual_entry_commission.setter
    def virtual_entry_commission(self, value): self.state.virtual_entry_commission = value

    @property
    def virtual_exit_commission(self): return self.state.virtual_exit_commission
    @virtual_exit_commission.setter
    def virtual_exit_commission(self, value): self.state.virtual_exit_commission = value

    @property
    def virtual_gross_pnl(self): return self.state.virtual_gross_pnl
    @virtual_gross_pnl.setter
    def virtual_gross_pnl(self, value): self.state.virtual_gross_pnl = value

    @property
    def entry_price(self): return self.state.entry_price
    @entry_price.setter
    def entry_price(self, value): self.state.entry_price = value

    @property
    def tp_level(self): return self.state.tp_level
    @tp_level.setter
    def tp_level(self, value): self.state.tp_level = value

    @property
    def sl_level(self): return self.state.sl_level
    @sl_level.setter
    def sl_level(self, value): self.state.sl_level = value

    @property
    def current_trail_step(self): return self.state.current_trail_step
    @current_trail_step.setter
    def current_trail_step(self, value): self.state.current_trail_step = value

    @property
    def closed_trades(self): return self.state.closed_trades
    @closed_trades.setter
    def closed_trades(self, value): self.state.closed_trades = value

    @property
    def total_contracts(self): return self.state.total_contracts
    @total_contracts.setter
    def total_contracts(self, value): self.state.total_contracts = value

    @property
    def total_commission(self): return self.state.total_commission
    @total_commission.setter
    def total_commission(self, value): self.state.total_commission = value

    @property
    def final_virtual_equity(self): return self.state.final_virtual_equity
    @final_virtual_equity.setter
    def final_virtual_equity(self, value): self.state.final_virtual_equity = value

    @property
    def _trade_records(self): return self.state.trade_records
    @_trade_records.setter
    def _trade_records(self, value): self.state.trade_records = value

    @property
    def _closed_trade_records(self): return self.state.closed_trade_records
    @_closed_trade_records.setter
    def _closed_trade_records(self, value): self.state.closed_trade_records = value

    def __init__(self, *, bar: Bar, position: int, entry: float, sl: float, tp: float):
        self.params = Params()
        self.logger = DummyLogger()
        self.data = DummyData(bar)
        self.broker = DummyBroker()
        self.state = BacktestState()

        self.trade_id = 1
        self.last_trade_bar = -1
        self.virtual_cash = self._money(self.params.initial_cash)
        self.virtual_position_size = position
        self.virtual_entry_price = entry
        self.virtual_entry_commission = self._money(10.185 * abs(position))
        if position:
            self.virtual_cash = self._money(self.virtual_cash - self.virtual_entry_commission)
        self.virtual_exit_commission = 0.0
        self.virtual_gross_pnl = 0.0
        self.entry_price = entry
        self.tp_level = tp
        self.sl_level = sl
        self.current_trail_step = -1
        self.closed_trades = 0
        self.total_contracts = 0
        self.total_commission = self.virtual_entry_commission
        self.final_virtual_equity = self.virtual_cash
        self._trade_records = [{
            "trade_id": 1,
            "direction": "LONG" if position > 0 else "SHORT",
            "size": abs(position),
            "entry_bar": 2,
            "entry_datetime": datetime(2026, 1, 1),
            "entry_price": entry,
            "entry_commission": self.virtual_entry_commission,
            "exit_bar": None,
            "exit_phase": None,
            "exit_price": None,
            "exit_reason": None,
            "exit_commission": 0.0,
            "gross_pnl": 0.0,
            "net_pnl": None,
        }]
        self._closed_trade_records = []

    def get_backtest_dynamic_slippage(self, size: int) -> float:
        return super().get_backtest_dynamic_slippage(size)


class BacktestContractTests(unittest.TestCase):
    def test_position_size_uses_previous_volume_and_all_limits(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 101.0, 99.0, 100.5),
            position=0,
            entry=0.0,
            sl=98.0,
            tp=105.0,
        )
        s.virtual_cash = 264000.0
        s.data.volume = Line(100)
        size = s._calculate_position_size(10)
        # Risk allows 60 contracts, margin allows 12, liquidity allows 5.
        self.assertEqual(size, 5)

    def test_entry_contract_sets_levels_and_charges_entry_commission(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 101.0, 99.0, 100.5),
            position=0,
            entry=0.0,
            sl=0.0,
            tp=0.0,
        )
        s.virtual_position_size = 0
        s.virtual_entry_price = None
        s._trade_records = []
        s._closed_trade_records = []
        s.trade_id = 0
        s.virtual_cash = 264000.0
        s.total_commission = 0.0
        s.closed_trades = 0
        s.total_contracts = 0
        s._open_virtual_position(1, 5, 10)
        self.assertEqual(s.virtual_position_size, 5)
        self.assertEqual(s.virtual_entry_price, 100.0)
        self.assertEqual(s.tp_level, 101.9)
        self.assertEqual(s.sl_level, 98.3)
        self.assertEqual(s.virtual_entry_commission, 50.93)
        self.assertEqual(s.virtual_cash, 263949.07)

    def test_exit_contract_realizes_gross_pnl_and_both_commissions(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 101.0, 99.0, 100.5),
            position=5,
            entry=100.0,
            sl=98.0,
            tp=102.0,
        )
        s._close_virtual_position("TAKE_PROFIT", 101.98, 102.0, 11, 2)
        record = s._trade_records[0]
        self.assertEqual(record["exit_price"], 101.98)
        self.assertEqual(record["gross_pnl"], 7692.3)
        self.assertEqual(record["entry_commission"], 50.93)
        self.assertEqual(record["exit_commission"], 50.93)
        self.assertEqual(record["net_pnl"], 7590.44)
        self.assertEqual(s.virtual_position_size, 0)
        self.assertEqual(s.closed_trades, 1)
        self.assertEqual(s.total_contracts, 10)

    def test_accounting_check_matches_closed_trade_ledger(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 101.0, 99.0, 100.5),
            position=5,
            entry=100.0,
            sl=98.0,
            tp=102.0,
        )
        s._close_virtual_position("TAKE_PROFIT", 101.98, 102.0, 11, 2)
        s._check_trade_lifecycle()
        s._check_negative_cash()
        s._check_accounting()

    def test_trail_cannot_retroactively_protect_an_earlier_phase(self):
        """A stop crossed before a later favorable phase must use the old SL."""
        s = VirtualStrategyContract(
            bar=Bar(99.0, 100.0, 97.90, 99.19),
            position=10,
            entry=97.0,
            sl=98.0,
            tp=110.0,
        )
        # Low->High is the favorable phase. First prove that the earlier
        # downward phase closes on the original stop before any trail exists.
        closed = s._process_monotonic_segment(99.0, 97.90, 10, 1)
        self.assertTrue(closed)
        self.assertEqual(s._trade_records[0]["exit_reason"], "STOP_LOSS")

    def test_short_trail_is_used_only_by_subsequent_movement(self):
        s = VirtualStrategyContract(
            bar=Bar(104.62, 104.62, 103.25, 104.0),
            position=-10,
            entry=105.0,
            sl=105.0,
            tp=100.0,
        )
        s.params.dynamic_trail_steps = [(0.50, 0.25)]
        s.sl_level = 105.0
        s.tp_level = 100.0

        # Favorable downward phase crosses the trigger and moves SL.
        closed = s._process_monotonic_segment(104.62, 103.25, 10, 2)
        self.assertFalse(closed)
        self.assertEqual(s.current_trail_step, 0)
        self.assertEqual(s.sl_level, 104.53)

        # The new SL is then valid for a later upward phase.
        closed = s._process_monotonic_segment(103.25, 104.62, 10, 3)
        self.assertTrue(closed)
        self.assertEqual(s._trade_records[0]["exit_reason"], "STOP_LOSS")
        self.assertEqual(s._trade_records[0]["exit_phase"], 3)

    def test_multiple_trail_steps_are_only_activated_when_crossed(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 103.0, 100.0, 103.0),
            position=1,
            entry=100.0,
            sl=98.0,
            tp=110.0,
        )
        s.params.dynamic_trail_steps = [(0.20, 0.05), (0.50, 0.15), (0.90, 0.65)]
        s._process_monotonic_segment(100.0, 106.0, 1, 0)
        self.assertEqual(s.current_trail_step, 2)
        self.assertEqual(s.sl_level, 101.23)

    def test_unreached_trail_steps_remain_unactivated(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 100.8, 100.0, 100.8),
            position=1,
            entry=100.0,
            sl=98.0,
            tp=110.0,
        )
        s.params.dynamic_trail_steps = [(0.20, 0.05), (0.50, 0.15)]
        s._process_monotonic_segment(100.0, 100.8, 1, 0)
        self.assertEqual(s.current_trail_step, 0)
        self.assertEqual(s.sl_level, 100.09)

    def test_first_bar_skips_signal_evaluation(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 101.0, 99.0, 100.5),
            position=0,
            entry=0.0,
            sl=0.0,
            tp=0.0,
        )
        s.data = MultiBarData([Bar(100, 101, 99, 100)], [100], 0)
        s.next()
        self.assertEqual(s.virtual_position_size, 0)
        self.assertEqual(s.trade_id, 1)

    def test_second_bar_uses_previous_candle_and_enters_at_current_open(self):
        s = VirtualStrategyContract(
            bar=Bar(102.0, 102.5, 101.5, 102.2),
            position=0,
            entry=0.0,
            sl=0.0,
            tp=0.0,
        )
        s.data = MultiBarData(
            [Bar(100.0, 101.0, 99.0, 100.5), Bar(102.0, 102.2, 101.8, 102.0)],
            [100, 100],
            1,
        )
        s.next()
        self.assertEqual(s.virtual_entry_price, 102.0)
        self.assertEqual(s._trade_records[-1]["entry_bar"], 2)

    def test_entry_is_exposed_to_remainder_of_same_bar(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 101.0, 99.0, 100.5),
            position=0,
            entry=0.0,
            sl=0.0,
            tp=0.0,
        )
        s.data = MultiBarData(
            [Bar(100.0, 101.0, 99.0, 100.5), Bar(102.0, 104.0, 101.5, 103.0)],
            [100, 100],
            1,
        )
        s.next()
        self.assertEqual(s._trade_records[-1]["entry_price"], 102.0)
        self.assertEqual(s._trade_records[-1]["exit_reason"], "TAKE_PROFIT")

    def test_long_sl_crossing_closes_position(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 101.0, 98.0, 99.0),
            position=5,
            entry=100.0,
            sl=99.0,
            tp=102.0,
        )
        s._process_monotonic_segment(100.0, 98.5, 11, 0)
        self.assertEqual(s._trade_records[0]["exit_reason"], "STOP_LOSS")
        self.assertEqual(s.virtual_position_size, 0)

    def test_short_tp_crossing_closes_position(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 101.0, 98.0, 99.0),
            position=-5,
            entry=100.0,
            sl=102.0,
            tp=98.0,
        )
        s._process_monotonic_segment(100.0, 97.5, 11, 0)
        self.assertEqual(s._trade_records[0]["exit_reason"], "TAKE_PROFIT")
        self.assertEqual(s.virtual_position_size, 0)

    def test_short_sl_crossing_closes_position(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 102.0, 99.0, 101.0),
            position=-5,
            entry=100.0,
            sl=101.0,
            tp=98.0,
        )
        s._process_monotonic_segment(100.0, 102.0, 11, 0)
        self.assertEqual(s._trade_records[0]["exit_reason"], "STOP_LOSS")
        self.assertEqual(s.virtual_position_size, 0)

    def test_exit_has_priority_over_trail_at_same_crossing_price(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 102.0, 99.0, 102.0),
            position=1,
            entry=100.0,
            sl=99.0,
            tp=101.0,
        )
        s.params.dynamic_trail_steps = [(1.0 / 1.9, 0.5)]
        s._process_monotonic_segment(100.0, 101.0, 11, 0)
        self.assertEqual(s._trade_records[0]["exit_reason"], "TAKE_PROFIT")
        self.assertEqual(s.virtual_position_size, 0)

    def test_zero_length_phase_does_not_cross_levels(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 101.0, 99.0, 100.0),
            position=5,
            entry=100.0,
            sl=98.0,
            tp=102.0,
        )
        closed = s._process_monotonic_segment(100.0, 100.0, 11, 0)
        self.assertFalse(closed)
        self.assertEqual(s.virtual_position_size, 5)
        self.assertEqual(s.current_trail_step, -1)

    def test_close_clears_position_levels_and_entry_state(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 101.0, 99.0, 100.0),
            position=5,
            entry=100.0,
            sl=98.0,
            tp=102.0,
        )
        s._close_virtual_position("TAKE_PROFIT", 102.0, 102.0, 11, 2)
        self.assertEqual(s.virtual_position_size, 0)
        self.assertIsNone(s.virtual_entry_price)
        self.assertIsNone(s.entry_price)
        self.assertIsNone(s.tp_level)
        self.assertIsNone(s.sl_level)
        self.assertEqual(s.current_trail_step, -1)

    def test_dynamic_slippage_schedule_is_stable(self):
        self.assertEqual(slip_values(), {1: 0.02, 5: 0.02, 6: 0.04, 15: 0.04, 16: 0.07, 30: 0.07, 31: 0.15})

    def test_doji_path_is_open_low_high_close(self):
        s = VirtualStrategyContract(
            bar=Bar(100.0, 103.0, 99.0, 100.0),
            position=1,
            entry=100.0,
            sl=98.0,
            tp=105.0,
        )
        seen = []
        original = s._process_monotonic_segment
        def spy(start_price, end_price, bar_index, phase_index):
            seen.append((start_price, end_price))
            return False
        s._process_monotonic_segment = spy
        s._process_open_position_bar(7)
        self.assertEqual(seen, [(100.0, 99.0), (99.0, 103.0), (103.0, 100.0)])
        s._process_monotonic_segment = original


def slip_values():
    s = VirtualStrategyContract(bar=Bar(100, 101, 99, 100), position=1, entry=100, sl=98, tp=105)
    return {n: s.get_backtest_dynamic_slippage(n) for n in (1, 5, 6, 15, 16, 30, 31)}


if __name__ == "__main__":
    unittest.main(verbosity=2)
