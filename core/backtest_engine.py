"""Legacy import compatibility for the historical Backtrader strategy.

The Backtrader-specific implementation now lives in ``backtest_adapter``.
Framework-independent production code must use ``backtest_loop`` instead.
"""
from __future__ import annotations

from .backtest_adapter import ContractVolumeAnalyzer, RealisticFuturesStrategy

__all__ = ["RealisticFuturesStrategy", "ContractVolumeAnalyzer"]
