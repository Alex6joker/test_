from __future__ import annotations

from collections import deque

from .backtest_bar import Bar


class Market:
    """Observable sequence of available market bars.

    Market deliberately does not sort, fill, interpolate, validate, or otherwise
    transform historical data. It only exposes the current and previous
    available observations.
    """

    def __init__(self) -> None:
        self._bars: deque[Bar] = deque(maxlen=2)
        self._bar_index = 0

    def observe(self, bar: Bar) -> None:
        if not isinstance(bar, Bar):
            raise TypeError("Market.observe() requires a Bar instance")

        self._bars.append(bar)
        self._bar_index += 1

    @property
    def current_bar(self) -> Bar | None:
        if not self._bars:
            return None
        return self._bars[-1]

    @property
    def previous_bar(self) -> Bar | None:
        if len(self._bars) < 2:
            return None
        return self._bars[-2]

    @property
    def bar_index(self) -> int:
        return self._bar_index

    @property
    def has_current_bar(self) -> bool:
        return self.current_bar is not None

    @property
    def has_previous_bar(self) -> bool:
        return self.previous_bar is not None
