from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path

core_pkg = types.ModuleType("core")
core_pkg.__path__ = [os.path.join(os.path.dirname(__file__), "..", "core")]
sys.modules.setdefault("core", core_pkg)

from core.backtest_bar import Bar
from core.backtest_data import dataframe_to_bars, load_and_prepare_backtest_bars
from core.backtest_validation import validate_backtest_bars
import pandas as pd


class LoggerStub:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.events = []

    def wants_event(self, name):
        return self.enabled and name == "CSV_VALIDATION"

    def event(self, name, **fields):
        self.events.append((name, fields))


class BacktestDataValidationTests(unittest.TestCase):
    def test_dataframe_to_bars_creates_exact_bar_sequence(self):
        df = pd.DataFrame(
            {
                "DATETIME": [pd.Timestamp("2026-01-01 10:00:00"), pd.Timestamp("2026-01-01 10:05:00")],
                "OPEN": [100.0, 101.25],
                "HIGH": [102.0, 103.5],
                "LOW": [99.0, 100.5],
                "CLOSE": [101.0, 103.0],
                "VOLUME": [100.0, 250.9],
            }
        )

        bars = dataframe_to_bars(df)

        self.assertEqual(
            bars,
            [
                Bar(datetime(2026, 1, 1, 10, 0), 100.0, 102.0, 99.0, 101.0, 100),
                Bar(datetime(2026, 1, 1, 10, 5), 101.25, 103.5, 100.5, 103.0, 251),
            ],
        )

    def test_load_and_prepare_backtest_bars_uses_csv_source_without_dataframe_api(self):
        csv = (
            "DATE;TIME;OPEN;HIGH;LOW;CLOSE;VOL\n"
            "2026-01-01;10:00:00;100;102;99;101;100\n"
            "2026-01-01;10:05:00;101;103;100;102;250\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bars.csv"
            path.write_text(csv, encoding="utf-8")

            bars = load_and_prepare_backtest_bars(str(path))

        self.assertIsInstance(bars, list)
        self.assertEqual(len(bars), 2)
        self.assertTrue(all(isinstance(bar, Bar) for bar in bars))
        self.assertEqual(bars[0].datetime, datetime(2026, 1, 1, 10, 0))
        self.assertEqual(bars[1].close, 102.0)

    def test_validation_uses_bar_sequence_and_reports_validation_event(self):
        bars = [
            Bar(datetime(2026, 1, 1, 10, 0), 100.0, 102.0, 99.0, 101.0, 100),
            Bar(datetime(2026, 1, 1, 10, 5), 101.0, 103.0, 100.0, 102.0, 250),
        ]
        logger = LoggerStub()

        validate_backtest_bars(bars, logger)

        self.assertEqual(len(logger.events), 1)
        name, fields = logger.events[0]
        self.assertEqual(name, "CSV_VALIDATION")
        self.assertEqual(fields["rows"], 2)
        self.assertEqual(fields["gaps_gt_1_minute"], 1)
        self.assertTrue(fields["passed"])

    def test_validation_preserves_previous_available_row_semantics(self):
        bars = [
            Bar(datetime(2026, 1, 1, 10, 0), 100.0, 102.0, 99.0, 101.0, 100),
            Bar(datetime(2026, 1, 1, 10, 5), 101.0, 103.0, 100.0, 102.0, 250),
        ]
        logger = LoggerStub()

        validate_backtest_bars(bars, logger)

        fields = logger.events[0][1]
        self.assertTrue(fields["previous_available_row_rule"])
        self.assertEqual(fields["gap_examples"][0]["previous_datetime"], datetime(2026, 1, 1, 10, 0))
        self.assertEqual(fields["gap_examples"][0]["datetime"], datetime(2026, 1, 1, 10, 5))

    def test_csv_parser_preserves_source_order_and_converts_volume_to_int(self):
        csv = (
            "<TICKER>;<PER>;<DATE>;<TIME>;<OPEN>;<HIGH>;<LOW>;<CLOSE>;<VOL>\n"
            "BR;1;20260505;085900;113.75;113.75;113.75;113.75;284.4\n"
            "BR;1;20260505;090000;113.77;113.78;113.57;113.75;3866.4\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bars.csv"
            path.write_text(csv, encoding="utf-8")
            bars = load_and_prepare_backtest_bars(str(path))

        self.assertEqual([bar.datetime for bar in bars], [
            datetime(2026, 5, 5, 8, 59),
            datetime(2026, 5, 5, 9, 0),
        ])
        self.assertEqual([bar.volume for bar in bars], [284, 3866])

    def test_csv_parser_rejects_missing_market_columns(self):
        csv = "DATE;TIME;OPEN\n2026-01-01;10:00:00;100\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bars.csv"
            path.write_text(csv, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unable to identify all date/time/OHLCV columns"):
                load_and_prepare_backtest_bars(str(path))

    def test_csv_parser_rejects_invalid_timestamp(self):
        csv = "DATE;TIME;OPEN;HIGH;LOW;CLOSE;VOL\n2026-99-99;10:00:00;100;101;99;100;1\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bars.csv"
            path.write_text(csv, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid timestamp"):
                load_and_prepare_backtest_bars(str(path))

    def test_csv_parser_rejects_invalid_numeric_value(self):
        csv = "DATE;TIME;OPEN;HIGH;LOW;CLOSE;VOL\n2026-01-01;10:00:00;bad;101;99;100;1\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bars.csv"
            path.write_text(csv, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid OHLCV value"):
                load_and_prepare_backtest_bars(str(path))

    def test_validation_rejects_negative_volume(self):
        bars = [Bar(datetime(2026, 1, 1, 10, 0), 100.0, 101.0, 99.0, 100.0, -1)]
        with self.assertRaisesRegex(ValueError, "negative VOLUME"):
            validate_backtest_bars(bars, LoggerStub(False))

    def test_validation_rejects_out_of_order_bars(self):
        bars = [
            Bar(datetime(2026, 1, 1, 10, 1), 100.0, 102.0, 99.0, 101.0, 100),
            Bar(datetime(2026, 1, 1, 10, 0), 101.0, 103.0, 100.0, 102.0, 250),
        ]
        with self.assertRaisesRegex(ValueError, "timestamps are not ordered increasingly"):
            validate_backtest_bars(bars, LoggerStub(False))

    def test_validation_rejects_duplicate_timestamps(self):
        dt = datetime(2026, 1, 1, 10, 0)
        bars = [
            Bar(dt, 100.0, 102.0, 99.0, 101.0, 100),
            Bar(dt, 101.0, 103.0, 100.0, 102.0, 250),
        ]
        with self.assertRaisesRegex(ValueError, "duplicate timestamps"):
            validate_backtest_bars(bars, LoggerStub(False))

    def test_validation_rejects_invalid_ohlcv_model(self):
        bars = [Bar(datetime(2026, 1, 1, 10, 0), 100.0, 98.0, 99.0, 101.0, 100)]
        with self.assertRaisesRegex(ValueError, "HIGH is below OPEN/CLOSE"):
            validate_backtest_bars(bars, LoggerStub(False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
