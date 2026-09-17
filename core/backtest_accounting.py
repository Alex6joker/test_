from __future__ import annotations


class AccountingEngine:
    """Money, P&L and portfolio accounting for the virtual backtest."""

    def __init__(self, state, params, money_fn, commission_per_side: float) -> None:
        self.state = state
        self.params = params
        self._money = money_fn
        self._commission_per_side = float(commission_per_side)

    def entry_commission(self, size: int) -> float:
        return self._money(self._commission_per_side * size)

    def exit_commission(self, size: int) -> float:
        return self._money(self._commission_per_side * size)

    def apply_entry(self, commission: float) -> None:
        self.state.virtual_cash = self._money(self.state.virtual_cash - commission)
        self.state.total_commission = self._money(
            self.state.total_commission + commission
        )

    def gross_pnl(self, entry_price: float, exit_price: float, direction: int, size: int) -> float:
        if direction > 0:
            pnl = (exit_price - entry_price) * self.params.real_mult * size
        else:
            pnl = (entry_price - exit_price) * self.params.real_mult * size
        return self._money(pnl)

    def net_trade_pnl(
        self,
        gross_pnl: float,
        entry_commission: float,
        exit_commission: float,
    ) -> float:
        return self._money(gross_pnl - entry_commission - exit_commission)

    def apply_exit(self, gross_pnl: float, exit_commission: float) -> None:
        self.state.virtual_cash = self._money(
            self.state.virtual_cash + gross_pnl - exit_commission
        )
        self.state.total_commission = self._money(
            self.state.total_commission + exit_commission
        )

    def unrealized_pnl(self, mark_price: float) -> float:
        if not self.state.virtual_position_size or self.state.virtual_entry_price is None:
            return 0.0
        size = abs(self.state.virtual_position_size)
        if self.state.virtual_position_size > 0:
            pnl = (
                mark_price - self.state.virtual_entry_price
            ) * self.params.real_mult * size
        else:
            pnl = (
                self.state.virtual_entry_price - mark_price
            ) * self.params.real_mult * size
        return self._money(pnl)

