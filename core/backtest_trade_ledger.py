from __future__ import annotations

from typing import Any


class TradeLedger:
    """Owner of trade history and trade lifecycle state.

    The ledger is deliberately independent from ``BacktestState``: State owns
    only the current execution/portfolio state, while this object owns trade
    records, active-trade lifecycle and trade aggregates.
    """

    def __init__(self) -> None:
        self._next_trade_id = 0
        self._active_record: dict[str, Any] | None = None
        self._records: list[dict[str, Any]] = []
        self._closed_records: list[dict[str, Any]] = []
        self._closed_trades = 0
        self._total_contracts = 0

    @property
    def records(self) -> list[dict[str, Any]]:
        return self._records

    @property
    def closed_records(self) -> list[dict[str, Any]]:
        return self._closed_records

    @property
    def active_record(self) -> dict[str, Any] | None:
        return self._active_record

    @property
    def trade_id(self) -> int:
        return self._next_trade_id

    @property
    def closed_trades(self) -> int:
        return self._closed_trades

    @property
    def total_contracts(self) -> int:
        return self._total_contracts

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
        if self._active_record is not None:
            raise RuntimeError("Attempted to open a trade while another trade is open")
        if size < 1:
            raise ValueError("Trade size must be positive")

        self._next_trade_id += 1
        record = {
            "trade_id": self._next_trade_id,
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
        self._records.append(record)
        self._active_record = record
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
        record = self._active_record
        if record is None:
            raise RuntimeError("Attempted to close a trade while no trade is open")
        if record["trade_id"] != self._next_trade_id:
            raise RuntimeError("Trade lifecycle order is corrupted")

        record.update({
            "exit_bar": bar_index,
            "exit_phase": phase_index,
            "exit_price": exit_price,
            "exit_reason": reason,
            "exit_commission": exit_commission,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
        })
        self._closed_records.append(record)
        self._active_record = None
        self._closed_trades += 1
        self._total_contracts += int(record["size"]) * 2
        return record

    def check(self, position_size: int) -> list[str]:
        errors: list[str] = []
        if self._next_trade_id != len(self._records):
            errors.append(
                f"trade_id={self._next_trade_id} records={len(self._records)}"
            )

        for expected_id, record in enumerate(self._records, start=1):
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

        closed_count = sum(1 for r in self._records if r["exit_bar"] is not None)
        if closed_count != self._closed_trades:
            errors.append(
                f"closed_trades={self._closed_trades} records={closed_count}"
            )

        if position_size:
            open_records = [r for r in self._records if r["exit_bar"] is None]
            if len(open_records) != 1:
                errors.append(
                    f"open_position={position_size} open_records={len(open_records)}"
                )
            elif open_records[0]["trade_id"] != self._next_trade_id:
                errors.append("open trade is not the latest trade")
            elif self._active_record is not open_records[0]:
                errors.append("active trade record is not the open record")
        else:
            if any(r["exit_bar"] is None for r in self._records):
                errors.append("open trade record exists while position is flat")
            if self._active_record is not None:
                errors.append("active trade record exists while position is flat")

        return errors
