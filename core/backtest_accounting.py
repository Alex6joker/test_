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


class BacktestAccountingMixin:
    """Accounting and portfolio-state operations for the virtual backtest."""

    def _ensure_accounting_engine(self) -> AccountingEngine:
        if not hasattr(self, "_accounting_engine"):
            self._accounting_engine = AccountingEngine(
                self.state,
                self.params,
                self._money,
                self.params.real_commission_per_side,
            )
        return self._accounting_engine

    def _unrealized_pnl(self, mark_price: float | None = None) -> float:
        if mark_price is None:
            mark_price = self.market.current_bar.close
        return self._ensure_accounting_engine().unrealized_pnl(mark_price)

    def _log_virtual_portfolio(self, bar_index: int):
        unrealized = self._unrealized_pnl()
        equity = self._money(self.state.virtual_cash + unrealized)
        if self.logger.wants_debug_event("PORTFOLIO_STATE"):
            self.logger.debug_event(
                "PORTFOLIO_STATE",
                bar_index=bar_index,
                datetime=self.market.current_bar.datetime,
                cash=self.state.virtual_cash,
                unrealized_pnl=unrealized,
                portfolio_value=equity,
                position_size=self.state.virtual_position_size,
                position_price=self.state.virtual_entry_price,
                mark_price=self.market.current_bar.close,
                trade_id=self.state.trade_id,
            )

    def _check_accounting(self):
        closed_net = self._money(
            sum(float(r["net_pnl"]) for r in self.state.closed_trade_records)
        )
        open_entry_commission = (
            self.state.virtual_entry_commission if self.state.virtual_position_size else 0.0
        )
        expected_equity = self._money(
            self.params.initial_cash
            + closed_net
            - open_entry_commission
            + self._unrealized_pnl()
        )
        actual_equity = self._money(
            self.state.virtual_cash + self._unrealized_pnl()
        )
        difference = self._money(actual_equity - expected_equity)

        if self.logger.wants_event("ACCOUNTING_CHECK"):
            self.logger.event(
                "ACCOUNTING_CHECK",
                initial_cash=self._money(self.params.initial_cash),
                closed_net_pnl=closed_net,
                open_entry_commission=open_entry_commission,
                unrealized_pnl=self._unrealized_pnl(),
                expected_equity=expected_equity,
                actual_equity=actual_equity,
                difference=difference,
                passed=(difference == 0.0),
            )

        if difference != 0.0:
            raise RuntimeError(
                f"Accounting self-check failed: difference = {difference}"
            )

    def _check_trade_lifecycle(self):
        errors = self._ensure_trade_ledger().check()
        if self.logger.wants_event("TRADE_LIFECYCLE_CHECK"):
            self.logger.event(
                "TRADE_LIFECYCLE_CHECK",
                trade_count=len(self.state.trade_records),
                closed_trade_count=self.state.closed_trades,
                open_position_size=self.state.virtual_position_size,
                errors=errors,
                passed=not errors,
            )
        if errors:
            raise RuntimeError(
                "Trade lifecycle self-check failed: " + "; ".join(errors)
            )

    def _check_negative_cash(self):
        passed = self.state.virtual_cash >= 0.0
        if self.logger.wants_event("NEGATIVE_CASH_CHECK"):
            self.logger.event(
                "NEGATIVE_CASH_CHECK",
                virtual_cash=self.state.virtual_cash,
                passed=passed,
            )
        if not passed:
            raise RuntimeError(
                f"Virtual cash became negative: {self.state.virtual_cash}"
            )

    def stop(self):
        final_bar = self.market.current_bar
        final_close = final_bar.close if final_bar is not None else None
        unrealized = self._unrealized_pnl(final_close)

        self.state.virtual_cash = self._money(self.state.virtual_cash)
        self.state.final_virtual_equity = self._money(
            self.state.virtual_cash + unrealized
        )

        self._check_negative_cash()
        self._check_trade_lifecycle()
        self._check_accounting()

        closed_net = self._money(
            sum(float(r["net_pnl"]) for r in self.state.closed_trade_records)
        )

        if self.logger.wants_event("BACKTEST_SELF_CHECK"):
            self.logger.event(
                "BACKTEST_SELF_CHECK",
                final_equity=self.state.final_virtual_equity,
                initial_cash=self._money(self.params.initial_cash),
                closed_net_pnl=closed_net,
                total_commission=self.state.total_commission,
                closed_trades=self.state.closed_trades,
                total_contracts=self.state.total_contracts,
                open_position_size=self.state.virtual_position_size,
                passed=True,
            )

        if self.logger.wants_event("BACKTEST_STOP"):
            self.logger.event(
                "BACKTEST_STOP",
                bar_index=self.market.bar_index,
                datetime=self.market.current_bar.datetime if self.market.current_bar else None,
                position_size=self.state.virtual_position_size,
                entry_price=self.state.virtual_entry_price,
                tp_level=self.state.tp_level,
                sl_level=self.state.sl_level,
                trade_id=self.state.trade_id,
                virtual_cash=self.state.virtual_cash,
                unrealized_pnl=unrealized,
                final_virtual_equity=self.state.final_virtual_equity,
                total_commission=self.state.total_commission,
            )

        if self.state.virtual_position_size:
            if self.logger.wants_warning_or_error():
                self.logger.warning(
                    f"OPEN_POSITION_AT_END trade_id = {self.state.trade_id}; "
                    f"position_size = {self.state.virtual_position_size}; "
                    f"entry_price = {self.state.virtual_entry_price}; "
                    f"mark_price = {final_close}; "
                    f"unrealized_pnl = {unrealized}"
                )
