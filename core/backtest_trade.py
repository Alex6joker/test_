from __future__ import annotations

from .backtest_trade_ledger import TradeLedger


class BacktestTradeMixin:
    """Compatibility/orchestration layer for virtual trade lifecycle."""

    def _ensure_trade_ledger(self) -> TradeLedger:
        if not hasattr(self, "_trade_ledger"):
            self._trade_ledger = TradeLedger(self.state)
        return self._trade_ledger

    def _open_virtual_position(self, signal: int, size: int, bar_index: int):
        entry_price = self._price(self.data.open[0])
        accounting = self._ensure_accounting_engine()
        commission = accounting.entry_commission(size)
        ledger = self._ensure_trade_ledger()

        direction = "LONG" if signal > 0 else "SHORT"
        signed_size = size if signal > 0 else -size

        self.state.virtual_position_size = signed_size
        self.state.virtual_entry_price = entry_price
        self.state.entry_price = entry_price
        self.state.virtual_entry_commission = commission
        self.state.virtual_exit_commission = 0.0
        self.state.virtual_gross_pnl = 0.0

        accounting.apply_entry(commission)
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

        ledger.open_trade(
            direction=direction,
            size=size,
            bar_index=bar_index,
            entry_datetime=self.data.datetime.datetime(0),
            entry_price=entry_price,
            entry_commission=commission,
        )

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
        direction_sign = 1 if self.state.virtual_position_size > 0 else -1
        direction = "LONG" if direction_sign > 0 else "SHORT"
        exit_price = self._price(target_exec_price)
        accounting = self._ensure_accounting_engine()

        gross_pnl = accounting.gross_pnl(
            self.state.virtual_entry_price,
            exit_price,
            direction_sign,
            size,
        )
        exit_commission = accounting.exit_commission(size)
        net_trade_pnl = accounting.net_trade_pnl(
            gross_pnl,
            self.state.virtual_entry_commission,
            exit_commission,
        )

        accounting.apply_exit(gross_pnl, exit_commission)
        self.state.virtual_gross_pnl = gross_pnl
        self.state.virtual_exit_commission = exit_commission

        self._ensure_trade_ledger().close_trade(
            bar_index=bar_index,
            phase_index=phase_index,
            exit_price=exit_price,
            reason=reason,
            exit_commission=exit_commission,
            gross_pnl=gross_pnl,
            net_pnl=net_trade_pnl,
        )

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
                f"exit_slippage = {self._execution_engine.get_backtest_dynamic_slippage(size)}; "
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
