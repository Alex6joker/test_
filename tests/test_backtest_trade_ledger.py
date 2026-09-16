from __future__ import annotations

import unittest

from core.backtest_trade_ledger import TradeLedger


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
