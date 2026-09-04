from __future__ import annotations

import math

import pandas as pd

from core.backtest_logger import BacktestLogger


def validate_backtest_dataframe(df: pd.DataFrame, logger: BacktestLogger) -> None:
    """Validate input without sorting, filling, dropping, or rewriting rows."""
    required = {"DATETIME", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"CSV validation failed: missing columns = {sorted(missing)}")

    if df.empty:
        raise ValueError("CSV validation failed: dataset is empty")

    dt = pd.to_datetime(df["DATETIME"], errors="coerce")
    if dt.isna().any():
        bad_rows = (dt.isna()).to_numpy().nonzero()[0][:10].tolist()
        raise ValueError(
            f"CSV validation failed: invalid timestamps at rows = {bad_rows}"
        )

    if not dt.is_monotonic_increasing:
        raise ValueError("CSV validation failed: timestamps are not ordered increasingly")

    if dt.duplicated().any():
        duplicate_rows = dt[dt.duplicated()].index[:10].tolist()
        raise ValueError(
            f"CSV validation failed: duplicate timestamps at rows = {duplicate_rows}"
        )

    numeric_columns = ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]
    for column in numeric_columns:
        values = pd.to_numeric(df[column], errors="coerce")
        if values.isna().any():
            bad_rows = values.isna().to_numpy().nonzero()[0][:10].tolist()
            raise ValueError(
                f"CSV validation failed: invalid {column} at rows = {bad_rows}"
            )
        if not values.map(math.isfinite).all():
            raise ValueError(f"CSV validation failed: non-finite values in {column}")

    opens = pd.to_numeric(df["OPEN"], errors="coerce")
    highs = pd.to_numeric(df["HIGH"], errors="coerce")
    lows = pd.to_numeric(df["LOW"], errors="coerce")
    closes = pd.to_numeric(df["CLOSE"], errors="coerce")
    volumes = pd.to_numeric(df["VOLUME"], errors="coerce")

    if (highs < pd.concat([opens, closes], axis=1).max(axis=1)).any():
        raise ValueError("CSV validation failed: HIGH is below OPEN/CLOSE")
    if (lows > pd.concat([opens, closes], axis=1).min(axis=1)).any():
        raise ValueError("CSV validation failed: LOW is above OPEN/CLOSE")
    if (highs < lows).any():
        raise ValueError("CSV validation failed: HIGH is below LOW")
    if (volumes < 0).any():
        raise ValueError("CSV validation failed: negative VOLUME")

    gap_mask = dt.diff() > pd.Timedelta(minutes=1)
    gap_count = int(gap_mask.sum())
    gap_examples = [
        {
            "previous_datetime": dt.iloc[i - 1],
            "datetime": dt.iloc[i],
            "gap_minutes": (dt.iloc[i] - dt.iloc[i - 1]).total_seconds() / 60.0,
        }
        for i in range(1, len(dt))
        if gap_mask.iloc[i]
    ][:10]

    logger.event(
        "CSV_VALIDATION",
        rows=len(df),
        first_datetime=dt.iloc[0],
        last_datetime=dt.iloc[-1],
        duplicate_timestamps=0,
        timestamp_order="INCREASING",
        gaps_gt_1_minute=gap_count,
        gap_examples=gap_examples,
        data_mutation="NONE",
        previous_available_row_rule=True,
        passed=True,
    )
