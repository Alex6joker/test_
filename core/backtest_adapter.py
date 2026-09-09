"""Backtrader boundary for the virtual backtester.

Only this module owns conversion between Backtrader data/feed objects and the
virtual Bar model.  Virtual-market code must consume Bar/Market objects and
must not read Backtrader data lines directly.
"""
from __future__ import annotations

from typing import Any

from .backtest_bar import Bar


class BacktraderBarAdapter:
    """Convert one Backtrader data observation into an immutable Bar."""

    @staticmethod
    def to_bar(data: Any, index: int = 0) -> Bar:
        """Return the OHLCV observation at the requested Backtrader index."""
        return Bar(
            datetime=data.datetime.datetime(index),
            open=float(data.open[index]),
            high=float(data.high[index]),
            low=float(data.low[index]),
            close=float(data.close[index]),
            volume=int(data.volume[index]),
        )


class BacktraderFeedAdapter:
    """Build the Backtrader feed used by the backtest runner."""

    @staticmethod
    def create_feed(processed_path: str) -> Any:
        """Create the Backtrader CSV feed without altering source data."""
        import backtrader as bt

        return bt.feeds.GenericCSVData(
            dataname=processed_path,
            sep=",",
            dtformat="%Y%m%d %H%M%S",
            timeframe=bt.TimeFrame.Minutes,
            datetime=0,
            time=-1,
            open=1,
            high=2,
            low=3,
            close=4,
            volume=5,
            openinterest=-1,
            headers=True,
        )
