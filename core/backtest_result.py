from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestResult:
    """Source-of-truth values collected from the virtual strategy."""

    final_portfolio_value: float
    real_net_profit: float
    total_closed_trades: int
    total_contracts: int
    total_commission: float
    open_position_size: int
    virtual_cash: float
