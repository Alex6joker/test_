# Q5 FIXED VERSION
# Based on: QUIK(5).zip / QUIK/core/backtest_engine.py
# Original SHA256: c3956739f8e88035d555fa30e51247aeecb0fa0601db891d4b08521d45cce153
# This file contains the diagnostic/backtest corrections prepared for Q5.

import backtrader as bt

from core.backtest_logger import BacktestLogger


class PreviousBarRange(bt.Indicator):
    """Округлённый H-L предыдущего полностью закрытого бара.

    Зеркалит live-логику: сначала берём H/L предыдущей свечи,
    затем округляем результат до шага цены инструмента.
    """

    lines = ("range",)
    params = (("precision", 2),)

    def __init__(self):
        self.addminperiod(2)

    def next(self):
        raw_range = self.data.high[-1] - self.data.low[-1]
        self.lines.range[0] = round(raw_range, self.p.precision)

class ContractVolumeAnalyzer(bt.Analyzer):
    """Анализатор общего объема проторгованных контрактов"""
    def __init__(self):
        self.total_contracts = 0

    def notify_order(self, order):
        if order.status == order.Completed:
            self.total_contracts += abs(order.executed.size)

    def get_analysis(self):
        return self.total_contracts


class RealisticFuturesStrategy(bt.Strategy):
    """
    Универсальная интрабар-стратегия бэктеста и оптимизации.
    Полностью изолирована от жестких конфигурационных файлов.
    """
    params = (
        ('trigger', None),
        ('tp', None),
        ('sl', None),
        ('risk', None),
        ('real_mult', None),        # Передается динамически из config бумаги
        ('real_margin', None),      # Передается динамически из config бумаги
        ('safety_factor', 1.1),     # Запас по деньгам под ГО
        ('precision_num', 2),       # Округление цены для фьючерса
        ('slippage_points', 0.02),  # Базовый шаг проскальзывания
        ('debug', False),
        ('dynamic_trail_steps', []),
        ('logger', None),
    )
    
    def get_backtest_dynamic_slippage(self, size: int) -> float:
        """Синхронизированная таблица проскальзывания — точное зеркало боевого ядра"""
        if size <= 5: 
            return 0.02
        elif size <= 15: 
            return 0.04
        elif size <= 30: 
            return 0.07
        else: 
            return 0.15

    def __init__(self):
        self.logger = self.params.logger or BacktestLogger()
        self.trade_id = 0

        # Создаем свойства, которые ожидает увидеть кастомный патч ядра
        self.slippage = self.params.slippage_points
        self.slip_open = True
        self.slip_suborders = True
        # Вызываем скрытый метод патча, который активирует встроенное фиксированное проскальзывание
        self.broker.set_intrarbar_strategy(self)
        self.signal = 0
        self.main_order = None 
        self.last_trade_bar = -1
        self.tp_level = None
        self.sl_level = None
        self.entry_price = None
        self.current_trail_step = -1  # ИНДЕКС СТУПЕНИ: -1 означает, что трал еще ни разу не сработал

        self.pending_exit_reason = None
        self.pending_exit_target_price = None
        self.pending_exit_slippage = None
        self.pending_exit_bar = None
        self.pending_exit_phase = None
        
        # Интеграция классического индикатора ATR для оценки динамического шума
        self.atr = bt.indicators.AverageTrueRange(period=14)

        # ВАЖНО: волатильность должна пересчитываться на каждом баре.
        # Ранее здесь вычислялось обычное число один раз в __init__, из-за чего
        # весь бэктест фактически использовал H-L только одного бара.
        # PreviousBarRange — динамическая Line и при этом сохраняет точное
        # округление, используемое боевым engine.py.
        self.volatility = PreviousBarRange(
            self.data,
            precision=self.params.precision_num,
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
        )

    def notify_order(self, order):
        self.logger.debug_event(
            "ORDER_STATUS",
            order_ref=order.ref,
            status=order.getstatusname(),
            order_size=order.size,
            order_price=order.price,
            executed_size=order.executed.size,
            executed_price=order.executed.price,
            executed_value=order.executed.value,
            executed_commission=order.executed.comm,
            created_price=getattr(order.created, "price", None),
            created_size=getattr(order.created, "size", None),
            exectype=order.getordername(),
            is_buy=order.isbuy(),
            is_sell=order.issell(),
            is_main_order=(order == self.main_order),
            trade_id=self.trade_id,
            bar_index=len(self.data),
            datetime=self.data.datetime.datetime(0),
        )

        if order.status in [order.Canceled, order.Margin, order.Rejected]:
            if order == self.main_order:
                self.main_order = None
                self.last_trade_bar = -1

        elif order.status == order.Completed:
            if order == self.main_order:
                # Ухудшаем цену открытия на величину смоделированного в боевом цикле ATR-проскальзывания
                slip_adj = getattr(self, 'entry_slip_adjustment', 0.0)
                exec_price = order.executed.price + slip_adj
                self.entry_price = round(exec_price, self.params.precision_num)
                self.trade_id += 1
                self.logger.trade(
                    f"ENTRY_EXECUTED trade_id = {self.trade_id}; order_ref = {order.ref}; "
                    f"broker_executed_price = {order.executed.price}; entry_slip_adjustment = {slip_adj}; "
                    f"strategy_entry_price = {self.entry_price}; executed_size = {order.executed.size}; "
                    f"executed_value = {order.executed.value}; executed_commission = {order.executed.comm}"
                )
                
                tp_distance = round(self.params.tp, self.params.precision_num)
                sl_distance = round(self.params.sl, self.params.precision_num)
                
                if order.isbuy():
                    self.tp_level = round(self.entry_price + tp_distance, self.params.precision_num)
                    self.sl_level = round(self.entry_price - sl_distance, self.params.precision_num)
                else:
                    self.tp_level = round(self.entry_price - tp_distance, self.params.precision_num)
                    self.sl_level = round(self.entry_price + sl_distance, self.params.precision_num)
                
                self.main_order = None
                self.entry_slip_adjustment = 0.0
                self.current_trail_step = -1  # СБРОС

            elif self.position.size == 0 and self.pending_exit_reason is not None:
                self.logger.trade(
                    f"EXIT_EXECUTED trade_id = {self.trade_id}; order_ref = {order.ref}; "
                    f"reason = {self.pending_exit_reason}; "
                    f"broker_executed_price = {order.executed.price}; "
                    f"target_exec_price = {self.pending_exit_target_price}; "
                    f"exit_slippage = {self.pending_exit_slippage}; "
                    f"executed_size = {order.executed.size}; "
                    f"executed_value = {order.executed.value}; "
                    f"executed_commission = {order.executed.comm}"
                )
                self.pending_exit_reason = None
                self.pending_exit_target_price = None
                self.pending_exit_slippage = None
                self.pending_exit_bar = None
                self.pending_exit_phase = None

    def notify_trade(self, trade):
        """Полная диагностика жизненного цикла сделки и фактического P&L брокера."""
        self.logger.debug_event(
            "TRADE_UPDATE",
            trade_ref=trade.ref,
            trade_id=self.trade_id,
            status="CLOSED" if trade.isclosed else "OPEN",
            size=trade.size,
            price=trade.price,
            value=trade.value,
            commission=trade.commission,
            pnl=trade.pnl,
            pnl_comm=trade.pnlcomm,
            bar_index=len(self.data),
            datetime=self.data.datetime.datetime(0),
        )

        if trade.isclosed:
            self.logger.trade(
                f"TRADE_CLOSED trade_id = {self.trade_id}; trade_ref = {trade.ref}; "
                f"size = {trade.size}; price = {trade.price}; value = {trade.value}; "
                f"commission = {trade.commission}; pnl = {trade.pnl}; pnl_comm = {trade.pnlcomm}"
            )

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
            position_size=self.position.size,
            entry_price=self.entry_price,
            tp_level=self.tp_level,
            sl_level=self.sl_level,
            current_trail_step=self.current_trail_step,
        )

        self.logger.debug_event(
            "PORTFOLIO_STATE",
            bar_index=current_bar_index,
            datetime=self.data.datetime.datetime(0),
            cash=self.broker.getcash(),
            portfolio_value=self.broker.getvalue(),
            position_size=self.position.size,
            position_price=self.position.price,
            position_value=self.position.size * self.position.price if self.position else 0.0,
            trade_id=self.trade_id,
        )

        # Полная диагностика входного сигнала.
        # Все значения берутся из текущего бара и предыдущего уже закрытого бара.
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
            position_size=self.position.size,
            main_order_ref=getattr(self.main_order, "ref", None),
        )

        # --- БЛОК СОПРОВОЖДЕНИЯ ОТКРЫТОЙ ПОЗИЦИИ ---
        if self.position:
            if self.tp_level is None or self.sl_level is None:
                return

            b_open = self.data.open[0]
            b_high = self.data.high[0]
            b_low = self.data.low[0]
            b_close = self.data.close[0]
            pos_size = abs(self.position.size)
            tp_distance = round(self.params.tp, self.params.precision_num)

            # Разбиваем минутный бар на последовательные фазы движения цены (Bar Split)
            if b_close >= b_open:
                sub_ticks = [{'high': b_open, 'low': b_open}, {'high': b_open, 'low': b_low}, {'high': b_high, 'low': b_low}, {'high': b_high, 'low': b_close}]
            else:
                sub_ticks = [{'high': b_open, 'low': b_open}, {'high': b_high, 'low': b_open}, {'high': b_high, 'low': b_low}, {'high': b_close, 'low': b_low}]

            # Прогоняем ваш оригинальный алгоритм трала и выходов через каждую интрабар фазу
            for phase_index, tick in enumerate(sub_ticks):
                if not self.position:
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
                    entry_price=self.entry_price,
                    tp_level=self.tp_level,
                    sl_level=self.sl_level,
                    current_trail_step=self.current_trail_step,
                )

                # --- СИНХРОНИЗИРОВАННЫЙ МНОГОСТУПЕНЧАТЫЙ ТРЕЙЛИНГ-СТОП (ЗЕРКАЛО БОЕВОГО ЯДРА) ---
                if self.position.size > 0: # ЛОНГ
                    current_profit_pts = high_price - self.entry_price
                    for step_idx, (trigger_pct, stop_pct) in enumerate(self.params.dynamic_trail_steps):
                        if step_idx > self.current_trail_step:
                            target_trigger = round(tp_distance * trigger_pct, self.params.precision_num)
                            if current_profit_pts >= target_trigger:
                                new_sl = round(self.entry_price + tp_distance * stop_pct, self.params.precision_num)
                                if new_sl > self.sl_level:
                                    old_sl_level = self.sl_level
                                    self.sl_level = new_sl
                                    self.current_trail_step = step_idx
                                    self.logger.trade(
                                        f"TRAIL_UPDATE trade_id = {self.trade_id}; step_idx = {step_idx}; "
                                        f"trigger_pct = {trigger_pct}; stop_pct = {stop_pct}; "
                                        f"old_sl_level = {old_sl_level}; new_sl_level = {new_sl}; "
                                        f"current_profit_pts = {current_profit_pts}"
                                    )

                elif self.position.size < 0: # ШОРТ
                    current_profit_pts = self.entry_price - low_price
                    for step_idx, (trigger_pct, stop_pct) in enumerate(self.params.dynamic_trail_steps):
                        if step_idx > self.current_trail_step:
                            target_trigger = round(tp_distance * trigger_pct, self.params.precision_num)
                            if current_profit_pts >= target_trigger:
                                new_sl = round(self.entry_price - tp_distance * stop_pct, self.params.precision_num)
                                if new_sl < self.sl_level:
                                    old_sl_level = self.sl_level
                                    self.sl_level = new_sl
                                    self.current_trail_step = step_idx
                                    self.logger.trade(
                                        f"TRAIL_UPDATE trade_id = {self.trade_id}; step_idx = {step_idx}; "
                                        f"trigger_pct = {trigger_pct}; stop_pct = {stop_pct}; "
                                        f"old_sl_level = {old_sl_level}; new_sl_level = {new_sl}; "
                                        f"current_profit_pts = {current_profit_pts}"
                                    )

                self.logger.debug_event(
                    "TRAIL_EVALUATION",
                    trade_id=self.trade_id,
                    bar_index=current_bar_index,
                    phase_index=phase_index,
                    position_size=self.position.size,
                    high_price=high_price,
                    low_price=low_price,
                    entry_price=self.entry_price,
                    tp_level=self.tp_level,
                    sl_level=self.sl_level,
                    tp_distance=tp_distance,
                    current_trail_step=self.current_trail_step,
                    current_profit_pts=(high_price - self.entry_price) if self.position.size > 0 else (self.entry_price - low_price),
                )

                # --- ИСПРАВЛЕННЫЙ КОНТУР ВЫХОДОВ: СИНХРОНИЗАЦИЯ С EMERGENCY_CLEAN_PORTFOLIO ---
                # Рассчитываем динамическое боевое проскальзывание под конкретный объем лота
                slippage_val = self.get_backtest_dynamic_slippage(pos_size)

                self.logger.debug_event(
                    "EXIT_EVALUATION",
                    trade_id=self.trade_id,
                    bar_index=current_bar_index,
                    phase_index=phase_index,
                    position_size=self.position.size,
                    high_price=high_price,
                    low_price=low_price,
                    tp_level=self.tp_level,
                    sl_level=self.sl_level,
                    slippage_val=slippage_val,
                    stop_condition=(low_price <= self.sl_level) if self.position.size > 0 else (high_price >= self.sl_level),
                    take_profit_condition=(high_price >= self.tp_level) if self.position.size > 0 else (low_price <= self.tp_level),
                )

                if self.position.size > 0: # ЛОНГ
                    # Консервативный сценарий: при одновременном касании TP/SL
                    # выбираем STOP_LOSS, поскольку точный порядок тиков из OHLC неизвестен.
                    if low_price <= self.sl_level:
                        target_exec_p = round(self.sl_level - slippage_val, self.params.precision_num)
                        self.pending_exit_reason = "STOP_LOSS"
                        self.pending_exit_target_price = target_exec_p
                        self.pending_exit_slippage = slippage_val
                        self.pending_exit_bar = current_bar_index
                        self.pending_exit_phase = phase_index
                        self.logger.trade(
                            f"EXIT_SIGNAL trade_id = {self.trade_id}; reason = STOP_LOSS; "
                            f"bar_index = {current_bar_index}; phase_index = {phase_index}; "
                            f"detected_price = {low_price}; level = {self.sl_level}; "
                            f"slippage_val = {slippage_val}; target_exec_price = {target_exec_p}"
                        )
                        self.close(size=pos_size, exectype=bt.Order.Market)
                        self.entry_slip_adjustment = target_exec_p - b_open
                        self.tp_level = self.sl_level = None
                        self.last_trade_bar = current_bar_index
                        return

                    if high_price >= self.tp_level:
                        target_exec_p = round(self.tp_level - slippage_val, self.params.precision_num)
                        self.pending_exit_reason = "TAKE_PROFIT"
                        self.pending_exit_target_price = target_exec_p
                        self.pending_exit_slippage = slippage_val
                        self.pending_exit_bar = current_bar_index
                        self.pending_exit_phase = phase_index
                        self.logger.trade(
                            f"EXIT_SIGNAL trade_id = {self.trade_id}; reason = TAKE_PROFIT; "
                            f"bar_index = {current_bar_index}; phase_index = {phase_index}; "
                            f"detected_price = {high_price}; level = {self.tp_level}; "
                            f"slippage_val = {slippage_val}; target_exec_price = {target_exec_p}"
                        )
                        self.close(size=pos_size, exectype=bt.Order.Market)
                        self.entry_slip_adjustment = target_exec_p - b_open
                        self.tp_level = self.sl_level = None
                        self.last_trade_bar = current_bar_index
                        return

                elif self.position.size < 0: # ШОРТ
                    if high_price >= self.sl_level:
                        target_exec_p = round(self.sl_level + slippage_val, self.params.precision_num)
                        self.pending_exit_reason = "STOP_LOSS"
                        self.pending_exit_target_price = target_exec_p
                        self.pending_exit_slippage = slippage_val
                        self.pending_exit_bar = current_bar_index
                        self.pending_exit_phase = phase_index
                        self.logger.trade(
                            f"EXIT_SIGNAL trade_id = {self.trade_id}; reason = STOP_LOSS; "
                            f"bar_index = {current_bar_index}; phase_index = {phase_index}; "
                            f"detected_price = {high_price}; level = {self.sl_level}; "
                            f"slippage_val = {slippage_val}; target_exec_price = {target_exec_p}"
                        )
                        self.close(size=pos_size, exectype=bt.Order.Market)
                        self.entry_slip_adjustment = target_exec_p - b_open
                        self.tp_level = self.sl_level = None
                        self.last_trade_bar = current_bar_index
                        return

                    if low_price <= self.tp_level:
                        target_exec_p = round(self.tp_level + slippage_val, self.params.precision_num)
                        self.pending_exit_reason = "TAKE_PROFIT"
                        self.pending_exit_target_price = target_exec_p
                        self.pending_exit_slippage = slippage_val
                        self.pending_exit_bar = current_bar_index
                        self.pending_exit_phase = phase_index
                        self.logger.trade(
                            f"EXIT_SIGNAL trade_id = {self.trade_id}; reason = TAKE_PROFIT; "
                            f"bar_index = {current_bar_index}; phase_index = {phase_index}; "
                            f"detected_price = {low_price}; level = {self.tp_level}; "
                            f"slippage_val = {slippage_val}; target_exec_price = {target_exec_p}"
                        )
                        self.close(size=pos_size, exectype=bt.Order.Market)
                        self.entry_slip_adjustment = target_exec_p - b_open
                        self.tp_level = self.sl_level = None
                        self.last_trade_bar = current_bar_index
                        return
            return

        # Блокировка повторных входов на одном и том же баре
        if self.last_trade_bar == current_bar_index:
            self.logger.debug_event(
                "ENTRY_BLOCKED",
                bar_index=current_bar_index,
                reason="LAST_TRADE_BAR",
                last_trade_bar=self.last_trade_bar,
            )
            return

        if self.main_order:
            self.logger.debug_event(
                "ENTRY_BLOCKED",
                bar_index=current_bar_index,
                reason="MAIN_ORDER_PENDING",
                main_order_ref=self.main_order.ref,
            )
            return

        # --- БЛОК ОПРЕДЕЛЕНИЯ СИГНАЛА НА ВХОД ---
        if self.long_signal[0]:
            self.signal = 1
        elif self.short_signal[0]:
            self.signal = -1

        self.logger.debug_event(
            "SIGNAL_DECISION",
            bar_index=current_bar_index,
            long_signal=bool(self.long_signal[0]),
            short_signal=bool(self.short_signal[0]),
            selected_signal=self.signal,
            previous_range=previous_range,
            trigger=self.params.trigger,
            previous_bullish=previous_bullish,
            previous_bearish=previous_bearish,
        )

        # --- БЛОК ВХОДА В СДЕЛКУ ПО ЦЕНЕ OPEN НОВОГО БАРА ---
        if self.signal != 0:
            current_free_funds = self.broker.getcash()

            loss_per_contract_rub = self.params.sl * self.params.real_mult
            max_rub_to_risk = current_free_funds * self.params.risk
            size_by_risk = int(max_rub_to_risk / loss_per_contract_rub)

            cost_margin_per_contract = self.params.real_margin * self.params.safety_factor
            max_size_by_margin = int(current_free_funds / cost_margin_per_contract)

            # Ограничение лота по реальной ликвидности свечи (не более 5%)
            bar_volume = self.data.volume[-1]
            max_size_by_liquidity = max(1, int(bar_volume * 0.05))

            dynamic_size = min(size_by_risk, max_size_by_margin, max_size_by_liquidity)

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
                    f"reason = POSITION_SIZE_ZERO; signal = {self.signal}; "
                    f"size_by_risk = {size_by_risk}; max_size_by_margin = {max_size_by_margin}; "
                    f"max_size_by_liquidity = {max_size_by_liquidity}"
                )

            if dynamic_size >= 1:
                # Шаг цены (минимальный квант изменения цены инструмента)
                tick_size = 1.0 if self.params.precision_num == 0 else 10**(-self.params.precision_num)
                
                # Коэффициент проскальзывания: отдаем рынку 15% от текущего минутного ATR
                # Но защищаем логику: проскальзывание не может быть меньше 1 шага цены
                dynamic_slip = max(self.atr[0] * 0.15, tick_size)
                dynamic_slip = round(dynamic_slip / tick_size) * tick_size

                self.logger.trade(
                    f"ENTRY_SIGNAL bar_index = {current_bar_index}; signal = {self.signal}; "
                    f"dynamic_size = {dynamic_size}; atr = {self.atr[0]}; "
                    f"dynamic_slip = {dynamic_slip}"
                )

                if self.signal == 1:
                    # Backtrader исполнит Market-ордер на следующем доступном баре.
                    self.main_order = self.buy(size=dynamic_size, exectype=bt.Order.Market)
                    self.entry_slip_adjustment = dynamic_slip
                    self.last_trade_bar = current_bar_index
                elif self.signal == -1:
                    self.main_order = self.sell(size=dynamic_size, exectype=bt.Order.Market)
                    self.entry_slip_adjustment = -dynamic_slip
                    self.last_trade_bar = current_bar_index

                self.logger.debug_event(
                    "ORDER_SUBMITTED",
                    bar_index=current_bar_index,
                    signal=self.signal,
                    order_ref=getattr(self.main_order, "ref", None),
                    order_type="BUY_MARKET" if self.signal == 1 else "SELL_MARKET",
                    requested_size=dynamic_size,
                    reference_open=self.data.open[0],
                    atr=self.atr[0],
                    dynamic_slip=dynamic_slip,
                    entry_slip_adjustment=self.entry_slip_adjustment,
                )

            self.signal = 0

    def stop(self):
        self.logger.event(
            "BACKTEST_STOP",
            bar_index=len(self.data),
            datetime=self.data.datetime.datetime(0) if len(self.data) else None,
            position_size=self.position.size,
            entry_price=self.entry_price,
            tp_level=self.tp_level,
            sl_level=self.sl_level,
            trade_id=self.trade_id,
        )
        if self.position:
            self.logger.warning(
                f"OPEN_POSITION_AT_END trade_id = {self.trade_id}; "
                f"position_size = {self.position.size}; entry_price = {self.entry_price}"
            )
            self.close()
