"""Backtrader-to-virtual-market adapter.

This module is the temporary boundary between Backtrader's data feed and the
Backtest Bar/Market model.  It performs representation conversion only; it
must not contain strategy, execution, accounting, or signal logic.
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
