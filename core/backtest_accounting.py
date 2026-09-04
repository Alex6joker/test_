from __future__ import annotations


class BacktestAccountingMixin:
    """Accounting and portfolio-state operations for the virtual backtest engine."""

    def _unrealized_pnl(self, mark_price: float | None = None) -> float:
        if not self.virtual_position_size or self.virtual_entry_price is None:
            return 0.0
        if mark_price is None:
            mark_price = float(self.data.close[0])

        size = abs(self.virtual_position_size)
        if self.virtual_position_size > 0:
            pnl = (
                float(mark_price) - self.virtual_entry_price
            ) * self.params.real_mult * size
        else:
            pnl = (
                self.virtual_entry_price - float(mark_price)
            ) * self.params.real_mult * size
        return self._money(pnl)

    def _log_virtual_portfolio(self, bar_index: int):
        unrealized = self._unrealized_pnl()
        equity = self._money(self.virtual_cash + unrealized)
        self.logger.debug_event(
            "PORTFOLIO_STATE",
            bar_index=bar_index,
            datetime=self.data.datetime.datetime(0),
            cash=self.virtual_cash,
            unrealized_pnl=unrealized,
            portfolio_value=equity,
            position_size=self.virtual_position_size,
            position_price=self.virtual_entry_price,
            mark_price=float(self.data.close[0]),
            trade_id=self.trade_id,
        )

    def _check_accounting(self):
        closed_net = self._money(
            sum(float(r["net_pnl"]) for r in self._closed_trade_records)
        )
        open_entry_commission = (
            self.virtual_entry_commission if self.virtual_position_size else 0.0
        )
        expected_equity = self._money(
            self.params.initial_cash
            + closed_net
            - open_entry_commission
            + self._unrealized_pnl()
        )
        actual_equity = self._money(
            self.virtual_cash + self._unrealized_pnl()
        )
        difference = self._money(actual_equity - expected_equity)

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
        errors = []
        if self.trade_id != len(self._trade_records):
            errors.append(
                f"trade_id={self.trade_id} records={len(self._trade_records)}"
            )

        for expected_id, record in enumerate(self._trade_records, start=1):
            if record["trade_id"] != expected_id:
                errors.append(
                    f"non-sequential trade_id={record['trade_id']} expected={expected_id}"
                )
            closed = record["exit_bar"] is not None
            if closed:
                if record["exit_price"] is None or record["net_pnl"] is None:
                    errors.append(
                        f"trade_id={record['trade_id']} marked closed without exit data"
                    )
            else:
                if record["exit_price"] is not None or record["net_pnl"] is not None:
                    errors.append(
                        f"trade_id={record['trade_id']} has partial exit data"
                    )

        closed_count = sum(
            1 for record in self._trade_records if record["exit_bar"] is not None
        )
        if closed_count != self.closed_trades:
            errors.append(
                f"closed_trades={self.closed_trades} records={closed_count}"
            )

        if self.virtual_position_size:
            open_records = [
                r for r in self._trade_records if r["exit_bar"] is None
            ]
            if len(open_records) != 1:
                errors.append(
                    f"open_position={self.virtual_position_size} open_records={len(open_records)}"
                )
            elif open_records[0]["trade_id"] != self.trade_id:
                errors.append("open trade is not the latest trade")
        else:
            if any(r["exit_bar"] is None for r in self._trade_records):
                errors.append("open trade record exists while position is flat")

        passed = not errors
        self.logger.event(
            "TRADE_LIFECYCLE_CHECK",
            trade_count=len(self._trade_records),
            closed_trade_count=self.closed_trades,
            open_position_size=self.virtual_position_size,
            errors=errors,
            passed=passed,
        )

        if errors:
            raise RuntimeError(
                "Trade lifecycle self-check failed: " + "; ".join(errors)
            )

    def _check_negative_cash(self):
        passed = self.virtual_cash >= 0.0
        self.logger.event(
            "NEGATIVE_CASH_CHECK",
            virtual_cash=self.virtual_cash,
            passed=passed,
        )
        if not passed:
            raise RuntimeError(
                f"Virtual cash became negative: {self.virtual_cash}"
            )

    def stop(self):
        final_close = float(self.data.close[0]) if len(self.data) else None
        unrealized = self._unrealized_pnl(final_close)

        self.virtual_cash = self._money(self.virtual_cash)
        self.final_virtual_equity = self._money(
            self.virtual_cash + unrealized
        )

        # These checks are deliberately performed before the final result.
        self._check_negative_cash()
        self._check_trade_lifecycle()
        self._check_accounting()

        closed_net = self._money(
            sum(float(r["net_pnl"]) for r in self._closed_trade_records)
        )

        self.logger.event(
            "BACKTEST_SELF_CHECK",
            final_equity=self.final_virtual_equity,
            initial_cash=self._money(self.params.initial_cash),
            closed_net_pnl=closed_net,
            total_commission=self.total_commission,
            closed_trades=self.closed_trades,
            total_contracts=self.total_contracts,
            open_position_size=self.virtual_position_size,
            passed=True,
        )

        self.logger.event(
            "BACKTEST_STOP",
            bar_index=len(self.data),
            datetime=self.data.datetime.datetime(0) if len(self.data) else None,
            position_size=self.virtual_position_size,
            entry_price=self.virtual_entry_price,
            tp_level=self.tp_level,
            sl_level=self.sl_level,
            trade_id=self.trade_id,
            virtual_cash=self.virtual_cash,
            unrealized_pnl=unrealized,
            final_virtual_equity=self.final_virtual_equity,
            total_commission=self.total_commission,
        )

        if self.virtual_position_size:
            self.logger.warning(
                f"OPEN_POSITION_AT_END trade_id = {self.trade_id}; "
                f"position_size = {self.virtual_position_size}; "
                f"entry_price = {self.virtual_entry_price}; "
                f"mark_price = {final_close}; "
                f"unrealized_pnl = {unrealized}"
            )
