from __future__ import annotations

from .backtest_accounting import AccountingEngine
from .backtest_trade_ledger import TradeLedger


class TradeAccountingEngine:
    """Coordinates AccountingEngine, TradeLedger and current BacktestState.

    This is the narrow transaction boundary for virtual trade lifecycle. It
    performs state/accounting/ledger mutations in one place; logging remains
    in the compatibility/orchestration facade so diagnostic output is not a
    responsibility of the accounting layer.
    """

    def __init__(self, runtime) -> None:
        self.state = runtime.state
        self.params = runtime.params
        self._price = runtime._price
        self.accounting = runtime._ensure_accounting_engine()
        self.ledger = runtime._ensure_trade_ledger()

    def open(
        self,
        signal: int,
        size: int,
        bar_index: int,
        current_bar,
    ) -> dict:
        entry_price = self._price(current_bar.open)
        commission = self.accounting.entry_commission(size)
        direction = "LONG" if signal > 0 else "SHORT"
        signed_size = size if signal > 0 else -size

        record = self.ledger.open_trade(
            direction=direction,
            size=size,
            bar_index=bar_index,
            entry_datetime=current_bar.datetime,
            entry_price=entry_price,
            entry_commission=commission,
        )

        state = self.state
        state.virtual_position_size = signed_size
        state.virtual_entry_price = entry_price
        state.virtual_entry_commission = commission
        self.accounting.apply_entry(commission)
        state.last_trade_bar = bar_index
        state.current_trail_step = -1

        tp_distance = self._price(self.params.tp)
        sl_distance = self._price(self.params.sl)
        if signal > 0:
            state.tp_level = self._price(entry_price + tp_distance)
            state.sl_level = self._price(entry_price - sl_distance)
        else:
            state.tp_level = self._price(entry_price - tp_distance)
            state.sl_level = self._price(entry_price + sl_distance)

        return record

    def close(
        self,
        reason: str,
        target_exec_price: float,
        bar_index: int,
        phase_index: int,
    ) -> dict:
        state = self.state
        position_size = state.virtual_position_size
        entry_price = state.virtual_entry_price
        if not position_size or entry_price is None:
            raise RuntimeError("Attempted to close a virtual position that is not open")

        size = abs(position_size)
        direction_sign = 1 if position_size > 0 else -1
        direction = "LONG" if direction_sign > 0 else "SHORT"
        exit_price = self._price(target_exec_price)
        entry_commission = state.virtual_entry_commission
        trade_id = self.ledger.trade_id

        gross_pnl = self.accounting.gross_pnl(
            entry_price, exit_price, direction_sign, size
        )
        exit_commission = self.accounting.exit_commission(size)
        net_trade_pnl = self.accounting.net_trade_pnl(
            gross_pnl, entry_commission, exit_commission
        )
        self.accounting.apply_exit(gross_pnl, exit_commission)

        record = self.ledger.close_trade(
            bar_index=bar_index,
            phase_index=phase_index,
            exit_price=exit_price,
            reason=reason,
            exit_commission=exit_commission,
            gross_pnl=gross_pnl,
            net_pnl=net_trade_pnl,
        )

        # Keep the values required by the existing diagnostic/trade log
        # contract together with the completed record. These are not persisted
        # as new ledger fields; they are only transient transaction metadata.
        return {
            "record": record,
            "trade_id": trade_id,
            "direction": direction,
            "size": size,
            "entry_price": entry_price,
            "entry_commission": entry_commission,
            "exit_price": exit_price,
            "exit_commission": exit_commission,
            "gross_pnl": gross_pnl,
            "net_pnl": net_trade_pnl,
        }

    def reset_position(self) -> None:
        state = self.state
        state.virtual_position_size = 0
        state.virtual_entry_price = None
        state.tp_level = None
        state.sl_level = None
        state.virtual_entry_commission = 0.0
        state.current_trail_step = -1


class BacktestTradeMixin:
    """Compatibility/orchestration facade for virtual trade lifecycle."""

    def _ensure_trade_ledger(self) -> TradeLedger:
        if not hasattr(self, "_trade_ledger"):
            self._trade_ledger = TradeLedger()
        return self._trade_ledger

    def _ensure_trade_accounting_engine(self) -> TradeAccountingEngine:
        if not hasattr(self, "_trade_accounting_engine"):
            self._trade_accounting_engine = TradeAccountingEngine(self)
        return self._trade_accounting_engine

    def _open_virtual_position(self, signal: int, size: int, bar_index: int):
        current_bar = self.market.current_bar
        if current_bar is None:
            raise RuntimeError("Market.current_bar is required to open a virtual position")

        transaction = self._ensure_trade_accounting_engine()
        record = transaction.open(signal, size, bar_index, current_bar)
        trade_id = record["trade_id"]
        direction = record["direction"]
        entry_price = record["entry_price"]
        commission = record["entry_commission"]

        if self.logger.wants_trade():
            self.logger.trade(
                f"ENTRY_SIGNAL trade_id = {trade_id}; "
                f"bar_index = {bar_index}; signal = {signal}; dynamic_size = {size}; "
                f"execution_model = VIRTUAL_OPEN; entry_price = {entry_price}; "
                f"entry_slippage = 0.0; commission = {commission}"
            )
        if self.logger.wants_debug_event("ORDER_SUBMITTED"):
            self.logger.debug_event(
                "ORDER_SUBMITTED",
                trade_id=trade_id,
                bar_index=bar_index,
                signal=signal,
                order_ref=None,
                order_type="VIRTUAL_OPEN",
                requested_size=size,
                reference_open=current_bar.open,
                execution_price=entry_price,
                dynamic_slip=0.0,
                execution_model="VIRTUAL",
            )
        if self.logger.wants_trade():
            self.logger.trade(
                f"ENTRY_EXECUTED trade_id = {trade_id}; direction = {direction}; "
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
        transaction = self._ensure_trade_accounting_engine()
        result = transaction.close(reason, target_exec_price, bar_index, phase_index)
        trade_id = result["trade_id"]
        direction = result["direction"]
        size = result["size"]
        entry_price = result["entry_price"]
        entry_commission = result["entry_commission"]
        exit_price = result["exit_price"]
        exit_commission = result["exit_commission"]
        gross_pnl = result["gross_pnl"]
        net_trade_pnl = result["net_pnl"]

        if self.logger.wants_trade():
            self.logger.trade(
                f"EXIT_SIGNAL trade_id = {trade_id}; reason = {reason}; "
                f"bar_index = {bar_index}; phase_index = {phase_index}; "
                f"detected_price = {detected_price}; "
                f"level = {self.state.sl_level if reason == 'STOP_LOSS' else self.state.tp_level}; "
                f"execution_model = VIRTUAL_INTRABAR; target_exec_price = {exit_price}"
            )
        if self.logger.wants_trade():
            self.logger.trade(
                f"EXIT_EXECUTED trade_id = {trade_id}; reason = {reason}; "
                f"execution_model = VIRTUAL_INTRABAR; broker_executed_price = None; "
                f"execution_price = {exit_price}; target_exec_price = {exit_price}; "
                f"exit_slippage = {self._execution_engine.get_backtest_dynamic_slippage(size)}; "
                f"executed_size = {size}; exit_commission = {exit_commission}"
            )
        if self.logger.wants_trade():
            self.logger.trade(
                f"TRADE_CLOSED trade_id = {trade_id}; direction = {direction}; "
                f"size = {size}; entry_price = {entry_price}; "
                f"exit_price = {exit_price}; gross_pnl = {gross_pnl}; "
                f"entry_commission = {entry_commission}; "
                f"exit_commission = {exit_commission}; net_pnl = {net_trade_pnl}; "
                f"reason = {reason}; execution_model = VIRTUAL"
            )
        if self.logger.wants_debug_event("TRADE_UPDATE"):
            self.logger.debug_event(
                "TRADE_UPDATE",
                trade_id=trade_id,
                status="CLOSED",
                direction=direction,
                size=size,
                entry_price=entry_price,
                exit_price=exit_price,
                commission=self._money(entry_commission + exit_commission),
                pnl=gross_pnl,
                pnl_comm=net_trade_pnl,
                bar_index=bar_index,
                phase_index=phase_index,
                datetime=self.market.current_bar.datetime if self.market.current_bar else None,
            )

        transaction.reset_position()
