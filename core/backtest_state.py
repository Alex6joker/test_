"""Mutable virtual backtest state.

This object owns the mutable state of the virtual execution model.  Strategy
parameters, logger and Backtrader data remain outside of it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BacktestState:
    trade_id: int = 0
    last_trade_bar: int = -1

    virtual_cash: float = 0.0
    virtual_position_size: int = 0
    virtual_entry_price: float | None = None
    virtual_entry_commission: float = 0.0
    virtual_gross_pnl: float = 0.0
    virtual_exit_commission: float = 0.0

    entry_price: float | None = None
    tp_level: float | None = None
    sl_level: float | None = None
    current_trail_step: int = -1

    closed_trades: int = 0
    total_contracts: int = 0
    total_commission: float = 0.0
    final_virtual_equity: float = 0.0

    trade_records: list[dict[str, Any]] = field(default_factory=list)
    closed_trade_records: list[dict[str, Any]] = field(default_factory=list)

    atr_diagnostic: Any = None
