"""Подготовка исторических данных для одиночного бэктеста.

Модуль отвечает только за чтение и нормализацию входного CSV.
Исходный CSV не изменяется, строки не сортируются и не удаляются.
"""

from __future__ import annotations

import pandas as pd


def _find_column(columns, *tokens):
    """Find the first input column containing one of the supplied tokens."""
    for column in columns:
        if any(token in column for token in tokens):
            return column
    return None


def load_and_prepare_backtest_dataframe(csv_path: str) -> pd.DataFrame:
    """Read and normalize the source CSV without changing its row order."""
    raw_df = pd.read_csv(csv_path, sep=";", dtype=str)
    raw_df.columns = [str(c).upper() for c in raw_df.columns]

    open_col = _find_column(raw_df.columns, "OPEN", "ОТКР")
    high_col = _find_column(raw_df.columns, "HIGH", "МАКС")
    low_col = _find_column(raw_df.columns, "LOW", "МИН")
    close_col = _find_column(raw_df.columns, "CLOSE", "ЗАКР")
    vol_col = _find_column(raw_df.columns, "VOL", "ОБЪЕМ")
    date_col = _find_column(raw_df.columns, "DATE", "ДАТА")
    time_col = _find_column(raw_df.columns, "TIME", "ВРЕМЯ")

    if not all(
        [open_col, high_col, low_col, close_col, vol_col, date_col, time_col]
    ):
        raise ValueError(
            "CSV structure error: unable to identify all date/time/OHLCV columns"
        )

    raw_df["DATETIME"] = pd.to_datetime(
        raw_df[date_col].astype(str).str.strip()
        + " "
        + raw_df[time_col].astype(str).str.strip(),
        errors="coerce",
        dayfirst=False,
    )

    return pd.DataFrame(
        {
            "DATETIME": raw_df["DATETIME"],
            "OPEN": pd.to_numeric(raw_df[open_col], errors="coerce"),
            "HIGH": pd.to_numeric(raw_df[high_col], errors="coerce"),
            "LOW": pd.to_numeric(raw_df[low_col], errors="coerce"),
            "CLOSE": pd.to_numeric(raw_df[close_col], errors="coerce"),
            "VOLUME": pd.to_numeric(raw_df[vol_col], errors="coerce"),
        }
    )


def export_backtrader_adapter(prepared: pd.DataFrame, processed_path: str) -> None:
    """Write the temporary Backtrader adapter without changing source data."""
    export_df = pd.DataFrame(
        {
            "DateTime": prepared["DATETIME"].dt.strftime("%Y%m%d %H%M%S"),
            "Open": prepared["OPEN"],
            "High": prepared["HIGH"],
            "Low": prepared["LOW"],
            "Close": prepared["CLOSE"],
            "Volume": prepared["VOLUME"].round().astype(int),
        }
    )
    export_df.to_csv(processed_path, index=False)
