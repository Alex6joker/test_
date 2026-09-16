from __future__ import annotations

import math
from datetime import timedelta

from core.backtest_bar import Bar
from core.backtest_logger import BacktestLogger


def validate_backtest_bars(bars: list[Bar], logger: BacktestLogger) -> None:
    """Validate the formal market-data model without modifying observations."""
    if not isinstance(bars, list):
        raise TypeError("CSV validation failed: bars must be a list")
    if not bars:
        raise ValueError("CSV validation failed: dataset is empty")

    previous = None
    duplicate_rows = []
    gap_examples = []
    gap_count = 0

    for index, bar in enumerate(bars):
        if not isinstance(bar, Bar):
            raise TypeError(f"CSV validation failed: row {index} is not a Bar")

        values = {
            "OPEN": bar.open,
            "HIGH": bar.high,
            "LOW": bar.low,
            "CLOSE": bar.close,
            "VOLUME": bar.volume,
        }
        for name, value in values.items():
            if not math.isfinite(float(value)):
                raise ValueError(f"CSV validation failed: non-finite values in {name}")

        if bar.high < max(bar.open, bar.close):
            raise ValueError("CSV validation failed: HIGH is below OPEN/CLOSE")
        if bar.low > min(bar.open, bar.close):
            raise ValueError("CSV validation failed: LOW is above OPEN/CLOSE")
        if bar.high < bar.low:
            raise ValueError("CSV validation failed: HIGH is below LOW")
        if bar.volume < 0:
            raise ValueError("CSV validation failed: negative VOLUME")

        if previous is not None:
            if bar.datetime < previous.datetime:
                raise ValueError("CSV validation failed: timestamps are not ordered increasingly")
            if bar.datetime == previous.datetime:
                duplicate_rows.append(index)
            gap = bar.datetime - previous.datetime
            if gap > timedelta(minutes=1):
                gap_count += 1
                if len(gap_examples) < 10:
                    gap_examples.append(
                        {
                            "previous_datetime": previous.datetime,
                            "datetime": bar.datetime,
                            "gap_minutes": gap.total_seconds() / 60.0,
                        }
                    )
        previous = bar

    if duplicate_rows:
        raise ValueError(
            f"CSV validation failed: duplicate timestamps at rows = {duplicate_rows[:10]}"
        )

    if logger.wants_event("CSV_VALIDATION"):
        logger.event(
            "CSV_VALIDATION",
            rows=len(bars),
            first_datetime=bars[0].datetime,
            last_datetime=bars[-1].datetime,
            duplicate_timestamps=0,
            timestamp_order="INCREASING",
            gaps_gt_1_minute=gap_count,
            gap_examples=gap_examples,
            data_mutation="NONE",
            previous_available_row_rule=True,
            passed=True,
        )
