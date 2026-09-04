from __future__ import annotations


class BacktestCommissionMixin:
    """Commission access used by the virtual execution model."""

    def _get_commission_per_side(self) -> float:
        comminfo = self.broker.getcommissioninfo(self.data)
        return float(comminfo.p.commission)
