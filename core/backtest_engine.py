# Q6 EXECUTION MODEL — FIXED
# Based on QUIK(6).zip
# FIX: restore RealisticFuturesStrategy and ContractVolumeAnalyzer classes.
# Execution model: virtual fills, with intrabar TP/SL execution at the
# calculated target price +/- dynamic slippage. No deferred Market close.

import backtrader as bt

from core.backtest_logger import BacktestLogger


class PreviousBarRange(bt.Indicator):
    """Округлённый H-L предыдущего полностью закрытого бара."""

    lines = ("range",)
    params = (("precision", 2),)

    def __init__(self):
        self.addminperiod(2)

    def next(self):
        raw_range = self.data.high[-1] - self.data.low[-1]
        self.lines.range[0] = round(raw_range, self.p.precision)


class RealisticFuturesStrategy(bt.Strategy):
    """Интрабар-бэктестер с виртуальной моделью исполнения."""

    params = (
        ("trigger", None),
        ("tp", None),
        ("sl", None),
        ("risk", None),
        ("real_mult", None),
        ("real_margin", None),
        ("safety_factor", 1.1),
        ("precision_num", 2),
        ("slippage_points", 0.02),
        ("debug", False),
        ("dynamic_trail_steps", []),
        ("logger", None),
        ("initial_cash", 0.0),
    )

    def get_backtest_dynamic_slippage(self, size: int) -> float:
        if size <= 5:
            return 0.02
        elif size <= 15:
            return 0.04
        elif size <= 30:
            return 0.07
        return 0.15

    def __init__(self):
        self.logger = self.params.logger or BacktestLogger()
        self.trade_id = 0

        # Compatibility with the existing patched broker.
        self.slippage = self.params.slippage_points
        self.slip_open = True
        self.slip_suborders = True
        if hasattr(self.broker, "set_intrarbar_strategy"):
            self.broker.set_intrarbar_strategy(self)

        self.signal = 0
        self.main_order = None
        self.last_trade_bar = -1

        self.tp_level = None
        self.sl_level = None
        self.entry_price = None
        self.current_trail_step = -1

        self.pending_exit_reason = None
        self.pending_exit_target_price = None
        self.pending_exit_slippage = None
        self.pending_exit_bar = None
        self.pending_exit_phase = None

        # Virtual accounting.
        self.virtual_cash = float(self.params.initial_cash)
        self.virtual_position_size = 0
        self.virtual_entry_price = None
        self.virtual_entry_commission = 0.0
        self.virtual_exit_commission = 0.0
        self.virtual_gross_pnl = 0.0
        self.final_virtual_equity = 0.0
        self.closed_trades = 0
        self.total_contracts = 0

        self.atr = bt.indicators.AverageTrueRange(period=14)

        self.volatility = PreviousBarRange(
            self.data, precision=self.params.precision_num
        )
        self.long_signal = bt.And(
            self.volatility >= self.params.trigger,
            self.data.close(-1) > self.data.open(-1),
        )
        self.short_signal = bt.And(
            self.volatility >= self.params.trigger,
            self.data.close(-1) < self.data.open(-1),
        )

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
            dynamic_trail_steps=self.params.dynamic_trail_steps,
            execution_model="VIRTUAL",
            initial_cash=self.virtual_cash,
        )

    def _get_commission_per_side(self) -> float:
        """Комиссия одной стороны из того же Backtrader broker, что задан в backtester.py."""
        comminfo = self.broker.getcommissioninfo(self.data)
        return float(comminfo.p.commission)

    def _log_virtual_portfolio(self, bar_index: int):
        """Состояние виртуального счета. Брокер Backtrader не используется для исполнения."""
        unrealized = 0.0
        if self.virtual_position_size > 0:
            unrealized = (
                self.data.close[0] - self.virtual_entry_price
            ) * self.params.real_mult * abs(self.virtual_position_size)
        elif self.virtual_position_size < 0:
            unrealized = (
                self.virtual_entry_price - self.data.close[0]
            ) * self.params.real_mult * abs(self.virtual_position_size)

        equity = self.virtual_cash + unrealized
        self.logger.debug_event(
            "PORTFOLIO_STATE",
            bar_index=bar_index,
            datetime=self.data.datetime.datetime(0),
            cash=self.virtual_cash,
            unrealized_pnl=unrealized,
            portfolio_value=equity,
            position_size=self.virtual_position_size,
            position_price=self.virtual_entry_price,
            position_value=abs(self.virtual_position_size) * self.data.close[0],
            trade_id=self.trade_id,
        )

    def _open_virtual_position(self, signal: int, size: int, bar_index: int):
        """Открытие позиции по цене Open текущего бара — как в live simulation."""
        entry_price = round(self.data.open[0], self.params.precision_num)
        commission = round(
            self._get_commission_per_side() * size,
            self.params.precision_num,
        )

        self.virtual_position_size = size if signal > 0 else -size
        self.virtual_entry_price = entry_price
        self.entry_price = entry_price
        self.virtual_entry_commission = commission
        self.virtual_exit_commission = 0.0
        self.virtual_gross_pnl = 0.0

        self.virtual_cash -= commission
        self.trade_id += 1
        self.last_trade_bar = bar_index
        self.current_trail_step = -1

        tp_distance = round(self.params.tp, self.params.precision_num)
        sl_distance = round(self.params.sl, self.params.precision_num)

        if signal > 0:
            self.tp_level = round(entry_price + tp_distance, self.params.precision_num)
            self.sl_level = round(entry_price - sl_distance, self.params.precision_num)
            direction = "LONG"
        else:
            self.tp_level = round(entry_price - tp_distance, self.params.precision_num)
            self.sl_level = round(entry_price + sl_distance, self.params.precision_num)
            direction = "SHORT"

        self.logger.trade(
            f"ENTRY_SIGNAL bar_index = {bar_index}; signal = {signal}; "
            f"dynamic_size = {size}; execution_model = VIRTUAL_OPEN; "
            f"entry_price = {entry_price}; entry_slippage = 0.0; "
            f"commission = {commission}"
        )
        self.logger.debug_event(
            "ORDER_SUBMITTED",
            bar_index=bar_index,
            signal=signal,
            order_ref=None,
            order_type="VIRTUAL_OPEN",
            requested_size=size,
            reference_open=self.data.open[0],
            execution_price=entry_price,
            atr=self.atr[0],
            dynamic_slip=0.0,
            execution_model="VIRTUAL",
        )
        self.logger.trade(
            f"ENTRY_EXECUTED trade_id = {self.trade_id}; direction = {direction}; "
            f"execution_model = VIRTUAL_OPEN; execution_price = {entry_price}; "
            f"executed_size = {size}; entry_commission = {commission}"
        )

    def _close_virtual_position(
        self,
        reason: str,
        target_exec_price: float,
        detected_price: float,
        bar_index: int,
        phase_index: int,
    ):
        """Немедленное виртуальное исполнение выхода в текущей intrabar-фазе."""
        size = abs(self.virtual_position_size)
        direction = "LONG" if self.virtual_position_size > 0 else "SHORT"
        exit_price = round(target_exec_price, self.params.precision_num)

        if self.virtual_position_size > 0:
            gross_pnl = (
                exit_price - self.virtual_entry_price
            ) * self.params.real_mult * size
        else:
            gross_pnl = (
                self.virtual_entry_price - exit_price
            ) * self.params.real_mult * size

        exit_commission = round(
            self._get_commission_per_side() * size,
            self.params.precision_num,
        )
        net_trade_pnl = round(
            gross_pnl - self.virtual_entry_commission - exit_commission,
            self.params.precision_num,
        )

        self.virtual_cash += gross_pnl - exit_commission
        self.virtual_gross_pnl = gross_pnl
        self.virtual_exit_commission = exit_commission

        self.logger.trade(
            f"EXIT_SIGNAL trade_id = {self.trade_id}; reason = {reason}; "
            f"bar_index = {bar_index}; phase_index = {phase_index}; "
            f"detected_price = {detected_price}; "
            f"level = {self.sl_level if reason == 'STOP_LOSS' else self.tp_level}; "
            f"execution_model = VIRTUAL_INTRABAR; "
            f"target_exec_price = {exit_price}"
        )
        self.logger.trade(
            f"EXIT_EXECUTED trade_id = {self.trade_id}; reason = {reason}; "
            f"execution_model = VIRTUAL_INTRABAR; "
            f"broker_executed_price = None; execution_price = {exit_price}; "
            f"target_exec_price = {exit_price}; "
            f"exit_slippage = {self.pending_exit_slippage}; "
            f"executed_size = {size}; exit_commission = {exit_commission}"
        )
        self.logger.trade(
            f"TRADE_CLOSED trade_id = {self.trade_id}; direction = {direction}; "
            f"size = {size}; entry_price = {self.virtual_entry_price}; "
            f"exit_price = {exit_price}; gross_pnl = {gross_pnl}; "
            f"entry_commission = {self.virtual_entry_commission}; "
            f"exit_commission = {exit_commission}; "
            f"net_pnl = {net_trade_pnl}; "
            f"reason = {reason}; execution_model = VIRTUAL"
        )

        # Аналог notify_trade, но с единой виртуальной бухгалтерией.
        self.logger.debug_event(
            "TRADE_UPDATE",
            trade_ref=None,
            trade_id=self.trade_id,
            status="CLOSED",
            size=self.virtual_position_size,
            price=self.virtual_entry_price,
            value=self.virtual_entry_price * size * self.params.real_mult,
            commission=self.virtual_entry_commission + exit_commission,
            pnl=gross_pnl,
            pnl_comm=net_trade_pnl,
            bar_index=bar_index,
            datetime=self.data.datetime.datetime(0),
        )

        self.virtual_position_size = 0
        self.virtual_entry_price = None
        self.entry_price = None
        self.tp_level = None
        self.sl_level = None
        self.pending_exit_reason = None
        self.pending_exit_target_price = None
        self.pending_exit_slippage = None
        self.pending_exit_bar = None
        self.pending_exit_phase = None
        self.current_trail_step = -1

        self.closed_trades += 1
        self.total_contracts += size * 2

    def next(self):
        current_bar_index = len(self.data)

        self.logger.debug_event(
            "BAR",
            bar_index=current_bar_index,
            datetime=self.data.datetime.datetime(0),
            open=self.data.open[0],
            high=self.data.high[0],
            low=self.data.low[0],
            close=self.data.close[0],
            volume=self.data.volume[0],
            position_size=self.virtual_position_size,
            entry_price=self.virtual_entry_price,
            tp_level=self.tp_level,
            sl_level=self.sl_level,
            current_trail_step=self.current_trail_step,
        )

        self._log_virtual_portfolio(current_bar_index)

        previous_open = self.data.open[-1]
        previous_high = self.data.high[-1]
        previous_low = self.data.low[-1]
        previous_close = self.data.close[-1]
        previous_range_raw = previous_high - previous_low
        previous_range = round(previous_range_raw, self.params.precision_num)
        previous_bullish = previous_close > previous_open
        previous_bearish = previous_close < previous_open
        range_ok = previous_range >= self.params.trigger
        long_signal_value = bool(self.long_signal[0])
        short_signal_value = bool(self.short_signal[0])

        self.logger.debug_event(
            "SIGNAL_EVALUATION",
            bar_index=current_bar_index,
            datetime=self.data.datetime.datetime(0),
            previous_datetime=self.data.datetime.datetime(-1),
            current_open=self.data.open[0],
            current_high=self.data.high[0],
            current_low=self.data.low[0],
            current_close=self.data.close[0],
            previous_open=previous_open,
            previous_high=previous_high,
            previous_low=previous_low,
            previous_close=previous_close,
            previous_range_raw=previous_range_raw,
            previous_range=previous_range,
            trigger=self.params.trigger,
            range_ok=range_ok,
            previous_bullish=previous_bullish,
            previous_bearish=previous_bearish,
            long_signal=long_signal_value,
            short_signal=short_signal_value,
            atr=self.atr[0],
            position_size=self.virtual_position_size,
            main_order_ref=None,
        )

        # ==================================================================
        # OPEN POSITION: synthetic intrabar execution
        # ==================================================================
        if self.virtual_position_size:
            if self.tp_level is None or self.sl_level is None:
                return

            b_open = self.data.open[0]
            b_high = self.data.high[0]
            b_low = self.data.low[0]
            b_close = self.data.close[0]
            pos_size = abs(self.virtual_position_size)
            tp_distance = round(self.params.tp, self.params.precision_num)

            if b_close >= b_open:
                sub_ticks = [
                    {'high': b_open, 'low': b_open},
                    {'high': b_open, 'low': b_low},
                    {'high': b_high, 'low': b_low},
                    {'high': b_high, 'low': b_close},
                ]
            else:
                sub_ticks = [
                    {'high': b_open, 'low': b_open},
                    {'high': b_high, 'low': b_open},
                    {'high': b_high, 'low': b_low},
                    {'high': b_close, 'low': b_low},
                ]

            for phase_index, tick in enumerate(sub_ticks):
                if not self.virtual_position_size:
                    break

                high_price = tick['high']
                low_price = tick['low']

                self.logger.debug_event(
                    "INTRABAR_PHASE",
                    trade_id=self.trade_id,
                    bar_index=current_bar_index,
                    phase_index=phase_index,
                    high_price=high_price,
                    low_price=low_price,
                    entry_price=self.virtual_entry_price,
                    tp_level=self.tp_level,
                    sl_level=self.sl_level,
                    current_trail_step=self.current_trail_step,
                )

                # Трал — та же математика, что и в live engine.
                if self.virtual_position_size > 0:
                    current_profit_pts = high_price - self.virtual_entry_price
                    for step_idx, (trigger_pct, stop_pct) in enumerate(self.params.dynamic_trail_steps):
                        if step_idx > self.current_trail_step:
                            target_trigger = round(
                                tp_distance * trigger_pct,
                                self.params.precision_num,
                            )
                            if current_profit_pts >= target_trigger:
                                new_sl = round(
                                    self.virtual_entry_price
                                    + tp_distance * stop_pct,
                                    self.params.precision_num,
                                )
                                if new_sl > self.sl_level:
                                    old_sl_level = self.sl_level
                                    self.sl_level = new_sl
                                    self.current_trail_step = step_idx
                                    self.logger.trade(
                                        f"TRAIL_UPDATE trade_id = {self.trade_id}; "
                                        f"step_idx = {step_idx}; trigger_pct = {trigger_pct}; "
                                        f"stop_pct = {stop_pct}; old_sl_level = {old_sl_level}; "
                                        f"new_sl_level = {new_sl}; "
                                        f"current_profit_pts = {current_profit_pts}"
                                    )
                else:
                    current_profit_pts = self.virtual_entry_price - low_price
                    for step_idx, (trigger_pct, stop_pct) in enumerate(self.params.dynamic_trail_steps):
                        if step_idx > self.current_trail_step:
                            target_trigger = round(
                                tp_distance * trigger_pct,
                                self.params.precision_num,
                            )
                            if current_profit_pts >= target_trigger:
                                new_sl = round(
                                    self.virtual_entry_price
                                    - tp_distance * stop_pct,
                                    self.params.precision_num,
                                )
                                if new_sl < self.sl_level:
                                    old_sl_level = self.sl_level
                                    self.sl_level = new_sl
                                    self.current_trail_step = step_idx
                                    self.logger.trade(
                                        f"TRAIL_UPDATE trade_id = {self.trade_id}; "
                                        f"step_idx = {step_idx}; trigger_pct = {trigger_pct}; "
                                        f"stop_pct = {stop_pct}; old_sl_level = {old_sl_level}; "
                                        f"new_sl_level = {new_sl}; "
                                        f"current_profit_pts = {current_profit_pts}"
                                    )

                current_profit_pts = (
                    high_price - self.virtual_entry_price
                    if self.virtual_position_size > 0
                    else self.virtual_entry_price - low_price
                )

                self.logger.debug_event(
                    "TRAIL_EVALUATION",
                    trade_id=self.trade_id,
                    bar_index=current_bar_index,
                    phase_index=phase_index,
                    position_size=self.virtual_position_size,
                    high_price=high_price,
                    low_price=low_price,
                    entry_price=self.virtual_entry_price,
                    tp_level=self.tp_level,
                    sl_level=self.sl_level,
                    tp_distance=tp_distance,
                    current_trail_step=self.current_trail_step,
                    current_profit_pts=current_profit_pts,
                )

                slippage_val = self.get_backtest_dynamic_slippage(pos_size)

                stop_hit = (
                    low_price <= self.sl_level
                    if self.virtual_position_size > 0
                    else high_price >= self.sl_level
                )
                tp_hit = (
                    high_price >= self.tp_level
                    if self.virtual_position_size > 0
                    else low_price <= self.tp_level
                )

                self.logger.debug_event(
                    "EXIT_EVALUATION",
                    trade_id=self.trade_id,
                    bar_index=current_bar_index,
                    phase_index=phase_index,
                    position_size=self.virtual_position_size,
                    high_price=high_price,
                    low_price=low_price,
                    tp_level=self.tp_level,
                    sl_level=self.sl_level,
                    slippage_val=slippage_val,
                    stop_condition=stop_hit,
                    take_profit_condition=tp_hit,
                )

                reason = None
                detected_price = None
                if stop_hit:
                    reason = "STOP_LOSS"
                    detected_price = low_price if self.virtual_position_size > 0 else high_price
                    if self.virtual_position_size > 0:
                        target_exec_p = round(
                            self.sl_level - slippage_val,
                            self.params.precision_num,
                        )
                    else:
                        target_exec_p = round(
                            self.sl_level + slippage_val,
                            self.params.precision_num,
                        )
                elif tp_hit:
                    reason = "TAKE_PROFIT"
                    detected_price = high_price if self.virtual_position_size > 0 else low_price
                    if self.virtual_position_size > 0:
                        target_exec_p = round(
                            self.tp_level - slippage_val,
                            self.params.precision_num,
                        )
                    else:
                        target_exec_p = round(
                            self.tp_level + slippage_val,
                            self.params.precision_num,
                        )

                if reason is not None:
                    self.pending_exit_reason = reason
                    self.pending_exit_target_price = target_exec_p
                    self.pending_exit_slippage = slippage_val
                    self.pending_exit_bar = current_bar_index
                    self.pending_exit_phase = phase_index

                    self._close_virtual_position(
                        reason=reason,
                        target_exec_price=target_exec_p,
                        detected_price=detected_price,
                        bar_index=current_bar_index,
                        phase_index=phase_index,
                    )
                    return

            return

        # ==================================================================
        # NO POSITION: evaluate new entry
        # ==================================================================
        self.logger.debug_event(
            "SIGNAL_DECISION",
            bar_index=current_bar_index,
            long_signal=bool(self.long_signal[0]),
            short_signal=bool(self.short_signal[0]),
            selected_signal=1 if long_signal_value else (-1 if short_signal_value else 0),
            previous_range=previous_range,
            trigger=self.params.trigger,
            previous_bullish=previous_bullish,
            previous_bearish=previous_bearish,
        )

        selected_signal = 1 if long_signal_value else (-1 if short_signal_value else 0)
        if selected_signal == 0:
            return

        current_free_funds = self.virtual_cash
        loss_per_contract_rub = self.params.sl * self.params.real_mult
        max_rub_to_risk = current_free_funds * self.params.risk
        size_by_risk = int(max_rub_to_risk / loss_per_contract_rub)

        cost_margin_per_contract = self.params.real_margin * self.params.safety_factor
        max_size_by_margin = int(current_free_funds / cost_margin_per_contract)

        bar_volume = self.data.volume[-1]
        max_size_by_liquidity = max(1, int(bar_volume * 0.05))
        dynamic_size = min(
            size_by_risk,
            max_size_by_margin,
            max_size_by_liquidity,
        )

        self.logger.debug_event(
            "POSITION_SIZE",
            bar_index=current_bar_index,
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

        if dynamic_size < 1:
            self.logger.warning(
                f"ENTRY_SKIPPED bar_index = {current_bar_index}; "
                f"reason = POSITION_SIZE_ZERO; signal = {selected_signal}; "
                f"size_by_risk = {size_by_risk}; "
                f"max_size_by_margin = {max_size_by_margin}; "
                f"max_size_by_liquidity = {max_size_by_liquidity}"
            )
            return

        self._open_virtual_position(
            signal=selected_signal,
            size=dynamic_size,
            bar_index=current_bar_index,
        )

    def stop(self):
        # Позиция не закрывается искусственно в конце истории.
        # Итоговый equity учитывает mark-to-market последней цены, если позиция открыта.
        final_close = self.data.close[0] if len(self.data) else None
        unrealized = 0.0
        if self.virtual_position_size and final_close is not None:
            if self.virtual_position_size > 0:
                unrealized = (
                    final_close - self.virtual_entry_price
                ) * self.params.real_mult * abs(self.virtual_position_size)
            else:
                unrealized = (
                    self.virtual_entry_price - final_close
                ) * self.params.real_mult * abs(self.virtual_position_size)

        self.final_virtual_equity = self.virtual_cash + unrealized

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
        )
        if self.virtual_position_size:
            self.logger.warning(
                f"OPEN_POSITION_AT_END trade_id = {self.trade_id}; "
                f"position_size = {self.virtual_position_size}; "
                f"entry_price = {self.virtual_entry_price}; "
                f"mark_price = {final_close}; "
                f"unrealized_pnl = {unrealized}"
            )

class ContractVolumeAnalyzer(bt.Analyzer):
    """Анализатор общего объёма проторгованных контрактов."""

    def __init__(self):
        self.total_contracts = 0

    def notify_order(self, order):
        if order.status == order.Completed:
            self.total_contracts += abs(order.executed.size)

    def get_analysis(self):
        return self.total_contracts
