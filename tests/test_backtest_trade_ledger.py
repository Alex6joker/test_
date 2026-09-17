from __future__ import annotations

import unittest

from core.backtest_trade_ledger import TradeLedger
from core.backtest_trade import TradeAccountingEngine
from core.backtest_state import BacktestState
from types import SimpleNamespace


class TradeLedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = TradeLedger()

    def test_open_trade_creates_sequential_record(self):
        record = self.ledger.open_trade(
            direction="LONG", size=5, bar_index=10, entry_datetime="dt",
            entry_price=100.0, entry_commission=50.0,
        )
        self.assertEqual(record["trade_id"], 1)
        self.assertEqual(len(self.ledger.records), 1)
        self.assertIs(self.ledger.active_record, record)

    def test_second_trade_cannot_open_while_first_is_active(self):
        self.ledger.open_trade(
            direction="LONG", size=1, bar_index=1, entry_datetime="dt",
            entry_price=100.0, entry_commission=1.0,
        )
        with self.assertRaises(RuntimeError):
            self.ledger.open_trade(
                direction="SHORT", size=1, bar_index=2, entry_datetime="dt",
                entry_price=101.0, entry_commission=1.0,
            )

    def test_close_trade_moves_record_to_closed_records(self):
        record = self.ledger.open_trade(
            direction="SHORT", size=2, bar_index=1, entry_datetime="dt",
            entry_price=100.0, entry_commission=2.0,
        )
        self.ledger.close_trade(
            bar_index=2, phase_index=1, exit_price=98.0, reason="TAKE_PROFIT",
            exit_commission=2.0, gross_pnl=20.0, net_pnl=16.0,
        )
        self.assertEqual(record["exit_price"], 98.0)
        self.assertEqual(record["net_pnl"], 16.0)
        self.assertEqual(self.ledger.closed_records, [record])
        self.assertEqual(self.ledger.closed_trades, 1)
        self.assertEqual(self.ledger.total_contracts, 4)
        self.assertIsNone(self.ledger.active_record)

    def test_close_without_open_trade_fails(self):
        with self.assertRaises(RuntimeError):
            self.ledger.close_trade(
                bar_index=1, phase_index=0, exit_price=100.0,
                reason="STOP_LOSS", exit_commission=1.0,
                gross_pnl=-1.0, net_pnl=-2.0,
            )

    def test_check_detects_inconsistent_lifecycle(self):
        self.ledger._next_trade_id = 1
        self.ledger._records.append({
            "trade_id": 1, "exit_bar": None, "exit_price": None, "net_pnl": None,
        })
        self.assertEqual(self.ledger.check(0), [
            "open trade record exists while position is flat",
        ])


class TradeAccountingEngineTests(unittest.TestCase):
    def setUp(self):
        self.state = BacktestState(virtual_cash=264000.0)
        self.params = SimpleNamespace(real_mult=1000.0, tp=1.9, sl=1.7, precision_money=2)
        self.ledger = TradeLedger()
        self.accounting = __import__(
            "core.backtest_accounting", fromlist=["AccountingEngine"]
        ).AccountingEngine(
            self.state, self.params, lambda value: round(float(value), 2), 10.186
        )
        self.engine = TradeAccountingEngine(
            state=self.state, params=self.params, accounting=self.accounting,
            ledger=self.ledger, price_fn=lambda value: round(float(value), 2),
        )

    def test_open_transaction_updates_state_accounting_and_ledger(self):
        bar = SimpleNamespace(open=100.0, datetime="dt")
        record = self.engine.open(1, 5, 10, bar)
        self.assertEqual(record["trade_id"], 1)
        self.assertEqual(self.state.virtual_position_size, 5)
        self.assertEqual(self.state.virtual_entry_price, 100.0)
        self.assertEqual(self.state.virtual_cash, 263949.07)
        self.assertEqual(self.ledger.active_record, record)

    def test_close_transaction_realizes_accounting_and_ledger(self):
        bar = SimpleNamespace(open=100.0, datetime="dt")
        self.engine.open(1, 5, 10, bar)
        result = self.engine.close("TAKE_PROFIT", 101.98, 11, 2)
        self.assertEqual(result["trade_id"], 1)
        self.assertEqual(result["gross_pnl"], 9900.0)
        self.assertEqual(result["net_pnl"], 9798.14)
        self.assertEqual(self.state.virtual_cash, 273798.14)
        self.assertEqual(self.ledger.closed_trades, 1)
        self.assertIsNone(self.ledger.active_record)

    def test_reset_position_only_clears_current_state(self):
        self.state.virtual_position_size = 5
        self.state.virtual_entry_price = 100.0
        self.state.virtual_entry_commission = 50.0
        self.state.tp_level = 102.0
        self.state.sl_level = 98.0
        self.state.current_trail_step = 2
        self.engine.reset_position()
        self.assertEqual(self.state.virtual_position_size, 0)
        self.assertIsNone(self.state.virtual_entry_price)
        self.assertEqual(self.state.virtual_entry_commission, 0.0)
        self.assertIsNone(self.state.tp_level)
        self.assertIsNone(self.state.sl_level)
        self.assertEqual(self.state.current_trail_step, -1)
        self.assertEqual(self.ledger.records, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
