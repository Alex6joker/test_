from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Bar:
    """Immutable market observation for one available historical candle."""

    datetime: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
