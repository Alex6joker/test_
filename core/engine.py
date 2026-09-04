import os
import sys
import asyncio
import logging
import pandas as pd
import backtrader as bt

# Принудительно подключаем патч для корректной работы Backtrader
try:
    import core.patch_backtrader
except ImportError:
    # Если запускается напрямую из папки core
    import patch_backtrader

import csv
from datetime import datetime, time

class QuikTimeFrameEmulator:
    """Класс-эмулятор для стопроцентного обхода внутренних ENUM библиотеки"""
    def __init__(self, val):
        self.value = val

class LiveTradingEngine:    
    def __init__(self, cfg):
        """Инициализация движка под конкретный инструмент."""
        self.cfg = cfg
        self.qp_client = None
        self.M1_INTERVAL = QuikTimeFrameEmulator(1)

        # Состояние текущей виртуальной позиции
        self.current_position_size = 0 
        self.tp_level = None
        self.sl_level = None
        self.virtual_cash = cfg.INITIAL_CASH 
        self.violation_counter = 0
        self.last_processed_minute = -1
        self.last_processed_candle_dt = None 
        self.last_entry_candle_dt = None # БАРЬЕР ПРОТИВ ПОВТОРНЫХ ВХОДОВ НА ОДНОМ БАРЕ
        
        # --- СТРАТЕГИЧЕСКИЙ КЭШ ЦЕН ДЛЯ ПАРАЛЛЕЛЬНЫХ ТАСКОВ ---
        self.last_known_ask = 0.0
        self.last_known_bid = 0.0
        self.is_processing_order_book = False  # Атомарный замок от Race Condition
        self.virtual_position_entry_price = 0.0  # Явное сохранение цены исполнения входа
        self.current_atr_slippage = self.cfg.SLIPPAGE_POINTS # Резервное значение (дефолт)
        self.current_trail_step = -1 # ИНДЕКС СТУПЕНИ: -1 означает, что каскадный трал еще не сработал

    def is_market_clearing_time(self) -> bool:
        """Расписание Мосбиржи (Июнь - Июль 2026)"""
        now_dt = datetime.now()
        now_time = now_dt.time()
        weekday = now_dt.weekday()

        change_date = getattr(self.cfg, 'REGLAMENT_CHANGE_DATE', datetime(2026, 7, 14))
        if now_dt >= change_date:
            weekday_market_open = time(6, 59, 0)
        else:
            weekday_market_open = time(8, 59, 0)

        if 0 <= weekday <= 4:
            weekday_market_close = time(23, 49, 0)
            if now_time >= weekday_market_close or now_time < weekday_market_open:
                return True
            return False
        return True

    def get_dynamic_slippage(self, size: int) -> float:
        """Синхронизированная таблица проскальзывания"""
        if size <= 5: return 0.02
        elif size <= 15: return 0.04
        elif size <= 30: return 0.07
        else: return 0.15

    def calculate_dynamic_size_sync(self, bar_vol: int) -> int:
        """Синхронный тройной фильтр объема позиции на основе динамического cfg"""
        try:
            current_free_funds = self.virtual_cash 
            loss_per_contract_rub = self.cfg.STOP_LOSS * self.cfg.REAL_MULT
            max_rub_to_risk = current_free_funds * self.cfg.OFFER_RISK
            size_by_risk = int(max_rub_to_risk / loss_per_contract_rub)

            cost_margin_per_contract = self.cfg.REAL_MARGIN * self.cfg.SAFETY_FACTOR
            max_size_by_margin = int((current_free_funds + 1e-5) // cost_margin_per_contract)

            max_size_by_liquidity = max(1, int(bar_vol * 0.05))

            dynamic_size = min(size_by_risk, max_size_by_margin, max_size_by_liquidity)
            final_size = max(1, dynamic_size)
            
            return final_size
        except Exception as e:
            logging.error(f"Ошибка расчета лота для {self.cfg.FUT_SEC_CODE}: {e}. Берем лот=1")
            return 1

    async def save_trade_to_csv(self, direction: str, exit_type: str, entry_p: float, exit_p: float, size: int, profit_rub: float, current_balance: float):
        """Асинхронная запись сделки в файл отчета (без блокировки основного потока)"""
        # Динамически определяем путь к папке запущенного инструмента
        instrument_dir = os.path.dirname(self.cfg.__file__)
        file_path = os.path.join(instrument_dir, self.cfg.LIVE_REPORT_CSV)
        file_exists = os.path.exists(file_path)

        row_data = {
            'Timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'Direction': direction,
            'Exit_Type': exit_type,
            'Lots': size,
            'Entry_Price': round(entry_p, self.cfg.PRECISION_NUM),
            'Exit_Price': round(exit_p, self.cfg.PRECISION_NUM),
            'Gross_PnL_Pts': round(exit_p - entry_p if direction == 'LONG' else entry_p - exit_p, self.cfg.PRECISION_NUM),
            'Net_PnL_Rub': round(profit_rub, self.cfg.PRECISION_NUM_DEPO_RUB),
            'Virtual_Balance': round(current_balance, self.cfg.PRECISION_NUM_DEPO_RUB)
        }
        
        if "STOP" in exit_type:
            logging.warning(
                f"[КРИТИЧЕСКИЙ ПРОБИВ СТОП-ЛОССА] Инструмент: {self.cfg.FUT_SEC_CODE} | "
                f"Направление: {direction} | Вход: {entry_p} | Выход: {exit_p} | Лот: {size} | "
                f"Убыток: {profit_rub:.2f} руб. | Баланс счета: {current_balance:.2f}р"
        )
        elif "TAKE" in exit_type:
            logging.info(
                f"[ПРОБИВ ТЕЙК-ПРОФИТА ИСПОЛНЕН] Инструмент: {self.cfg.FUT_SEC_CODE} | "
                f"Направление: {direction} | Вход: {entry_p} | Выход: {exit_p} | Лот: {size} | "
                f"Прибыль: +{profit_rub:.2f} руб. | Баланс счета: {current_balance:.2f}р"
        )
        
        # Фиксируем красивое текстовое сообщение о сделке в фоне
        # Это не тормозит стакан, так как метод save_trade_to_csv запущен как фоновый task
        trade_label = "СТОП-ЛОСС" if "STOP" in exit_type else "ТЕЙК-ПРОФИТ"
        logging.info(
            f"[ВИРТУАЛЬНЫЙ {trade_label} ИСПОЛНЕН] "
            f"Направление: {direction} | На фьючерсе: {self.cfg.FUT_SEC_CODE} | "
            f"Выход: {exit_p} | Лот: {size} | PnL: {profit_rub:.2f}р | Баланс: {current_balance:.2f}р"
        )

        # Внутренняя синхронная функция для выполнения в отдельном потоке ОС
        def write_operation():
            with open(file_path, mode='a', newline='', encoding='cp1251') as f:
                writer = csv.DictWriter(f, fieldnames=row_data.keys(), delimiter=';')
                if not file_exists:
                    writer.writeheader()
                writer.writerow(row_data)

        # Выносим тяжелую дисковую операцию в фоновый поток
        await asyncio.to_thread(write_operation)

    async def execute_order_simulation(self, operation: str, price: float, quantity: int, candle_time: str = ""):
        """Расчет уровней без отправки реальной заявки в QUIK"""
        logging.info(f"[ИМИТАЦИЯ ВХОДА] Сигнал: {operation} | Кол-во: {quantity} | Цена Open: {price}")

        self.violation_counter = 0 
        exec_price = price
        self.virtual_position_entry_price = round(price, self.cfg.PRECISION_NUM)
        self.current_trail_step = -1 # СБРОС: Обнуляем индекс пройденных ступеней каскада для новой сделки

        if operation == 'BUY':
            self.current_position_size = quantity
            self.tp_level = round(exec_price + self.cfg.TAKE_PROFIT, self.cfg.PRECISION_NUM)
            self.sl_level = round(exec_price - self.cfg.STOP_LOSS, self.cfg.PRECISION_NUM)
        elif operation == 'SELL':
            self.current_position_size = -quantity
            self.tp_level = round(exec_price - self.cfg.TAKE_PROFIT, self.cfg.PRECISION_NUM)
            self.sl_level = round(exec_price + self.cfg.STOP_LOSS, self.cfg.PRECISION_NUM)

        logging.info(f"[ВИРТУАЛЬНАЯ ПОЗИЦИЯ ОТКРЫТА] Лот: {self.current_position_size} | TP: {self.tp_level} | SL: {self.sl_level} | Текущий депо: {self.virtual_cash:.2f} руб.")

    async def check_and_trail_position(self):
        """
            Высокоскоростное интрабар-сопровождение виртуальной позиции.
            Оптимизировано: Запрос BID, ASK и LAST объединен в один сетевой такт через asyncio.gather.
        """
        try:
            if self.current_position_size == 0 or self.tp_level is None or self.sl_level is None:
                self.is_processing_order_book = False
                return

            pos_size = abs(self.current_position_size)
            slippage = self.get_dynamic_slippage(pos_size)

            # Параллельный высокоскоростной запрос стакана котировок (Level 2)
            try:
                ob = await self.qp_client.order_book.get_quote_level2(self.cfg.FUT_CLASS_CODE, self.cfg.FUT_SEC_CODE)

                # Если структура стакана нарушена или пуста — немедленно выходим из такта
                if not ob or not hasattr(ob, 'bid') or not hasattr(ob, 'offer') or not ob.bid or not ob.offer:
                    return

                # Преобразуем коллекции к спискам для безопасной итерации по индексам
                trail_offers = list(ob.offer)
                trail_bids = list(ob.bid)

                if len(trail_offers) == 0 or len(trail_bids) == 0:
                    return

                # Вытаскиваем цены абсолютно всех уровней стакана для безопасного поиска лучших краев
                all_trail_asks = [float(x.price if hasattr(x, 'price') else x.get('price', 0.0) if isinstance(x, dict) else x) for x in trail_offers]
                all_trail_bids = [float(x.price if hasattr(x, 'price') else x.get('price', 0.0) if isinstance(x, dict) else x) for x in trail_bids]

                # Очищаем массивы цен от нулевых и некорректных значений биржи
                all_trail_asks = [x for x in all_trail_asks if x > 0]
                all_trail_bids = [x for x in all_trail_bids if x > 0]

                if not all_trail_asks or not all_trail_bids:
                    return

                # Жестко определяем лучшие котировки краев спреда
                best_ask = min(all_trail_asks)
                best_bid = max(all_trail_bids)

                # Математический расчет Midprice для интрабар-трейлинга
                last_trade_price = round((best_ask + best_bid) / 2, self.cfg.PRECISION_NUM)

            except Exception as e_network:
                logging.error(f"[{self.cfg.FUT_SEC_CODE}] Сбой быстрого опроса стакана котировок в трейлинге: {e_network}")
                return

            # Тотальный барьер: если расчет дал ноль, блокируем дальнейшее выполнение условий TP/SL
            if last_trade_price <= 0:
                self.is_processing_order_book = False
                return
                
            # Безопасный барьер: если конфигурации каскадного трала нет в config.py — прерываем такт
            if not hasattr(self.cfg, 'DYNAMIC_TRAIL_STEPS') or not self.cfg.DYNAMIC_TRAIL_STEPS:
                return

            # ======================================================================
            # ПОЗИЦИЯ LONG (Вход по Ask, Выход по Bid в стакане)
            # ======================================================================
            if self.current_position_size > 0:
                entry_p = self.virtual_position_entry_price
                tp_dist = round(self.cfg.TAKE_PROFIT, self.cfg.PRECISION_NUM)
                sl_p = self.sl_level
                
                # Проходим по каскаду ступеней, переданных из конфигурационного файла бумаги
                for step_idx, (trigger_pct, stop_pct) in enumerate(self.cfg.DYNAMIC_TRAIL_STEPS):
                    # Шаг активируется, только если он строго старше текущего зафиксированного
                    if step_idx > self.current_trail_step:
                        target_trigger = round(entry_p + tp_dist * trigger_pct, self.cfg.PRECISION_NUM)
                        
                        if last_trade_price >= target_trigger:
                            new_sl = round(entry_p + tp_dist * stop_pct, self.cfg.PRECISION_NUM)
                            if new_sl > sl_p: # Защитный барьер: виртуальный стоп лонга двигаем только вверх
                                self.sl_level = new_sl
                                self.current_trail_step = step_idx # Фиксируем пройденный рубеж каскада
                                logging.info(
                                    f"[{self.cfg.FUT_SEC_CODE}] СУХОЙ ТЕСТ LONG: Сработала ступень №{step_idx + 1} "
                                    f"(триггер {int(trigger_pct*100)}%). Стоп подтянут в прибыль: {sl_p} -> {self.sl_level}"
                                )
                                break # За один тиковый проход обрабатываем строго одну ступень

                # Выход по Стоп-Лоссу (Фиксируем цену строго по уровню SL минус проскальзывание)
                if last_trade_price > 0 and last_trade_price <= self.sl_level:
                    exec_p = round(self.sl_level - float(slippage), self.cfg.PRECISION_NUM) 
                    rub_profit = (exec_p - entry_p) * self.cfg.REAL_MULT * pos_size
                    commission = self.cfg.REAL_COMMISSION * pos_size
                    final_rub_profit = rub_profit - commission
                    self.virtual_cash += final_rub_profit
                    self.current_position_size = 0
                    self.tp_level = self.sl_level = None
                    async with asyncio.Lock():
                        asyncio.create_task(self.save_trade_to_csv('LONG', 'STOP_LOSS', entry_p, exec_p, pos_size, final_rub_profit, float(self.virtual_cash)))
                    return

                # Выход по Тейк-Профиту (Фиксируем цену строго по целевому уровню TP)
                elif last_trade_price > 0 and last_trade_price >= self.tp_level:
                    exec_p = round(self.tp_level - float(slippage), self.cfg.PRECISION_NUM)
                    rub_profit = (exec_p - entry_p) * self.cfg.REAL_MULT * pos_size
                    commission = self.cfg.REAL_COMMISSION * pos_size
                    final_rub_profit = rub_profit - commission
                    self.virtual_cash += final_rub_profit
                    self.current_position_size = 0
                    self.tp_level = self.sl_level = None
                    async with asyncio.Lock():
                        asyncio.create_task(self.save_trade_to_csv('LONG', 'TAKE_PROFIT', entry_p, exec_p, pos_size, final_rub_profit, float(self.virtual_cash)))
                    return

            # ======================================================================
            # ПОЗИЦИЯ SHORT (Вход по Bid, Выход по Ask в стакане)
            # ======================================================================
            elif self.current_position_size < 0:
                entry_p = self.virtual_position_entry_price
                tp_dist = round(self.cfg.TAKE_PROFIT, self.cfg.PRECISION_NUM)
                sl_p = self.sl_level
                
                # Проходим по каскаду ступеней, переданных из конфигурационного файла бумаги
                for step_idx, (trigger_pct, stop_pct) in enumerate(self.cfg.DYNAMIC_TRAIL_STEPS):
                    # Шаг активируется, только если он строго старше текущего зафиксированного
                    if step_idx > self.current_trail_step:
                        target_trigger = round(entry_p - tp_dist * trigger_pct, self.cfg.PRECISION_NUM)
                        
                        if last_trade_price <= target_trigger:
                            new_sl = round(entry_p - tp_dist * stop_pct, self.cfg.PRECISION_NUM)
                            if new_sl < sl_p: # Защитный барьер: виртуальный стоп шорта двигаем только вниз
                                self.sl_level = new_sl
                                self.current_trail_step = step_idx # Фиксируем пройденный рубеж каскада
                                logging.info(
                                    f"[{self.cfg.FUT_SEC_CODE}] СУХОЙ ТЕСТ SHORT: Сработала ступень №{step_idx + 1} "
                                    f"(триггер {int(trigger_pct*100)}%). Стоп подтянут в прибыль: {sl_p} -> {self.sl_level}"
                                )
                                break # За один тиковый проход обрабатываем строго одну ступень

                # Выход по Стоп-Лоссу (Фиксируем цену строго по уровню SL плюс проскальзывание)
                if last_trade_price > 0 and last_trade_price >= self.sl_level:
                    exec_p = round(self.sl_level + float(slippage), self.cfg.PRECISION_NUM) 
                    rub_profit = (entry_p - exec_p) * self.cfg.REAL_MULT * pos_size
                    commission = self.cfg.REAL_COMMISSION * pos_size
                    final_rub_profit = rub_profit - commission
                    self.virtual_cash += final_rub_profit
                    self.current_position_size = 0
                    self.tp_level = self.sl_level = None
                    async with asyncio.Lock():
                        asyncio.create_task(self.save_trade_to_csv('SHORT', 'STOP_LOSS', entry_p, exec_p, pos_size, final_rub_profit, float(self.virtual_cash)))
                    return

                # Выход по Тейк-Профиту (Фиксируем цену строго по уровню TP плюс проскальзывание)
                elif last_trade_price > 0 and last_trade_price <= self.tp_level:
                    exec_p = round(self.tp_level + float(slippage), self.cfg.PRECISION_NUM)
                    rub_profit = (entry_p - exec_p) * self.cfg.REAL_MULT * pos_size
                    commission = self.cfg.REAL_COMMISSION * pos_size
                    final_rub_profit = rub_profit - commission
                    self.virtual_cash += final_rub_profit
                    self.current_position_size = 0
                    self.tp_level = self.sl_level = None
                    async with asyncio.Lock():
                        asyncio.create_task(self.save_trade_to_csv('SHORT', 'TAKE_PROFIT', entry_p, exec_p, pos_size, final_rub_profit, float(self.virtual_cash)))
                    return
                
        except Exception as e:
            logging.error(f"Ошибка внутри трейлинга: {e}")
        finally:
            # Снимаем блокировку стакана в любом случае: и при холостом тике, и если внутри методов произошел принудительный return
            self.is_processing_order_book = False
                
    async def main_live_trading(self, qp_stub):
        """
        Главный асинхронный движок живых торгов инструмента.
        """
        from quik_python import Quik  # Импортируем класс для инициализации новых сессий

        logging.info(f"Движок живых торгов запущен для {self.cfg.FUT_SEC_CODE}. Ожидание первичного подключения...")

        while True:
            try:
                # СБРОС КЭША: Защита от входа по тухлым ценам при переподключении
                self.last_known_ask = 0.0
                self.last_known_bid = 0.0
                self.is_processing_order_book = False
                
                # Создаем сетевое подключение к терминалу с нуля
                async with Quik(port=self.cfg.QUIK_PORT) as qp:
                    self.qp_client = qp

                    logging.info(f"[СВЯЗЬ УСТАНОВЛЕНА] Робот переведен в штатный PULL-режим для {self.cfg.FUT_SEC_CODE}.")
        
                    # Вызываем хук первичной синхронизации портфеля при холодном старте
                    await self.sync_position_on_cold_start()
            
                    # Даем планировщику asyncio 200 миллисекунд, чтобы полностью прогрузить 
                    # и активировать порт событий 34151 до того, как начнется выкачка свечей
                    await asyncio.sleep(0.2)
                    await qp.candles.subscribe(self.cfg.FUT_CLASS_CODE, self.cfg.FUT_SEC_CODE, self.M1_INTERVAL)
                    
                    # === ДОБАВИТЬ: ПРИНУДИТЕЛЬНАЯ ПРОГРАММНАЯ ПОДПИСКА НА СТАКАН ===
                    try:
                        # Принудительно заставляем QUIK подписаться на стакан Level 2 по сети брокера
                        await qp.order_book.subscribe(self.cfg.FUT_CLASS_CODE, self.cfg.FUT_SEC_CODE)
                        logging.info(f"[{self.cfg.FUT_SEC_CODE}] Программная подписка на стакан Level 2 успешно активирована.")
                    except Exception as sub_ob_err:
                        logging.warning(f"[{self.cfg.FUT_SEC_CODE}] Не удалось заказать поток стакана: {sub_ob_err}")

                    # Запускаем теперь только ОДИН изолированный таск — для свечей (вход).
                    # Сопровождение (TP/SL) будет вызываться автоматически на каждом входящем тике.
                    candles_task = asyncio.create_task(self._analyze_candles_loop(qp))
                    
                    # Ожидаем выполнения. Если свечной таск упадет по ошибке (break -> Exception), 
                    # цикл прервется, и мы пересоздадим подключение Quik() снаружи с авто-реконнектом.
                    await candles_task

            except Exception as conn_err:
                logging.error(f"[ОШИБКА ПОДКЛЮЧЕНИЯ] Сеть QUIK недоступна для {self.cfg.FUT_SEC_CODE}: {conn_err}. Реконнект через 5 сек...")
                await asyncio.sleep(5)

    async def _analyze_candles_loop(self, qp):
        """Изолированный таск проверки сигналов по закрытым минутным свечам"""
        logging.info(f"[{self.cfg.FUT_SEC_CODE}] Минутный анализатор сигналов входа запущен.")
        while True:
            try:
                await asyncio.sleep(0.5) # Проверка частоты опроса
                
                if self.is_market_clearing_time():
                    continue
                
                try:
                    # Легкий сетевой запрос 3 свечей. Без шлюза секунд он гарантированно 
                    # поймает закрытие бара, даже если сеть моргнула на полминуты.
                    history = await qp.candles.get_last_candles(self.cfg.FUT_CLASS_CODE, self.cfg.FUT_SEC_CODE, self.M1_INTERVAL, 3)
                except Exception as candle_err:
                    logging.warning(f"[{self.cfg.FUT_SEC_CODE}] Сбой сети при запросе истории свечей: {candle_err}. Реконнект...")
                    break 

                if history and isinstance(history, list) and len(history) >= 2:
                    # Закрытый бар — это ВСЕГДА предпоследний элемент в полученной пачке (history[-2])
                    closed_bar = history[-2]

                    try:
                        if hasattr(closed_bar, 'datetime'):
                            c_time = str(closed_bar.datetime)
                        elif isinstance(closed_bar, dict) and 'datetime' in closed_bar:
                            c_time = str(closed_bar['datetime'])
                        else:
                            c_time = str(closed_bar.time if hasattr(closed_bar, 'time') else closed_bar.get('time', ''))
                    except (AttributeError, KeyError, TypeError):
                        continue

                    # При холодном старте или после восстановления связи с QUIK, мы принудительно
                    # принимаем ТЕКУЩУЮ закрытую свечу как уже обработанную.
                    # Преобразуем входящее время свечи из любого текстового формата в чистый объект datetime
                    try:
                        current_bar_dt = pd.to_datetime(c_time)
                    except Exception:
                        continue

                    if self.last_processed_candle_dt is None:
                        self.last_processed_candle_dt = current_bar_dt
                        self.last_entry_candle_dt = current_bar_dt
                        logging.warning(f"[{self.cfg.FUT_SEC_CODE}] [ГОРЯЧАЯ СИНХРОНИЗАЦИЯ] Свечной dt-кэш прошит: {current_bar_dt}")

                    # СИГНАЛЬНЫЙ ШЛЮЗ: Сравниваем объекты datetime напрямую (устойчиво к пробелам и секундам)
                    if current_bar_dt != self.last_processed_candle_dt:
                        
                        try:
                            b_volume = int(closed_bar.volume if hasattr(closed_bar, 'volume') else closed_bar['volume'])
                        except Exception:
                            b_volume = 0

                        if b_volume <= 0:
                            continue

                        b_open = float(closed_bar.open if hasattr(closed_bar, 'open') else closed_bar['open'])
                        b_high = float(closed_bar.high if hasattr(closed_bar, 'high') else closed_bar['high'])
                        b_low = float(closed_bar.low if hasattr(closed_bar, 'low') else closed_bar['low'])
                        b_close = float(closed_bar.close if hasattr(closed_bar, 'close') else closed_bar['close'])

                        raw_volatility = b_high - b_low
                        volatility = round(raw_volatility, self.cfg.PRECISION_NUM)
                        
                        # --- ВЫЧИСЛЕНИЕ ДИНАМИЧЕСКОГО ATR(14) НА ЛЕТУ ---
                        dynamic_slip = self.cfg.SLIPPAGE_POINTS # Резервное значение (дефолт)
                        try:
                            # Запрашиваем 15 свечей (чтобы получить 14 периодов изменений)
                            atr_history = await qp.candles.get_last_candles(self.cfg.FUT_CLASS_CODE, self.cfg.FUT_SEC_CODE, self.M1_INTERVAL, 15)
                            if atr_history and len(atr_history) >= 2:
                                true_ranges = []
                                for i in range(1, len(atr_history)):
                                    prev = atr_history[i-1]
                                    curr = atr_history[i]

                                    # Безопасное извлечение Close предыдущей свечи
                                    if hasattr(prev, 'close'):
                                        p_close = float(prev.close)
                                    elif isinstance(prev, dict) and 'close' in prev:
                                        p_close = float(prev['close'])
                                    else:
                                        continue

                                    # Безопасное извлечение High текущей свечи
                                    if hasattr(curr, 'high'):
                                        c_high = float(curr.high)
                                    elif isinstance(curr, dict) and 'high' in curr:
                                        c_high = float(curr['high'])
                                    else:
                                        continue

                                    # Безопасное извлечение Low текущей свечи
                                    if hasattr(curr, 'low'):
                                        c_low = float(curr.low)
                                    elif isinstance(curr, dict) and 'low' in curr:
                                        c_low = float(curr['low'])
                                    else:
                                        continue

                                    # Вычисляем True Range (Истинный диапазон) по классической формуле Уайлдера
                                    tr = max(c_high - c_low, abs(c_high - p_close), abs(c_low - p_close))
                                    true_ranges.append(tr)
                                
                                # Берем среднее арифметическое (SMA от True Range)
                                calculated_atr = sum(true_ranges) / len(true_ranges)
                                
                                # Корректный расчет кванта изменения цены без погрешностей деления float
                                tick_size = 1.0 if self.cfg.PRECISION_NUM == 0 else float(f"1e-{self.cfg.PRECISION_NUM}")

                                # Расчет сырого значения проскальзывания
                                raw_slip = max(calculated_atr * 0.15, tick_size)

                                # Округляем строго кратно шагу цены и убираем микроскопический float-хвост через round()
                                steps_count = round(raw_slip / tick_size)
                                dynamic_slip = round(steps_count * tick_size, self.cfg.PRECISION_NUM)
                                
                                # Сохраняем живой ATR-слип в кэш класса для боевого робота
                                self.current_atr_slippage = dynamic_slip
                        except Exception as atr_err:
                            logging.warning(f"[{self.cfg.FUT_SEC_CODE}] Не удалось рассчитать ATR на лету: {atr_err}. Используем базовый шаг.")

                        time_hhmm = "00:00"
                        if c_time and len(c_time) >= 16:
                            time_hhmm = c_time[-8:-3] if ":" in c_time[-3:] or ":" in c_time[-6:] else c_time[11:16]
                        else:
                            time_hhmm = c_time[-5:] if len(c_time) >= 5 else c_time

                        logging.info(
                            f"[СВЕЧА ЗАКРЫТА] Время: {time_hhmm} | "
                            f"Волатильность H-L: {volatility:{self.cfg.VOLATILITY_PRECISION_NUM}} | "
                            f"Объем: {b_volume} | Триггер: {self.cfg.TRIGGER_SPREAD}"
                        )

                        # Ищем точку входа, проверяя барьер dt объектов
                        if self.current_position_size == 0 and (self.last_entry_candle_dt is None or current_bar_dt > self.last_entry_candle_dt):
                            
                            # ПРИНУДИТЕЛЬНОЕ ОБНОВЛЕНИЕ: Запрашиваем строго живой стакан без использования кэша
                            try:
                                fresh_ob = await qp.order_book.get_quote_level2(self.cfg.FUT_CLASS_CODE, self.cfg.FUT_SEC_CODE)
                                
                                # Преобразуем к спискам для гарантированной работы индексов коллекций quik_python
                                em_offers = list(fresh_ob.offer) if fresh_ob and fresh_ob.offer else []
                                em_bids = list(fresh_ob.bid) if fresh_ob and fresh_ob.bid else []

                                if len(em_offers) == 0 or len(em_bids) == 0:
                                    logging.warning(f"[{self.cfg.FUT_SEC_CODE}] В момент сигнала стакан пуст. Пропуск входа.")
                                    continue

                                # Вытаскиваем цены всех уровней стакана для безопасного поиска лучших краев
                                all_asks = [float(x.price if hasattr(x, 'price') else x.get('price', 0.0) if isinstance(x, dict) else x) for x in em_offers]
                                all_bids = [float(x.price if hasattr(x, 'price') else x.get('price', 0.0) if isinstance(x, dict) else x) for x in em_bids]

                                # Очищаем массивы от возможных нулевых значений
                                all_asks = [x for x in all_asks if x > 0]
                                all_bids = [x for x in all_bids if x > 0]

                                if not all_asks or not all_bids:
                                    logging.warning(f"[{self.cfg.FUT_SEC_CODE}] Не удалось распарсить цены стакана. Пропуск такта.")
                                    continue

                                # Лучший Аск — это САМАЯ НИЗКАЯ цена продажи на рынке (min)
                                current_ask = min(all_asks)
                                # Лучший Бид — это САМАЯ ВЫСОКАЯ цена покупки на рынке (max)
                                current_bid = max(all_bids)

                                if current_ask <= 0 or current_bid <= 0:
                                    continue

                                # Расчет Midprice строго на основании свежих данных
                                asset_price = round((current_ask + current_bid) / 2, self.cfg.PRECISION_NUM)

                                # Обновляем кэш ТОЛЬКО для параллельного метода трейлинга check_and_trail_position
                                self.last_known_ask = current_ask
                                self.last_known_bid = current_bid

                            except Exception as em_err:
                                logging.error(f"[{self.cfg.FUT_SEC_CODE}] Критический сбой получения стакана для входа: {em_err}")
                                continue

                            # Жесткий барьер: входим только если asset_price рассчитан прямо сейчас
                            if asset_price > 0:
                                
                                # Универсальный парсер ликвидности стакана Мосбиржи (ИСПРАВЛЕНО)
                                def extract_safe_volume(node, backup_vol):
                                    if node is None:
                                        return backup_vol
                                    
                                    # Строго изолируем извлечение индекса [0] только для списков/кортежей
                                    if isinstance(node, (list, tuple)):
                                        target = node[0] if len(node) > 0 else None
                                    else:
                                        target = node

                                    if target is None:
                                        return backup_vol
                                        
                                    if isinstance(target, dict): 
                                        return int(target.get('quantity', target.get('qty', target.get('vol', backup_vol))))
                                    
                                    # Массив полей для 100% совместимости с любыми форками QUIK API
                                    for field in ['quantity', 'qty', 'vol', 'count']:
                                        if hasattr(target, field):
                                            return int(getattr(target, field, backup_vol))
                                    return backup_vol

                            if volatility >= self.cfg.TRIGGER_SPREAD and b_close > b_open:
                                # Напрямую передаем первый элемент отсортированного списка предложений
                                top_offer = em_offers[0] if em_offers else None
                                current_liquidity = extract_safe_volume(top_offer, b_volume)
                                
                                # Для сухого форвард-теста берем объем минутной свечи b_volume как главный фильтр ликвидности актива
                                trade_size = self.calculate_dynamic_size_sync(b_volume)
                                
                                logging.info(f"[{self.cfg.FUT_SEC_CODE}] Целевой лот для входа: {trade_size} (Ликвидность стакана: {current_liquidity} | Свечи: {b_volume})")
                                logging.info(f"[СИГНАЛ LONG] Вход по цене актива (Midprice): {asset_price} (Стакан: {current_bid}/{current_ask})")

                                self.last_entry_candle_time = c_time
                            
                                # Имитируем проскальзывание входа: покупка ХУЖЕ текущей цены стакана
                                worst_entry_price = round(asset_price + dynamic_slip, self.cfg.PRECISION_NUM)

                                # ЖЕСТКИЙ ПАТЧ: Прошиваем барьер объектом datetime ДО асинхронного вызова, полностью блокируя повторный вход
                                self.last_entry_candle_dt = current_bar_dt
                                
                                await self.execute_order_simulation('BUY', worst_entry_price, trade_size, candle_time=c_time)

                                # Фиксируем новое время, закрывая шлюз для повторных входов на этой минуте
                                self.last_processed_candle_time = c_time

                            elif volatility >= self.cfg.TRIGGER_SPREAD and b_close < b_open:
                                # Напрямую передаем первый элемент отсортированного списка спроса
                                top_bid = em_bids[0] if em_bids else None
                                current_liquidity = extract_safe_volume(top_bid, b_volume)
                                
                                # Для сухого форвард-теста берем объем минутной свечи b_volume как главный фильтр ликвидности актива
                                trade_size = self.calculate_dynamic_size_sync(b_volume)
                                
                                logging.info(f"[{self.cfg.FUT_SEC_CODE}] Целевой лот для входа: {trade_size} (Ликвидность стакана: {current_liquidity} | Свечи: {b_volume})")
                                logging.info(f"[СИГНАЛ SHORT] Вход по цене актива (Midprice): {asset_price} (Стакан: {current_bid}/{current_ask})")

                                self.last_entry_candle_time = c_time
                            
                                # Имитируем проскальзывание входа: продажа ХУЖЕ текущей цены стакана
                                worst_entry_price = round(asset_price - dynamic_slip, self.cfg.PRECISION_NUM)

                                # ЖЕСТКИЙ ПАТЧ: Прошиваем барьер объектом datetime ДО асинхронного вызова, полностью блокируя повторный вход
                                self.last_entry_candle_dt = current_bar_dt
                                
                                await self.execute_order_simulation('SELL', worst_entry_price, trade_size, candle_time=c_time)
                                
                                # Фиксируем новое время, закрывая шлюз для повторных входов на этой минуте
                                self.last_processed_candle_time = c_time
            except Exception as ex:
                logging.error(f" Критическая ошибка в таске сигналов {self.cfg.FUT_SEC_CODE}: {ex}")
                await asyncio.sleep(1)
    
    async def sync_position_on_cold_start(self):
        """Хук для переопределения в боевом движке. В сухом тесте не используется."""
        pass