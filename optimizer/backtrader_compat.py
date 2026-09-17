from __future__ import annotations

import backtrader as bt


class PreviousBarRange(bt.Indicator):
    """Compatibility indicator; intentionally has no strategy-level role."""

    lines = ("range",)
    params = (("precision", 2),)

    def __init__(self):
        self.addminperiod(2)

    def next(self):
        self.lines.range[0] = round(
            float(self.data.high[-1]) - float(self.data.low[-1]),
            self.p.precision,
        )

class ContractVolumeAnalyzer(bt.Analyzer):
    """Compatibility class only. It is not a source of backtest results."""

    def __init__(self):
        self.total_contracts = 0

    def notify_order(self, order):
        if order.status == order.Completed:
            self.total_contracts += abs(order.executed.size)

    def get_analysis(self):
        return self.total_contracts
