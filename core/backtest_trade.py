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

    def __init__(self, *, state, params, accounting, ledger, price_fn) -> None:
        self.state = state
        self.params = params
        self._price = price_fn
        self.accounting = accounting
        self.ledger = ledger

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

