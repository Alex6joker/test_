from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from backtest_audit import AuditData, Crossing, Entry, Phase, audit


class BacktestAuditGapTests(unittest.TestCase):
    def _run(self, *, direction, reason, open_price, level):
        data = AuditData()
        data.params = {"precision_num": 2, "tp": 1.90}
        data.entries.append(
            Entry(
                trade=1,
                direction=direction,
                price=100.0,
                size=5,
                commission=0.0,
                seq=1,
            )
        )
        data.phases.append(
            Phase(
                trade=1,
                bar=10,
                phase=0,
                start=open_price,
                end=open_price,
                direction="FLAT",
                entry=100.0,
                tp=level if reason == "TAKE_PROFIT" else 101.9,
                sl=level if reason == "STOP_LOSS" else 98.1,
                step=-1,
                seq=2,
            )
        )
        data.crossings.append(
            Crossing(
                trade=1,
                bar=10,
                phase=0,
                reason=reason,
                price=open_price,
                sl=level if reason == "STOP_LOSS" else 98.1,
                tp=level if reason == "TAKE_PROFIT" else 101.9,
                slippage=0.02,
                seq=3,
                phase_obj=data.phases[-1],
            )
        )
        return {r["name"]: r for r in audit(data)}

    def test_long_gap_stop_is_valid_open_execution(self):
        results = self._run(
            direction="LONG",
            reason="STOP_LOSS",
            open_price=97.0,
            level=99.0,
        )
        self.assertFalse(results["EXIT_CROSSING_CAUSALITY"]["errors"])
        self.assertFalse(results["GAP_OPEN_EXECUTION"]["errors"])
        self.assertEqual(results["GAP_OPEN_EXECUTION"]["checked"], 1)

    def test_short_gap_take_profit_is_valid_open_execution(self):
        results = self._run(
            direction="SHORT",
            reason="TAKE_PROFIT",
            open_price=97.0,
            level=98.0,
        )
        self.assertFalse(results["EXIT_CROSSING_CAUSALITY"]["errors"])
        self.assertFalse(results["GAP_OPEN_EXECUTION"]["errors"])

    def test_non_gap_mismatched_crossing_is_rejected(self):
        results = self._run(
            direction="LONG",
            reason="STOP_LOSS",
            open_price=99.5,
            level=99.0,
        )
        self.assertTrue(results["GAP_OPEN_EXECUTION"]["errors"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
