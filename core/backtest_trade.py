from __future__ import annotations


class BacktestTradeMixin:
    """Virtual trade lifecycle operations: entry and exit."""

    def _open_virtual_position(self, signal: int, size: int, bar_index: int):
        entry_price = self._price(self.data.open[0])
        commission = self._money(self._get_commission_per_side() * size)

        self.state.trade_id += 1
        direction = "LONG" if signal > 0 else "SHORT"
        signed_size = size if signal > 0 else -size

        self.state.virtual_position_size = signed_size
        self.state.virtual_entry_price = entry_price
        self.state.entry_price = entry_price
        self.state.virtual_entry_commission = commission
        self.state.virtual_exit_commission = 0.0
        self.state.virtual_gross_pnl = 0.0

        self.state.virtual_cash = self._money(self.state.virtual_cash - commission)
        self.state.total_commission = self._money(self.state.total_commission + commission)
        self.state.last_trade_bar = bar_index
        self.state.current_trail_step = -1

        tp_distance = self._price(self.params.tp)
        sl_distance = self._price(self.params.sl)

        if signal > 0:
            self.state.tp_level = self._price(entry_price + tp_distance)
            self.state.sl_level = self._price(entry_price - sl_distance)
        else:
            self.state.tp_level = self._price(entry_price - tp_distance)
            self.state.sl_level = self._price(entry_price + sl_distance)

        record = {
            "trade_id": self.state.trade_id,
            "direction": direction,
            "size": size,
            "entry_bar": bar_index,
            "entry_datetime": self.data.datetime.datetime(0),
            "entry_price": entry_price,
            "entry_commission": commission,
            "exit_bar": None,
            "exit_phase": None,
            "exit_price": None,
            "exit_reason": None,
            "exit_commission": 0.0,
            "gross_pnl": 0.0,
            "net_pnl": None,
        }
        self.state.trade_records.append(record)

        if self.logger.wants_trade():
            self.logger.trade(
                f"ENTRY_SIGNAL trade_id = {self.state.trade_id}; "
                f"bar_index = {bar_index}; signal = {signal}; dynamic_size = {size}; "
                f"execution_model = VIRTUAL_OPEN; entry_price = {entry_price}; "
                f"entry_slippage = 0.0; commission = {commission}"
            )
        if self.logger.wants_debug_event("ORDER_SUBMITTED"):
            self.logger.debug_event(
                "ORDER_SUBMITTED",
                trade_id=self.state.trade_id,
                bar_index=bar_index,
                signal=signal,
                order_ref=None,
                order_type="VIRTUAL_OPEN",
                requested_size=size,
                reference_open=float(self.data.open[0]),
                execution_price=entry_price,
                dynamic_slip=0.0,
                execution_model="VIRTUAL",
            )
        if self.logger.wants_trade():
            self.logger.trade(
                f"ENTRY_EXECUTED trade_id = {self.state.trade_id}; direction = {direction}; "
                f"execution_model = VIRTUAL_OPEN; execution_price = {entry_price}; "
                f"executed_size = {size}; entry_commission = {commission}; "
                f"tp_level = {self.state.tp_level}; sl_level = {self.state.sl_level}"
            )

    def _close_virtual_position(
        self,
        reason: str,
        target_exec_price: float,
        detected_price: float,
        bar_index: int,
        phase_index: int,
    ):
        if not self.state.virtual_position_size or self.state.virtual_entry_price is None:
            raise RuntimeError("Attempted to close a virtual position that is not open")

        size = abs(self.state.virtual_position_size)
        direction = "LONG" if self.state.virtual_position_size > 0 else "SHORT"
        exit_price = self._price(target_exec_price)

        if self.state.virtual_position_size > 0:
            gross_pnl = (
                exit_price - self.state.virtual_entry_price
            ) * self.params.real_mult * size
        else:
            gross_pnl = (
                self.state.virtual_entry_price - exit_price
            ) * self.params.real_mult * size
        gross_pnl = self._money(gross_pnl)

        exit_commission = self._money(
            self._get_commission_per_side() * size
        )
        net_trade_pnl = self._money(
            gross_pnl - self.state.virtual_entry_commission - exit_commission
        )

        self.state.virtual_cash = self._money(
            self.state.virtual_cash + gross_pnl - exit_commission
        )
        self.state.virtual_gross_pnl = gross_pnl
        self.state.virtual_exit_commission = exit_commission
        self.state.total_commission = self._money(
            self.state.total_commission + exit_commission
        )

        record = self.state.trade_records[-1]
        if record["trade_id"] != self.state.trade_id:
            raise RuntimeError("Trade lifecycle order is corrupted")
        record.update(
            {
                "exit_bar": bar_index,
                "exit_phase": phase_index,
                "exit_price": exit_price,
                "exit_reason": reason,
                "exit_commission": exit_commission,
                "gross_pnl": gross_pnl,
                "net_pnl": net_trade_pnl,
            }
        )
        self.state.closed_trade_records.append(record)

        if self.logger.wants_trade():
            self.logger.trade(
                f"EXIT_SIGNAL trade_id = {self.state.trade_id}; reason = {reason}; "
                f"bar_index = {bar_index}; phase_index = {phase_index}; "
                f"detected_price = {detected_price}; "
                f"level = {self.state.sl_level if reason == 'STOP_LOSS' else self.state.tp_level}; "
                f"execution_model = VIRTUAL_INTRABAR; target_exec_price = {exit_price}"
            )
        if self.logger.wants_trade():
            self.logger.trade(
                f"EXIT_EXECUTED trade_id = {self.state.trade_id}; reason = {reason}; "
                f"execution_model = VIRTUAL_INTRABAR; broker_executed_price = None; "
                f"execution_price = {exit_price}; target_exec_price = {exit_price}; "
                f"exit_slippage = {self.get_backtest_dynamic_slippage(size)}; "
                f"executed_size = {size}; exit_commission = {exit_commission}"
            )
        if self.logger.wants_trade():
            self.logger.trade(
                f"TRADE_CLOSED trade_id = {self.state.trade_id}; direction = {direction}; "
                f"size = {size}; entry_price = {self.state.virtual_entry_price}; "
                f"exit_price = {exit_price}; gross_pnl = {gross_pnl}; "
                f"entry_commission = {self.state.virtual_entry_commission}; "
                f"exit_commission = {exit_commission}; net_pnl = {net_trade_pnl}; "
                f"reason = {reason}; execution_model = VIRTUAL"
            )
        if self.logger.wants_debug_event("TRADE_UPDATE"):
            self.logger.debug_event(
                "TRADE_UPDATE",
                trade_id=self.state.trade_id,
                status="CLOSED",
                direction=direction,
                size=size,
                entry_price=self.state.virtual_entry_price,
                exit_price=exit_price,
                commission=self._money(
                    self.state.virtual_entry_commission + exit_commission
                ),
                pnl=gross_pnl,
                pnl_comm=net_trade_pnl,
                bar_index=bar_index,
                phase_index=phase_index,
                datetime=self.data.datetime.datetime(0),
            )

        self.state.virtual_position_size = 0
        self.state.virtual_entry_price = None
        self.state.entry_price = None
        self.state.tp_level = None
        self.state.sl_level = None
        self.state.virtual_entry_commission = 0.0
        self.state.virtual_exit_commission = 0.0
        self.state.current_trail_step = -1

        self.state.closed_trades += 1
        self.state.total_contracts += size * 2
