"""Market-data adapters for the native backtest.

The runtime consumes immutable :class:`Bar` objects.  CSV parsing and the
legacy pandas conversion helper live at this boundary; no DataFrame crosses
into the native backtest runtime.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from core.backtest_bar import Bar


def _find_column(columns, *tokens):
    for column in columns:
        if any(token in column for token in tokens):
            return column
    return None


def _parse_datetime(date_value: object, time_value: object) -> datetime:
    date_text = str(date_value).strip()
    time_text = str(time_value).strip()

    # The QUIK export used by the native backtester is fixed-width YYYYMMDD /
    # HHMMSS.  Keep this as the hot path: datetime.strptime() is substantially
    # more expensive when called once per bar.
    if (
        len(date_text) == 8
        and len(time_text) == 6
        and date_text.isdigit()
        and time_text.isdigit()
    ):
        try:
            return datetime(
                int(date_text[0:4]),
                int(date_text[4:6]),
                int(date_text[6:8]),
                int(time_text[0:2]),
                int(time_text[2:4]),
                int(time_text[4:6]),
            )
        except ValueError:
            pass

    text = f"{date_text} {time_text}"
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y%m%d %H%M%S",
        "%Y-%m-%d %H:%M",
        "%Y%m%d %H%M",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    # Keep the public error wording close to the previous pandas-based loader.
    raise ValueError(f"invalid timestamp: {text}")


def _bar_from_values(dt, open_value, high_value, low_value, close_value, volume_value) -> Bar:
    try:
        return Bar(
            datetime=dt,
            open=float(open_value),
            high=float(high_value),
            low=float(low_value),
            close=float(close_value),
            volume=int(round(float(volume_value))),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid OHLCV value: {exc}") from exc


def _column_map(fieldnames):
    columns = [str(c).upper() for c in fieldnames]
    return {
        "date": _find_column(columns, "DATE", "ДАТА"),
        "time": _find_column(columns, "TIME", "ВРЕМЯ"),
        "open": _find_column(columns, "OPEN", "ОТКР"),
        "high": _find_column(columns, "HIGH", "МАКС"),
        "low": _find_column(columns, "LOW", "МИН"),
        "close": _find_column(columns, "CLOSE", "ЗАКР"),
        "volume": _find_column(columns, "VOL", "ОБЪЕМ"),
    }, columns


def load_and_prepare_backtest_bars(csv_path: str) -> list[Bar]:
    """Read source CSV directly into the formal Bar model.

    Rows are preserved exactly in source order.  No sorting, filling,
    deduplication, or rewriting is performed here.
    """
    path = Path(csv_path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        if not reader.fieldnames:
            raise ValueError("CSV structure error: missing header")

        field_map, columns = _column_map(reader.fieldnames)
        if not all(field_map.values()):
            raise ValueError(
                "CSV structure error: unable to identify all date/time/OHLCV columns"
            )

        # DictReader retains original header spelling; resolve the actual names.
        actual = dict(zip(columns, reader.fieldnames))
        bars: list[Bar] = []
        for row in reader:
            dt = _parse_datetime(row[actual[field_map["date"]]], row[actual[field_map["time"]]])
            bars.append(
                _bar_from_values(
                    dt,
                    row[actual[field_map["open"]]],
                    row[actual[field_map["high"]]],
                    row[actual[field_map["low"]]],
                    row[actual[field_map["close"]]],
                    row[actual[field_map["volume"]]],
                )
            )
        return bars


def dataframe_to_bars(df) -> list[Bar]:
    """Compatibility adapter: convert a prepared DataFrame into Bars.

    This function exists only at the external data boundary for tests and
    legacy tooling.  Native runtime APIs never accept a DataFrame.
    """
    required = ("DATETIME", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME")
    missing = [name for name in required if name not in df.columns]
    if missing:
        raise ValueError(f"DataFrame structure error: missing columns = {missing}")

    return [
        _bar_from_values(
            row.DATETIME.to_pydatetime() if hasattr(row.DATETIME, "to_pydatetime") else row.DATETIME,
            row.OPEN, row.HIGH, row.LOW, row.CLOSE, row.VOLUME,
        )
        for row in df.itertuples(index=False)
    ]


def export_backtrader_adapter(bars, processed_path: str) -> None:
    """Write the temporary Backtrader CSV adapter from a Bar sequence."""
    import pandas as pd

    export_df = pd.DataFrame(
        {
            "DateTime": [bar.datetime.strftime("%Y%m%d %H%M%S") for bar in bars],
            "Open": [bar.open for bar in bars],
            "High": [bar.high for bar in bars],
            "Low": [bar.low for bar in bars],
            "Close": [bar.close for bar in bars],
            "Volume": [bar.volume for bar in bars],
        }
    )
    export_df.to_csv(processed_path, index=False)
