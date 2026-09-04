# BACKTEST EXECUTION MODEL
# Virtual execution model for historical backtests.
#
# Rules:
# - Signal uses the previous available candle.
# - Entry is at the current candle Open.
# - The newly opened position is exposed to the remainder of that same candle.
# - Intrabar processing follows a deterministic monotonic price path:
#     bullish/doji: Open -> Low -> High -> Close
#     bearish:      Open -> High -> Low -> Close
# - Trail/SL/TP are processed in chronological price-crossing order.
# - ATR is diagnostic only and MUST NOT affect strategy warm-up.
# - Virtual accounting is the source of truth; Backtrader analyzers are not used.

from __future__ import annotations

import math
from typing import Any

import backtrader as bt

from core.backtest_logger import BacktestLogger


class PreviousBarRange(bt.Indicator):
    """Compatibility indicator; intentionally has no strategy-level role."""

    lines = ("range",)
    params = (("precision", 2),)

    def __init__(self):
        self.addminperiod(2)

    def next(self):
        self.lines.range[0] = round(
            float(self.data.high[-1]) - float(self.data.low[-1]),
            self.p.precision,
        )


class ContractVolumeAnalyzer(bt.Analyzer):
    """Compatibility class only. It is not a source of backtest results."""

    def __init__(self):
        self.total_contracts = 0

    def notify_order(self, order):
        if order.status == order.Completed:
            self.total_contracts += abs(order.executed.size)

    def get_analysis(self):
        return self.total_contracts


class RealisticFuturesStrategy(bt.Strategy):
    """Virtual intrabar futures backtester."""

    params = (
        ("trigger", None),
        ("tp", None),
        ("sl", None),
        ("risk", None),
        ("real_mult", None),
        ("real_margin", None),
        ("safety_factor", 1.1),
        ("precision_num", 2),
        ("precision_money", 2),
        ("slippage_points", 0.02),
        ("debug", False),
        ("dynamic_trail_steps", []),
        ("logger", None),
        ("initial_cash", 0.0),
    )

    def __init__(self):
        self.logger = self.params.logger or BacktestLogger()

        self.trade_id = 0
        self.last_trade_bar = -1

        self.virtual_cash = self._money(self.params.initial_cash)
        self.virtual_position_size = 0
        self.virtual_entry_price = None
        self.virtual_entry_commission = 0.0
        self.virtual_gross_pnl = 0.0
        self.virtual_exit_commission = 0.0

        self.entry_price = None
        self.tp_level = None
        self.sl_level = None
        self.current_trail_step = -1

        self.closed_trades = 0
        self.total_contracts = 0
        self.total_commission = 0.0
        self.final_virtual_equity = self.virtual_cash

        self._trade_records: list[dict[str, Any]] = []
        self._closed_trade_records: list[dict[str, Any]] = []

        # Deliberately no ATR indicator:
        # any Backtrader indicator can impose a minperiod and delay the strategy.
        self.atr_diagnostic = None

        self.logger.event(
            "STRATEGY_INIT",
            trigger=self.params.trigger,
            tp=self.params.tp,
            sl=self.params.sl,
            risk=self.params.risk,
            real_mult=self.params.real_mult,
            real_margin=self.params.real_margin,
            safety_factor=self.params.safety_factor,
            precision_num=self.params.precision_num,
            precision_money=self.params.precision_money,
            dynamic_trail_steps=self.params.dynamic_trail_steps,
            execution_model="VIRTUAL",
            entry_model="CURRENT_BAR_OPEN",
            signal_model="PREVIOUS_AVAILABLE_BAR",
            intrabar_model="ORDERED_MONOTONIC_PATH",
            atr_role="DIAGNOSTIC_ONLY",
            initial_cash=self.virtual_cash,
        )

    def _money(self, value: float) -> float:
        return round(float(value), self.params.precision_money)

    def _price(self, value: float) -> float:
        return round(float(value), self.params.precision_num)

    def get_backtest_dynamic_slippage(self, size: int) -> float:
        if size <= 5:
            return 0.02
        if size <= 15:
            return 0.04
        if size <= 30:
            return 0.07
        return 0.15

    def _get_commission_per_side(self) -> float:
        comminfo = self.broker.getcommissioninfo(self.data)
        return float(comminfo.p.commission)

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

    def _open_virtual_position(self, signal: int, size: int, bar_index: int):
        entry_price = self._price(self.data.open[0])
        commission = self._money(self._get_commission_per_side() * size)

        self.trade_id += 1
        direction = "LONG" if signal > 0 else "SHORT"
        signed_size = size if signal > 0 else -size

        self.virtual_position_size = signed_size
        self.virtual_entry_price = entry_price
        self.entry_price = entry_price
        self.virtual_entry_commission = commission
        self.virtual_exit_commission = 0.0
        self.virtual_gross_pnl = 0.0

        self.virtual_cash = self._money(self.virtual_cash - commission)
        self.total_commission = self._money(self.total_commission + commission)
        self.last_trade_bar = bar_index
        self.current_trail_step = -1

        tp_distance = self._price(self.params.tp)
        sl_distance = self._price(self.params.sl)

        if signal > 0:
            self.tp_level = self._price(entry_price + tp_distance)
            self.sl_level = self._price(entry_price - sl_distance)
        else:
            self.tp_level = self._price(entry_price - tp_distance)
            self.sl_level = self._price(entry_price + sl_distance)

        record = {
            "trade_id": self.trade_id,
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
        self._trade_records.append(record)

        self.logger.trade(
            f"ENTRY_SIGNAL trade_id = {self.trade_id}; "
            f"bar_index = {bar_index}; signal = {signal}; dynamic_size = {size}; "
            f"execution_model = VIRTUAL_OPEN; entry_price = {entry_price}; "
            f"entry_slippage = 0.0; commission = {commission}"
        )
        self.logger.debug_event(
            "ORDER_SUBMITTED",
            trade_id=self.trade_id,
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
        self.logger.trade(
            f"ENTRY_EXECUTED trade_id = {self.trade_id}; direction = {direction}; "
            f"execution_model = VIRTUAL_OPEN; execution_price = {entry_price}; "
            f"executed_size = {size}; entry_commission = {commission}; "
            f"tp_level = {self.tp_level}; sl_level = {self.sl_level}"
        )

    def _close_virtual_position(
        self,
        reason: str,
        target_exec_price: float,
        detected_price: float,
        bar_index: int,
        phase_index: int,
    ):
        if not self.virtual_position_size or self.virtual_entry_price is None:
            raise RuntimeError("Attempted to close a virtual position that is not open")

        size = abs(self.virtual_position_size)
        direction = "LONG" if self.virtual_position_size > 0 else "SHORT"
        exit_price = self._price(target_exec_price)

        if self.virtual_position_size > 0:
            gross_pnl = (
                exit_price - self.virtual_entry_price
            ) * self.params.real_mult * size
        else:
            gross_pnl = (
                self.virtual_entry_price - exit_price
            ) * self.params.real_mult * size
        gross_pnl = self._money(gross_pnl)

        exit_commission = self._money(
            self._get_commission_per_side() * size
        )
        net_trade_pnl = self._money(
            gross_pnl - self.virtual_entry_commission - exit_commission
        )

        self.virtual_cash = self._money(
            self.virtual_cash + gross_pnl - exit_commission
        )
        self.virtual_gross_pnl = gross_pnl
        self.virtual_exit_commission = exit_commission
        self.total_commission = self._money(
            self.total_commission + exit_commission
        )

        record = self._trade_records[-1]
        if record["trade_id"] != self.trade_id:
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
        self._closed_trade_records.append(record)

        self.logger.trade(
            f"EXIT_SIGNAL trade_id = {self.trade_id}; reason = {reason}; "
            f"bar_index = {bar_index}; phase_index = {phase_index}; "
            f"detected_price = {detected_price}; "
            f"level = {self.sl_level if reason == 'STOP_LOSS' else self.tp_level}; "
            f"execution_model = VIRTUAL_INTRABAR; target_exec_price = {exit_price}"
        )
        self.logger.trade(
            f"EXIT_EXECUTED trade_id = {self.trade_id}; reason = {reason}; "
            f"execution_model = VIRTUAL_INTRABAR; broker_executed_price = None; "
            f"execution_price = {exit_price}; target_exec_price = {exit_price}; "
            f"exit_slippage = {self.get_backtest_dynamic_slippage(size)}; "
            f"executed_size = {size}; exit_commission = {exit_commission}"
        )
        self.logger.trade(
            f"TRADE_CLOSED trade_id = {self.trade_id}; direction = {direction}; "
            f"size = {size}; entry_price = {self.virtual_entry_price}; "
            f"exit_price = {exit_price}; gross_pnl = {gross_pnl}; "
            f"entry_commission = {self.virtual_entry_commission}; "
            f"exit_commission = {exit_commission}; net_pnl = {net_trade_pnl}; "
            f"reason = {reason}; execution_model = VIRTUAL"
        )
        self.logger.debug_event(
            "TRADE_UPDATE",
            trade_id=self.trade_id,
            status="CLOSED",
            direction=direction,
            size=size,
            entry_price=self.virtual_entry_price,
            exit_price=exit_price,
            commission=self._money(
                self.virtual_entry_commission + exit_commission
            ),
            pnl=gross_pnl,
            pnl_comm=net_trade_pnl,
            bar_index=bar_index,
            phase_index=phase_index,
            datetime=self.data.datetime.datetime(0),
        )

        self.virtual_position_size = 0
        self.virtual_entry_price = None
        self.entry_price = None
        self.tp_level = None
        self.sl_level = None
        self.virtual_entry_commission = 0.0
        self.virtual_exit_commission = 0.0
        self.current_trail_step = -1

        self.closed_trades += 1
        self.total_contracts += size * 2

    def _trail_trigger_levels(self, direction: int):
        tp_distance = self._price(self.params.tp)
        entry = self.virtual_entry_price
        if entry is None:
            return []

        result = []
        for step_idx, (trigger_pct, stop_pct) in enumerate(
            self.params.dynamic_trail_steps
        ):
            if step_idx <= self.current_trail_step:
                continue

            trigger_distance = self._price(tp_distance * trigger_pct)
            if direction > 0:
                trigger_price = self._price(entry + trigger_distance)
                new_sl = self._price(entry + tp_distance * stop_pct)
            else:
                trigger_price = self._price(entry - trigger_distance)
                new_sl = self._price(entry - tp_distance * stop_pct)

            result.append((step_idx, trigger_price, new_sl))
        return result

    def _apply_trail_step(self, step_idx: int, new_sl: float, current_price: float):
        old_sl = self.sl_level
        if old_sl is None:
            return

        if self.virtual_position_size > 0:
            if new_sl <= old_sl:
                self.current_trail_step = max(self.current_trail_step, step_idx)
                return
        else:
            if new_sl >= old_sl:
                self.current_trail_step = max(self.current_trail_step, step_idx)
                return

        self.sl_level = new_sl
        self.current_trail_step = step_idx
        self.logger.trade(
            f"TRAIL_UPDATE trade_id = {self.trade_id}; "
            f"step_idx = {step_idx}; "
            f"trigger_pct = {self.params.dynamic_trail_steps[step_idx][0]}; "
            f"stop_pct = {self.params.dynamic_trail_steps[step_idx][1]}; "
            f"old_sl_level = {old_sl}; new_sl_level = {new_sl}; "
            f"trigger_cross_price = {current_price}"
        )

    def _process_monotonic_segment(
        self,
        start_price: float,
        end_price: float,
        bar_index: int,
        phase_index: int,
    ) -> bool:
        """
        Process one monotonic path segment in exact price-crossing order.

        Returns True if the position was closed.
        """
        if not self.virtual_position_size:
            return True

        start = self._price(start_price)
        end = self._price(end_price)
        if start == end:
            # A zero-length phase cannot cross a level.
            return False

        direction = 1 if self.virtual_position_size > 0 else -1
        moving_up = end > start

        # For a LONG, favorable movement is upward; for a SHORT, favorable
        # movement is downward. Only favorable movement can activate a trail.
        favorable = (direction > 0 and moving_up) or (direction < 0 and not moving_up)

        events = []

        if favorable:
            for step_idx, trigger_price, new_sl in self._trail_trigger_levels(direction):
                if direction > 0 and start < trigger_price <= end:
                    events.append(("TRAIL", trigger_price, step_idx, new_sl))
                elif direction < 0 and end <= trigger_price < start:
                    events.append(("TRAIL", trigger_price, step_idx, new_sl))

        # Exit levels are checked on the actual movement direction.
        if direction > 0:
            if not moving_up and end <= self.sl_level < start:
                events.append(("STOP_LOSS", self.sl_level, None, None))
            elif moving_up and start <= self.sl_level <= end:
                # This can only happen if a previously moved stop is already
                # at/above the current path start.
                events.append(("STOP_LOSS", self.sl_level, None, None))

            if moving_up and start <= self.tp_level <= end:
                events.append(("TAKE_PROFIT", self.tp_level, None, None))
        else:
            if moving_up and start <= self.sl_level <= end:
                events.append(("STOP_LOSS", self.sl_level, None, None))
            elif not moving_up and end <= self.sl_level <= start:
                events.append(("STOP_LOSS", self.sl_level, None, None))

            if not moving_up and end <= self.tp_level <= start:
                events.append(("TAKE_PROFIT", self.tp_level, None, None))

        # Sort by actual traversal order. At equal price, exits take priority
        # over trail activation so a level cannot retroactively protect itself.
        if moving_up:
            events.sort(key=lambda e: (e[1], 0 if e[0] != "TRAIL" else 1))
        else:
            events.sort(key=lambda e: (-e[1], 0 if e[0] != "TRAIL" else 1))

        current_price = start
        for event_type, event_price, step_idx, new_sl in events:
            if not self.virtual_position_size:
                return True

            # Ignore stale events made obsolete by an earlier event.
            if moving_up and event_price < current_price:
                continue
            if not moving_up and event_price > current_price:
                continue

            current_price = event_price

            if event_type == "TRAIL":
                self._apply_trail_step(
                    step_idx,
                    new_sl,
                    current_price,
                )
                continue

            size = abs(self.virtual_position_size)
            slippage = self.get_backtest_dynamic_slippage(size)

            if event_type == "STOP_LOSS":
                detected_price = current_price
                if self.virtual_position_size > 0:
                    target_exec_price = self._price(
                        self.sl_level - slippage
                    )
                else:
                    target_exec_price = self._price(
                        self.sl_level + slippage
                    )
            else:
                detected_price = current_price
                if self.virtual_position_size > 0:
                    target_exec_price = self._price(
                        self.tp_level - slippage
                    )
                else:
                    target_exec_price = self._price(
                        self.tp_level + slippage
                    )

            self.logger.debug_event(
                "EXIT_CROSSING",
                trade_id=self.trade_id,
                bar_index=bar_index,
                phase_index=phase_index,
                event_type=event_type,
                crossing_price=current_price,
                sl_level=self.sl_level,
                tp_level=self.tp_level,
                slippage=slippage,
            )

            self._close_virtual_position(
                reason=event_type,
                target_exec_price=target_exec_price,
                detected_price=detected_price,
                bar_index=bar_index,
                phase_index=phase_index,
            )
            return True

        return False

    def _process_open_position_bar(self, bar_index: int):
        b_open = self._price(self.data.open[0])
        b_high = self._price(self.data.high[0])
        b_low = self._price(self.data.low[0])
        b_close = self._price(self.data.close[0])

        # Doji is deliberately treated as bullish/deterministic:
        # Open -> Low -> High -> Close.
        bullish_or_doji = b_close >= b_open
        if bullish_or_doji:
            points = [b_open, b_low, b_high, b_close]
        else:
            points = [b_open, b_high, b_low, b_close]

        for phase_index in range(len(points) - 1):
            if not self.virtual_position_size:
                break

            start_price = points[phase_index]
            end_price = points[phase_index + 1]

            self.logger.debug_event(
                "INTRABAR_PHASE",
                trade_id=self.trade_id,
                bar_index=bar_index,
                phase_index=phase_index,
                start_price=start_price,
                end_price=end_price,
                direction="UP" if end_price > start_price else (
                    "DOWN" if end_price < start_price else "FLAT"
                ),
                entry_price=self.virtual_entry_price,
                tp_level=self.tp_level,
                sl_level=self.sl_level,
                current_trail_step=self.current_trail_step,
            )

            closed = self._process_monotonic_segment(
                start_price=start_price,
                end_price=end_price,
                bar_index=bar_index,
                phase_index=phase_index,
            )

            self.logger.debug_event(
                "TRAIL_EVALUATION",
                trade_id=self.trade_id,
                bar_index=bar_index,
                phase_index=phase_index,
                position_size=self.virtual_position_size,
                start_price=start_price,
                end_price=end_price,
                entry_price=self.virtual_entry_price,
                tp_level=self.tp_level,
                sl_level=self.sl_level,
                current_trail_step=self.current_trail_step,
            )

            self.logger.debug_event(
                "EXIT_EVALUATION",
                trade_id=self.trade_id,
                bar_index=bar_index,
                phase_index=phase_index,
                position_size=self.virtual_position_size,
                start_price=start_price,
                end_price=end_price,
                tp_level=self.tp_level,
                sl_level=self.sl_level,
                closed=closed,
            )

            if closed:
                break

    def _calculate_position_size(self, bar_index: int) -> int:
        current_free_funds = self.virtual_cash
        loss_per_contract_rub = float(self.params.sl) * float(self.params.real_mult)
        max_rub_to_risk = current_free_funds * float(self.params.risk)
        size_by_risk = int(max_rub_to_risk / loss_per_contract_rub)

        cost_margin_per_contract = (
            float(self.params.real_margin) * float(self.params.safety_factor)
        )
        max_size_by_margin = int(
            (current_free_funds + 1e-5) // cost_margin_per_contract
        )

        # Same liquidity rule as live engine: previous available candle volume.
        bar_volume = int(self.data.volume[-1])
        max_size_by_liquidity = max(1, int(bar_volume * 0.05))

        dynamic_size = min(
            size_by_risk,
            max_size_by_margin,
            max_size_by_liquidity,
        )

        self.logger.debug_event(
            "POSITION_SIZE",
            bar_index=bar_index,
            current_free_funds=current_free_funds,
            max_rub_to_risk=max_rub_to_risk,
            loss_per_contract_rub=loss_per_contract_rub,
            size_by_risk=size_by_risk,
            cost_margin_per_contract=cost_margin_per_contract,
            max_size_by_margin=max_size_by_margin,
            bar_volume=bar_volume,
            max_size_by_liquidity=max_size_by_liquidity,
            dynamic_size=dynamic_size,
        )

        return dynamic_size

    def next(self):
        current_bar_index = len(self.data)

        self.logger.debug_event(
            "BAR",
            bar_index=current_bar_index,
            datetime=self.data.datetime.datetime(0),
            open=float(self.data.open[0]),
            high=float(self.data.high[0]),
            low=float(self.data.low[0]),
            close=float(self.data.close[0]),
            volume=float(self.data.volume[0]),
            position_size=self.virtual_position_size,
            entry_price=self.virtual_entry_price,
            tp_level=self.tp_level,
            sl_level=self.sl_level,
            current_trail_step=self.current_trail_step,
        )

        self._log_virtual_portfolio(current_bar_index)

        # The strategy needs one previous available candle, and no more.
        if current_bar_index < 2:
            self.logger.debug_event(
                "SIGNAL_EVALUATION_SKIPPED",
                bar_index=current_bar_index,
                reason="NO_PREVIOUS_AVAILABLE_BAR",
            )
            return

        previous_open = float(self.data.open[-1])
        previous_high = float(self.data.high[-1])
        previous_low = float(self.data.low[-1])
        previous_close = float(self.data.close[-1])
        previous_volume = int(self.data.volume[-1])

        previous_range_raw = previous_high - previous_low
        previous_range = self._price(previous_range_raw)
        previous_bullish = previous_close > previous_open
        previous_bearish = previous_close < previous_open
        range_ok = previous_range >= float(self.params.trigger)

        long_signal_value = bool(range_ok and previous_bullish)
        short_signal_value = bool(range_ok and previous_bearish)

        # ATR is intentionally not calculated as a Backtrader indicator.
        atr_diagnostic = None

        self.logger.debug_event(
            "SIGNAL_EVALUATION",
            bar_index=current_bar_index,
            datetime=self.data.datetime.datetime(0),
            previous_datetime=self.data.datetime.datetime(-1),
            current_open=float(self.data.open[0]),
            current_high=float(self.data.high[0]),
            current_low=float(self.data.low[0]),
            current_close=float(self.data.close[0]),
            previous_open=previous_open,
            previous_high=previous_high,
            previous_low=previous_low,
            previous_close=previous_close,
            previous_volume=previous_volume,
            previous_range_raw=previous_range_raw,
            previous_range=previous_range,
            trigger=self.params.trigger,
            range_ok=range_ok,
            previous_bullish=previous_bullish,
            previous_bearish=previous_bearish,
            long_signal=long_signal_value,
            short_signal=short_signal_value,
            atr=atr_diagnostic,
            position_size=self.virtual_position_size,
            main_order_ref=None,
        )

        if self.virtual_position_size:
            self._process_open_position_bar(current_bar_index)
            return

        selected_signal = (
            1 if long_signal_value else (-1 if short_signal_value else 0)
        )

        self.logger.debug_event(
            "SIGNAL_DECISION",
            bar_index=current_bar_index,
            long_signal=long_signal_value,
            short_signal=short_signal_value,
            selected_signal=selected_signal,
            previous_range=previous_range,
            trigger=self.params.trigger,
            previous_bullish=previous_bullish,
            previous_bearish=previous_bearish,
        )

        if selected_signal == 0:
            return

        dynamic_size = self._calculate_position_size(current_bar_index)
        if dynamic_size < 1:
            self.logger.warning(
                f"ENTRY_SKIPPED bar_index = {current_bar_index}; "
                f"reason = POSITION_SIZE_ZERO; signal = {selected_signal}"
            )
            return

        self._open_virtual_position(
            signal=selected_signal,
            size=dynamic_size,
            bar_index=current_bar_index,
        )

        # Entry occurs at current Open, so the position must be exposed to
        # the remainder of the same candle.
        self._process_open_position_bar(current_bar_index)

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
