"""Mutable virtual backtest execution and portfolio state."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BacktestState:
    """Current mutable state of the virtual execution model.

    Trade history/lifecycle belongs to ``TradeLedger``.  Final results belong
    to ``BacktestResult``.  Derived diagnostic/cache data stays outside this
    object.
    """

    last_trade_bar: int = -1

    virtual_cash: float = 0.0
    virtual_position_size: int = 0
    virtual_entry_price: float | None = None
    virtual_entry_commission: float = 0.0

    tp_level: float | None = None
    sl_level: float | None = None
    current_trail_step: int = -1

    total_commission: float = 0.0
