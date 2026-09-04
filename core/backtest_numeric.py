from __future__ import annotations


class BacktestNumericMixin:
    """Numeric normalization helpers shared by all backtest functional blocks."""

    def _money(self, value: float) -> float:
        return round(float(value), self.params.precision_money)

    def _price(self, value: float) -> float:
        return round(float(value), self.params.precision_num)
