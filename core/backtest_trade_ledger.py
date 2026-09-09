from __future__ import annotations

from typing import Any


class TradeLedger:
    """Trade lifecycle ledger for the virtual backtest.

    The ledger owns trade-record lifecycle operations while BacktestState
    remains the mutable state container during the staged refactor.
    Accounting/P&L is deliberately outside this class.
    """

    def __init__(self, state) -> None:
        self.state = state
        self._active_trade_id = (
            state.trade_id
            if state.virtual_position_size and state.trade_records
            else None
        )

    @property
    def records(self) -> list[dict[str, Any]]:
        return self.state.trade_records

    @property
    def closed_records(self) -> list[dict[str, Any]]:
        return self.state.closed_trade_records

    @property
    def active_record(self) -> dict[str, Any] | None:
        if self._active_trade_id is None:
            return None
        for record in reversed(self.records):
            if record["trade_id"] == self._active_trade_id:
                return record if record["exit_bar"] is None else None
        return None

    def open_trade(
        self,
        *,
        direction: str,
        size: int,
        bar_index: int,
        entry_datetime,
        entry_price: float,
        entry_commission: float,
    ) -> dict[str, Any]:
        if self.active_record is not None:
            raise RuntimeError("Attempted to open a trade while another trade is open")
        if size < 1:
            raise ValueError("Trade size must be positive")

        self.state.trade_id += 1
        record = {
            "trade_id": self.state.trade_id,
            "direction": direction,
            "size": size,
            "entry_bar": bar_index,
            "entry_datetime": entry_datetime,
            "entry_price": entry_price,
            "entry_commission": entry_commission,
            "exit_bar": None,
            "exit_phase": None,
            "exit_price": None,
            "exit_reason": None,
            "exit_commission": 0.0,
            "gross_pnl": 0.0,
            "net_pnl": None,
        }
        self.records.append(record)
        self._active_trade_id = record["trade_id"]
        return record

    def close_trade(
        self,
        *,
        bar_index: int,
        phase_index: int,
        exit_price: float,
        reason: str,
        exit_commission: float,
        gross_pnl: float,
        net_pnl: float,
    ) -> dict[str, Any]:
        record = self.active_record
        if record is None:
            raise RuntimeError("Attempted to close a trade while no trade is open")
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
                "net_pnl": net_pnl,
            }
        )
        self.closed_records.append(record)
        self._active_trade_id = None
        return record

    def check(self) -> list[str]:
        errors: list[str] = []
        if self.state.trade_id != len(self.records):
            errors.append(
                f"trade_id={self.state.trade_id} records={len(self.records)}"
            )

        for expected_id, record in enumerate(self.records, start=1):
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

        closed_count = sum(1 for r in self.records if r["exit_bar"] is not None)
        if closed_count != self.state.closed_trades:
            errors.append(
                f"closed_trades={self.state.closed_trades} records={closed_count}"
            )

        if self.state.virtual_position_size:
            open_records = [r for r in self.records if r["exit_bar"] is None]
            if len(open_records) != 1:
                errors.append(
                    f"open_position={self.state.virtual_position_size} open_records={len(open_records)}"
                )
            elif open_records[0]["trade_id"] != self.state.trade_id:
                errors.append("open trade is not the latest trade")
        else:
            if any(r["exit_bar"] is None for r in self.records):
                errors.append("open trade record exists while position is flat")

        return errors
