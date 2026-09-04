import os
import asyncio
import logging
import csv
from decimal import Decimal
from datetime import datetime
from core.engine import LiveTradingEngine

# Импортируем чистокровные структуры данных и перечисления из quik_python
from quik_python.data_structures import (
    Order, StopOrder, Operation, TransactionType, StopOrderType, 
    ExecutionCondition, TransactionOperation, StopOrderKind,
    FuturesLimitType, Transaction
)

class QuikEnumStringAdapter:
    """Универсальный адаптер для обхода жесткой сериализации .name в quik_python"""
    def __init__(self, value_str: str):
        self.name = value_str
    def __str__(self):
        return self.name

class RealTradingEngine(LiveTradingEngine):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.account_id = getattr(cfg, 'REAL_ACCOUNT_ID', "")
        # На срочном рынке Мосбиржи FIRM_ID для физических лиц стандартно равен SPBFUT
        self.firm_id = getattr(cfg, 'FUT_CLASS_CODE', "SPBFUT") 
        
        # Боевые регистры контроля транзакций
        self.active_stop_trans_id = None
        self.is_cleaning_in_progress = False
        self.is_stop_modification_in_progress = False  # БОЕВОЙ ЗАМОК: Блокирует тики на время перевыставления ордеров
        self.is_auto_stop_in_progress = False         # ЗАЩИТА ОТ ДУБЛИРОВАНИЯ СТОПОВ НА СТАРТЕ

        logging.info(f"[БОЕВОЙ ДВИЖОК] Архитектура стабилизирована. Выравнивание типов quik_python завершено.")

    def round_by_step(self, price: float) -> str:
        """Универсальное боевое округление до шага цены фьючерса Мосбиржи"""
        try:
            if self.cfg.PRECISION_NUM == 0:
                return str(int(round(price)))
            rounded_val = round(price, self.cfg.PRECISION_NUM)
            return f"{rounded_val:.{self.cfg.PRECISION_NUM}f}"
        except Exception as e:
            logging.error(f"[ОШИБКА ОКРУГЛЕНИЯ] Сбой форматирования цены {price}: {e}")
            return str(price)

    async def get_real_quik_position(self) -> int:
        """Боевой скрытый запрос текущей позиции для регулярного трейлинга"""
        try:
            all_holdings = await self.qp_client.trading.get_futures_client_holdings()
            if all_holdings and isinstance(all_holdings, list):
                for position in all_holdings:
                    pos_dict = position.to_dict() if hasattr(position, 'to_dict') else str(position)
                    sec_code = str(pos_dict.get('sec_code', pos_dict.get('SECCODE', ''))).strip().upper()
                    if sec_code == str(self.cfg.FUT_SEC_CODE).strip().upper():
                        net_val = pos_dict.get('totalnet', pos_dict.get('TOTALNET', pos_dict.get('current_net', 0)))
                        return int(net_val) if net_val is not None else 0
            return 0
        except Exception as e:
            logging.error(f"[ТРЕЙЛИНГ ПОРТФЕЛЯ] Не удалось прочитать текущую позицию из QUIK: {e}")
            return self.current_position_size

    async def get_real_active_stop_order_num(self) -> int:
        """
        Прямой поиск фактического номера активного стоп-ордера (OCO) в таблице QUIK.
        Исключает использование ненадежных локальных TRANS_ID и ручных стоп-лимитов.
        """
        try:
            stop_orders = await self.qp_client.stop_orders.get_stop_orders()

            if not stop_orders:
                logging.info(f"[{self.cfg.FUT_SEC_CODE}] [АУДИТ СТОПОВ] Таблица стоп-заявок пуста (None или []).")
                return None

            if not isinstance(stop_orders, list):
                logging.error(f"[{self.cfg.FUT_SEC_CODE}] [АУДИТ СТОПОВ] Критическая аномалия типов: ожидался list, пришел {type(stop_orders)}")
                return None

            for idx, order in enumerate(stop_orders):
                o_dict = order.to_dict() if hasattr(order, 'to_dict') else str(order)

                sec_code = str(o_dict.get('sec_code', o_dict.get('SECCODE', ''))).strip().upper()
                status = int(o_dict.get('flags', o_dict.get('FLAGS', 0)))
                order_type = int(o_dict.get('stop_order_type', o_dict.get('STOP_ORDER_TYPE', 0)))

                # Каскадный сбор номера заявки по всем возможным ключам QUIK API
                stop_num = o_dict.get('stop_order_num', o_dict.get('STOP_ORDER_NUM', 
                           o_dict.get('order_num', o_dict.get('ORDER_NUM', 
                           o_dict.get('trans_id', o_dict.get('TRANS_ID', None))))))

                is_active = o_dict.get('status', 0) == 1 or (status & 0x1 == 1)

                logging.debug(
                    f"-> Строка №{idx} | Стоп №: {stop_num} | Инструмент: '{sec_code}' "
                    f"| Активен: {is_active} | Тип заявки: {order_type}"
                )

                if sec_code == str(self.cfg.FUT_SEC_CODE).strip().upper():
                    if is_active:
                        if order_type in (6, 9):
                            # Жесткая очистка: проверяем, что номер состоит только из цифр и его int > 0
                            clean_num_str = str(stop_num).strip().lower()
                            if clean_num_str and clean_num_str != "none" and clean_num_str != "false" and clean_num_str != "0":
                                if clean_num_str.isdigit(): 
                                    return int(clean_num_str)
                                else:
                                    logging.error(f"[{self.cfg.FUT_SEC_CODE}] [АУДИТ СТОПОВ ФИЛЬТР] Найдена OCO-строка, но номер содержит грязь: '{stop_num}'. Пропуск.")
                            else:
                                logging.error(f"[{self.cfg.FUT_SEC_CODE}] [АУДИТ СТОПОВ КРИТ] Найден верный OCO-стоп тип {order_type}, но его номер пустой (None).")
                        else:
                            logging.warning(f"[{self.cfg.FUT_SEC_CODE}] [АУДИТ СТОПОВ МИМО] На фьючерсе есть активный стоп №{stop_num}, но его тип равен {order_type} (Ожидался OCO тип 6 или 9). Пропуск.")
                    else:
                        logging.debug(f"[{self.cfg.FUT_SEC_CODE}] Пропуск неактивного стопа №{stop_num}.")

            logging.warning(f"[{self.cfg.FUT_SEC_CODE}] [АУДИТ СТОПОВ] Активный комплексный OCO-стоп на бирже НЕ найден.")
            return None

        except Exception as e:
            logging.error(f"[{self.cfg.FUT_SEC_CODE}] Ошибка при парсинге таблицы стоп-ордеров QUIK: {e}", exc_info=True)
            return None
            
    async def execute_order_simulation(self, operation: str, price: float, quantity: int, candle_time: str = ""):
        if not self.account_id:
            logging.critical("[КРИТИЧЕСКАЯ ОШИБКА] Боевой запуск невозможен: Не указан REAL_ACCOUNT_ID!")
            return

        # Переводим валидацию внутри ручного/боевого входа на сравнение datetime объектов
        input_candle_dt = pd.to_datetime(candle_time) if candle_time else None
        if input_candle_dt and (getattr(self, 'last_executed_signal_dt', None) == input_candle_dt or self.last_entry_candle_dt == input_candle_dt):
            logging.warning(f"[БЛОКИРОВКА ПЕРЕВХОДА] Попытка повторного входа на баре {candle_time} отклонена боевым ядром.")
            return

        if input_candle_dt:
            self.last_executed_signal_dt = input_candle_dt
            self.last_entry_candle_dt = input_candle_dt

        # Выставляем блокирующие флаги
        self.is_stop_modification_in_progress = True
        self.is_auto_stop_in_progress = True
        
        # Записываем метку времени физического завершения входа для фильтрации Сценария №1
        self.entry_execution_end_time = 0.0

        if self.current_position_size == 0:
            self.current_trail_step = -1
            self.tp_level = None
            self.sl_level = None
            self.active_stop_trans_id = None
            logging.warning(f"[{self.cfg.FUT_SEC_CODE}] Торговый контур чист. Ступень трала и целевые кэш-уровни аппаратно занулены.")
        else:
            # Если это добор/долив — сбрасываем только уровни для их гарантированного перерасчета
            self.tp_level = None
            self.sl_level = None
            logging.info(f"[{self.cfg.FUT_SEC_CODE}] Вход выполняется при удерживаемой позиции ({self.current_position_size} лотов). Кэш уровней стерт для перерасчета, ступень трала: {self.current_trail_step}")
        
        quik_dir = Operation.BUY if operation == 'BUY' else Operation.SELL

        # АТОМАРНЫЙ БАРЬЕР ВХОДА: Мгновенно ослепляем тиковый трекер и блокируем свечной движок
        self.is_stop_modification_in_progress = True
        self.is_auto_stop_in_progress = True
        self.is_processing_order_book = True

        if not self.account_id:
            self.is_stop_modification_in_progress = False
            self.is_auto_stop_in_progress = False
            self.is_processing_order_book = False
            logging.critical("[КРИТИЧЕСКАЯ ОШИБКА] Боевой запуск невозможен: Не указан REAL_ACCOUNT_ID!")
            return

        # Переводим валидацию внутри ручного/боевого входа на сравнение datetime объектов
        input_candle_dt = pd.to_datetime(candle_time) if candle_time else None
        if input_candle_dt and (getattr(self, 'last_executed_signal_dt', None) == input_candle_dt or self.last_entry_candle_dt == input_candle_dt):
            self.is_stop_modification_in_progress = False
            self.is_auto_stop_in_progress = False
            self.is_processing_order_book = False
            logging.warning(f"[БЛОКИРОВКА ПЕРЕВХОДА] Попытка повторного входа на баре {candle_time} отклонена боевым ядром.")
            return

        if input_candle_dt:
            self.last_executed_signal_dt = input_candle_dt
            self.last_entry_candle_dt = input_candle_dt

        # Записываем метку времени физического завершения входа для фильтрации Сценария №1
        self.entry_execution_end_time = 0.0

        if self.current_position_size == 0:
            self.current_trail_step = -1
            self.tp_level = None
            self.sl_level = None
            self.active_stop_trans_id = None
            logging.warning(f"[{self.cfg.FUT_SEC_CODE}] Торговый контур чист. Ступень трала и целевые кэш-уровни аппаратно занулены.")
        else:
            self.tp_level = None
            self.sl_level = None
            logging.info(f"[{self.cfg.FUT_SEC_CODE}] Вход выполняется при удерживаемой позиции ({self.current_position_size} лотов). Кэш уровней стерт для перерасчета, ступень трала: {self.current_trail_step}")

        try:
            logging.warning(f"[ОТПРАВКА БОЕВОГО ВХОДА] {operation} | Запрошенный лот: {quantity}")
            
            # 1. Отправляем рыночный приказ
            await self.qp_client.orders.send_order(
                str(self.cfg.FUT_CLASS_CODE),
                str(self.cfg.FUT_SEC_CODE),
                str(self.account_id),
                quik_dir,
                Decimal("0"),
                int(quantity),
                TransactionType.M,
                ExecutionCondition.PUT_IN_QUEUE,
                'Робот_Боевой_Вход'
            )
            
            # 2. МИКРОПАУЗА И ПОВТОРНЫЙ ОПРОС: Даем QUIK время на обновление таблиц (до 2 секунд)
            logging.info("[ШЛЮЗ] Ордер отправлен. Ожидание синхронизации холдингов в QUIK...")
            
            fact_position = 0
            fact_qty = 0
            
            # Делаем 4 попытки опроса каждые 400 мс (суммарно до 1.6 сек ожидания клиринга)
            for attempt in range(4):
                await asyncio.sleep(0.4)
                fact_position = await self.get_real_quik_position()
                fact_qty = abs(fact_position)
                if fact_qty > 0:
                    logging.info(f"[ШЛЮЗ] Позиция успешно подтверждена на попытке №{attempt + 1}: {fact_position} лотов.")
                    break
            
            # 3. ЖЕСТКАЯ КАЛИБРОВКА СБОЯ И ТАЙМАУТА ШЛЮЗА
            if fact_qty == 0:
                # Выставляем глухой замок зачистки ДО вызова исключений и переключения контекста
                self.is_cleaning_in_progress = True 
                logging.critical(f"[{self.cfg.FUT_SEC_CODE}] [ТАЙМАУТ ШЛЮЗА] Позиция не подтверждена за 1.6 сек! Взвод глухого защитного шлюза.")
                
                if candle_time:
                    self.last_entry_candle_time = candle_time
                self.current_position_size = 0
                raise ConnectionResetError(f"[КРИТИЧЕСКИЙ ТАЙМАУТ ШЛЮЗА] Ордер по {self.cfg.FUT_SEC_CODE} завис в QUIK. Экстренное прерывание сессии для защиты от двойного входа.")

            # 4. КОНТРОЛЬ ЛИКВИДНОСТИ И ЧАСТИЧНОГО ИСПОЛНЕНИЯ
            if fact_qty != int(quantity):
                # АТОМАРНЫЙ ЗАМОК: Мгновенно ослепляем тиковый трекер до отправки ордеров
                self.is_cleaning_in_progress = True 
                
                logging.critical(
                    f"[{self.cfg.FUT_SEC_CODE}] [ЧАСТИЧНОЕ ИСПОЛНЕНИЕ] Запрошено лотов: {quantity}, "
                    f"налито по факту: {fact_qty}. Объем некорректен. Запуск экстренной ликвидации огрызка."
                )
                if candle_time:
                    self.last_entry_candle_time = candle_time

                # Синхронизируем регистр памяти с фактом, предотвращая ложные тиковые триггеры
                self.current_position_size = fact_position 
                
                await self.emergency_clean_portfolio(current_tail=fact_position)
                return

            # Фиксируем в памяти реальный боевой объем (если налито строго 100% от запроса)
            self.current_position_size = fact_position
            self.virtual_position_entry_price = price
            
            # 4. Расчет уровней на основе цены исполнения
            if fact_position > 0: # Фактический ЛОНГ
                self.tp_level = round(price + self.cfg.TAKE_PROFIT, self.cfg.PRECISION_NUM)
                self.sl_level = round(price - self.cfg.STOP_LOSS, self.cfg.PRECISION_NUM)
                stop_operation = Operation.SELL
            else: # Фактический ШОРТ
                self.tp_level = round(price - self.cfg.TAKE_PROFIT, self.cfg.PRECISION_NUM)
                self.sl_level = round(price + self.cfg.STOP_LOSS, self.cfg.PRECISION_NUM)
                stop_operation = Operation.BUY
                
            # 5. Инициализация структуры OCO Стоп-Заявки строго на ФАКТИЧЕСКИЙ ОБЪЕМ (fact_qty)
            stop_order = StopOrder()
            stop_order.account = str(self.account_id)
            stop_order.class_code = str(self.cfg.FUT_CLASS_CODE)
            stop_order.sec_code = str(self.cfg.FUT_SEC_CODE)
            stop_order.qty = int(fact_qty) # <--- СТРАХОВКА: Никакого избыточного объема!
            stop_order.operation = stop_operation 
            
            stop_order.stop_order_type = StopOrderType.TAKE_PROFIT_STOP_LIMIT
            stop_order.condition_price = float(self.round_by_step(self.tp_level)) # Активация TP
            
            # Адаптивный расчет: приоритет отдаем живому ATR из кэша, при его отсутствии - берем дефолт
            slippage_val = float(getattr(self, 'current_atr_slippage', float(getattr(self.cfg, 'SLIPPAGE_POINTS', 0.02))))
            
            if stop_operation == Operation.SELL:
                exec_sl_price = self.sl_level - slippage_val
            else:
                exec_sl_price = self.sl_level + slippage_val
                
            stop_order.price = float(self.round_by_step(exec_sl_price))
            stop_order.market_stop_price = "NO" 
            
            stop_order.offset = "0"
            stop_order.spread = "0"
            stop_order.offset_unit = QuikEnumStringAdapter("PRICE_UNITS")
            stop_order.spread_unit = QuikEnumStringAdapter("PRICE_UNITS")
            stop_order.condition_price2 = float(self.round_by_step(self.sl_level)) # Активация SL
            
            # Отправка скорректированной стоп-транзакции
            trans_res = await self.qp_client.stop_orders.create_stop_order(stop_order)
            
            # Защищаем регистр от строкового "None" при первичном входе
            if trans_res and str(trans_res).strip().upper() != "FALSE" and str(trans_res).strip() != "0":
                self.active_stop_trans_id = str(trans_res)
            else:
                self.active_stop_trans_id = None
            
            logging.warning(
                f"[ВЫСТАВЛЕНИЕ КОРРЕКТНОГО СТОПА] ID: {self.active_stop_trans_id} | "
                f"Защищено лотов: {fact_qty} | TP: {self.tp_level} | SL: {self.sl_level}"
            )
            
            # Даем шлюзу 1000 мс, чтобы ордер гарантированно появился в таблицах, исключая гонку тиков
            await asyncio.sleep(1.0)
            self.is_auto_stop_in_progress = False # Снимаем блокировку: вход завершен штатно
            
        except Exception as trade_err:
            logging.critical(f"[{self.cfg.FUT_SEC_CODE}] [КАТАСТРОФА ВХОДА] Ошибка выставления защитной сетки: {trade_err}. Блокировка перевхода.")
            
            # ЖЕСТКИЙ ЗАЩИТНЫЙ РУБЕЖ: Если стоп-система упала, мы принудительно выжигаем
            # текущую сигнальную свечу в барьере перевходов (last_entry_candle_time).
            # Даже если аварийный модуль обнулит позицию, свечной анализатор на этой минуте
            # больше НЕ СМОЖЕТ протиснуться через строку 494 и отправить повторный лот!
            if candle_time:
                self.last_entry_candle_time = candle_time
                
            await self.emergency_clean_portfolio()
        finally:
            self.entry_execution_end_time = asyncio.get_event_loop().time()
            await asyncio.sleep(0.15)
            self.is_stop_modification_in_progress = False
            self.is_auto_stop_in_progress = False
            self.is_processing_order_book = False
            logging.info(f"[{self.cfg.FUT_SEC_CODE}] Все асинхронные замки входа деактивированы. Запущен 3-секундный фильтр стабилизации таблиц.")

    async def save_real_trade_to_csv(self, direction: str, exit_type: str, entry_p: float, exit_p: float, size: int):
        """Асинхронная изолированная запись результатов сделки в real_trading_report.csv"""
        instrument_dir = os.path.dirname(self.cfg.__file__)
        file_name = getattr(self.cfg, 'REAL_REPORT_CSV', 'real_trading_report.csv')
        file_path = os.path.join(instrument_dir, file_name)
        file_exists = os.path.exists(file_path)

        gross_pts = round(exit_p - entry_p if direction == 'LONG' else entry_p - exit_p, self.cfg.PRECISION_NUM)
        rub_profit = gross_pts * self.cfg.REAL_MULT * size
        commission = self.cfg.REAL_COMMISSION * size
        net_pnl_rub = round(rub_profit - commission, self.cfg.PRECISION_NUM_DEPO_RUB)

        row_data = {
            'Timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'Direction': direction,
            'Exit_Type': exit_type,
            'Lots': size,
            'Entry_Price': round(entry_p, self.cfg.PRECISION_NUM),
            'Exit_Price': round(exit_p, self.cfg.PRECISION_NUM),
            'Gross_PnL_Pts': gross_pts,
            'Net_PnL_Rub': net_pnl_rub
        }

        def write_operation():
            with open(file_path, mode='a', newline='', encoding='cp1251') as f:
                writer = csv.DictWriter(f, fieldnames=row_data.keys(), delimiter=';')
                if not file_exists:
                    writer.writeheader()
                writer.writerow(row_data)

        await asyncio.to_thread(write_operation)
        logging.info(f"[БОЕВОЙ ОТЧЕТ ЗАПИСАН] Чистый профит: {net_pnl_rub} руб.")
        
    async def _high_speed_trailing_loop(self, qp):
        """
        Изолированный высокоскоростной таск сопровождения открытых позиций (100 миллисекунд).
        Интегрирован живой трекер накопленного профита и дистанции до следующей ступени трала.
        """
        logging.info(f"[{self.cfg.FUT_SEC_CODE}] Высокоскоростной боевой трекер трала и стакана успешно запущен.")
        
        # Локальный таймер, чтобы лог текущего профита не забивал консоль на каждом тике (вывод раз в 2 секунды)
        last_log_time = 0.0
        
        while True:
            try:
                await asyncio.sleep(0.1) # Частота опроса стакана — 100 мс
                
                if self.is_market_clearing_time():
                    continue
                
                # Высокоскоростной трекер работает непрерывно, позволяя ядру реагировать на изменения статуса позиций
                if True:
                    try:
                        ob = await qp.order_book.get_quote_level2(self.cfg.FUT_CLASS_CODE, self.cfg.FUT_SEC_CODE)
                        if ob and hasattr(ob, 'offer') and hasattr(ob, 'bid') and ob.offer and ob.bid:
                            all_asks = [float(x.price if hasattr(x, 'price') else x.get('price', 0.0) if isinstance(x, dict) else x) for x in ob.offer]
                            all_bids = [float(x.price if hasattr(x, 'price') else x.get('price', 0.0) if isinstance(x, dict) else x) for x in ob.bid]
                            
                            all_asks = [x for x in all_asks if x > 0]
                            all_bids = [x for x in all_bids if x > 0]
                            
                            if all_asks and all_bids:
                                # Атомарно записываем лучшие цены спреда прямо перед расчетом каскада ступеней
                                self.last_known_ask = min(all_asks)
                                self.last_known_bid = max(all_bids)
                                
                                # --- БЛОК РАСЧЕТА НАКОПЛЕННОГО ПРОФИТА НА КАЖДОМ ТИКЕ (Вне таймера) ---
                                midprice = round((self.last_known_ask + self.last_known_bid) / 2, self.cfg.PRECISION_NUM)
                                entry_p = self.virtual_position_entry_price
                                
                                if self.current_position_size > 0: # LONG
                                    current_profit_pts = midprice - entry_p
                                else: # SHORT
                                    current_profit_pts = entry_p - midprice
                                
                                # --- БЛОК МОНИТОРИНГА И ЛОГИРОВАНИЯ (Сдерживается таймером раз в 2 секунды) ---
                                now_loop_time = asyncio.get_event_loop().time()
                                if now_loop_time - last_log_time >= 2.0:
                                    last_log_time = now_loop_time
                                    
                                    # Ищем параметры следующей ступени, если они настроены
                                    if hasattr(self.cfg, 'DYNAMIC_TRAIL_STEPS') and self.cfg.DYNAMIC_TRAIL_STEPS:
                                        next_step_idx = self.current_trail_step + 1
                                        
                                        if next_step_idx < len(self.cfg.DYNAMIC_TRAIL_STEPS):
                                            trigger_pct, _ = self.cfg.DYNAMIC_TRAIL_STEPS[next_step_idx]
                                            tp_dist = round(self.cfg.TAKE_PROFIT, self.cfg.PRECISION_NUM)
                                            
                                            # Целевой профит в пунктах для активации этой ступени
                                            target_profit_pts = round(tp_dist * trigger_pct, self.cfg.PRECISION_NUM)
                                            pts_left = round(target_profit_pts - current_profit_pts, self.cfg.PRECISION_NUM)
                                            
                                            logging.info(
                                                f"[{self.cfg.FUT_SEC_CODE}] ТРЕКЕР: Профит: {current_profit_pts:.{self.cfg.PRECISION_NUM}f} п. | "
                                                f"Ступень №{next_step_idx + 1} (триггер {int(trigger_pct*100)}%): "
                                                f"цель {target_profit_pts:.{self.cfg.PRECISION_NUM}f} п. | До активации: {pts_left:.{self.cfg.PRECISION_NUM}f} п."
                                            )
                                        else:
                                            logging.info(f"[{self.cfg.FUT_SEC_CODE}] ТРЕКЕР: Профит: {current_profit_pts:.{self.cfg.PRECISION_NUM}f} п. | Каскад полностью пройден (высшая ступень).")
                                
                                # Вызываем контур сопровождения с маркером авторизованного прохода
                                await self.check_and_trail_position(from_trailing_loop=True, current_tick_price=midprice)
                                
                    except Exception as net_ob_err:
                        logging.warning(f"[{self.cfg.FUT_SEC_CODE}] Лаг сети при обновлении стакана в трекере: {net_ob_err}")
                        continue
                        
            except Exception as e:
                logging.error(f"Критический сбой в таске боевого трала {self.cfg.FUT_SEC_CODE}: {e}")
                await asyncio.sleep(1)
        
    async def check_and_trail_position(self, from_trailing_loop: bool = False, current_tick_price: float = None):
        """
        ПЕРЕОПРЕДЕЛЕНИЕ ТРЕЙЛИНГА: Контроль стоп-заявок + автовыставление ОСО-стопа при холодном старте.
        Истинное решение дедлока: Каскадная изоляция боевых замков от Race Condition.
        """
        # ЭШЕЛОН 0: Защита интерфейса ядра. Если вызов «слепой» (из старого engine.py) — уходим сразу
        if not from_trailing_loop:
            return

        # ЭШЕЛОН 1: Боевой шлюз биржи. Жесткая изоляция контуров.
        # Переменная active_stop_trans_id удалена отсюда, чтобы не блокировать трейлинг.
        # Она теперь контролируется локально внутри Сценария №1 для защиты от дублирования.
        if (self.is_cleaning_in_progress or 
            self.is_stop_modification_in_progress or 
            self.is_auto_stop_in_progress):

            self.is_processing_order_book = False 
            return

        # ЭШЕЛОН 2: Атомарный замок стакана. Защищает от набивания параллельных тиков, пока ведем расчеты
        if getattr(self, 'is_processing_order_book', False):
            return
            
        self.is_processing_order_book = True

        # ЭШЕЛОНИРОВАННАЯ ЗАЩИТА: Оборачиваем внутреннюю логику в try, гарантируя выполнение finally
        try:
            # КЛЮЧЕВОЙ ФИКС: Опрашиваем ордера и мгновенно гасим флаг входа, если ордер физически встал в стакан биржи!
            current_exchange_stop_num = await self.get_real_active_stop_order_num()
            if current_exchange_stop_num is not None:
                self.active_stop_trans_id = None
                if self.is_auto_stop_in_progress:
                    logging.warning(f"[{self.cfg.FUT_SEC_CODE}] Первичный ОСО-стоп успешно отрисован шлюзом биржи. Замок входа снят штатно.")
                    self.is_auto_stop_in_progress = False

            # Мгновенно сверяем боевую позицию
            real_position = await self.get_real_quik_position()
            old_position = self.current_position_size
            self.current_position_size = real_position

            # --- СЦЕНАРИЙ 1: ХОЛОДНЫЙ СТАРТ (Позиция есть, а биржевого стопа на сервере нет) ---
            if real_position != 0 and self.tp_level is not None and self.sl_level is not None and current_exchange_stop_num is None:
                
                # ЗАЩИТНЫЙ РУБЕЖ (Уязвимость №2): Фильтр отставания локальных таблиц ордеров КВИКа.
                # Если с момента физического завершения метода execute_order_simulation прошло менее 3.0 секунд,
                # мы ОПРЕДЕЛЕННО находимся в зоне лага таблиц. Запрещаем генерировать повторные стопы!
                time_since_entry = asyncio.get_event_loop().time() - getattr(self, 'entry_execution_end_time', 0.0)
                if time_since_entry < 3.0:
                    logging.debug(f"[{self.cfg.FUT_SEC_CODE}] [ФИЛЬТР ТАБЛИЦ] Обнаружен риск гонки таблиц стоп-ордеров (прошло {time_since_entry:.2f}с из 3.0с). Пропуск такта.")
                    self.is_processing_order_book = False
                    return

                # ЖЕСТКИЙ БАРЬЕР (ЗАЩИТА ОТ ДВОЙНОГО СТОПА): Если взведен флаг предстарта 
                # ИЛИ в памяти удерживается строковый ID отправленной, но еще не отрисованной 
                # в таблицах транзакции — МГНОВЕННО выходим, блокируя лавинообразный спам!
                if self.is_auto_stop_in_progress or self.active_stop_trans_id is not None:
                    logging.debug(f"[{self.cfg.FUT_SEC_CODE}] Защита от двойного стопа: активен транзакционный ID {self.active_stop_trans_id} или флаг автостопа. Пропуск такта.")
                    self.is_processing_order_book = False
                    return

                # Мгновенно блокируем шлюз для последующих тиков (вешаем замок)
                self.is_auto_stop_in_progress = True
                
                logging.warning(
                    f"[{self.cfg.FUT_SEC_CODE}] [АВТО-СТОП СТАРТ] Позиция {real_position} удерживается без биржевой защиты. "
                    f"Запуск генерации комплексного OCO-стопа на сервере брокера..."
                )
                try:
                    stop_order = StopOrder()
                    stop_order.account = str(self.account_id)
                    stop_order.class_code = str(self.cfg.FUT_CLASS_CODE)
                    stop_order.sec_code = str(self.cfg.FUT_SEC_CODE)
                    # Защита от частичных исполнений: выставляем объем условного ордера 
                    # строго на текущий фактический биржевой остаток позиции
                    stop_order.qty = int(abs(real_position))
                    # Если мы в шорте — защитный стоп-приказ должен быть на ПОКУПКУ (BUY), и наоборот
                    stop_order.operation = Operation.BUY if real_position < 0 else Operation.SELL
                    
                    stop_order.stop_order_type = StopOrderType.TAKE_PROFIT_STOP_LIMIT
                    stop_order.condition_price = float(self.round_by_step(self.tp_level)) # Активация TP
                    
                    # РЕШЕНИЕ ДЛЯ ХОЛОДНОГО СТАРТА: Автоматический подхват динамического ATR при рестарте робота
                    slippage_val = float(getattr(self, 'current_atr_slippage', float(getattr(self.cfg, 'SLIPPAGE_POINTS', 0.02))))
                    
                    if real_position < 0: # Мы в шорте, защитный ордер на ПОКУПКУ (BUY)
                        exec_sl_price = self.sl_level + slippage_val
                    else: # Мы в лонге, защитный ордер на ПРОДАЖУ (SELL)
                        exec_sl_price = self.sl_level - slippage_val
                        
                    stop_order.price = float(self.round_by_step(exec_sl_price))
                    
                    stop_order.market_stop_price = "NO" 

                    stop_order.offset = "0"
                    stop_order.spread = "0"
                    stop_order.offset_unit = QuikEnumStringAdapter("PRICE_UNITS")
                    stop_order.spread_unit = QuikEnumStringAdapter("PRICE_UNITS")
                    stop_order.condition_price2 = float(self.round_by_step(self.sl_level)) # Активация SL
                    
                    logging.info(f"[{self.cfg.FUT_SEC_CODE}] Отправка транзакции авто-стопа (SL: {self.sl_level} | TP: {self.tp_level})...")
                    trans_res = await self.qp_client.stop_orders.create_stop_order(stop_order)
                    self.active_stop_trans_id = str(trans_res)
                    logging.warning(f"[{self.cfg.FUT_SEC_CODE}] [АВТО-СТОП СТАРТ] Транзакция отправлена: {self.active_stop_trans_id}. Ожидание стабилизации шлюза...")
                    
                    # ШЛЮЗОВОЙ ФИКС ДЕДЛОКА АВТО-СТОПА:
                    # Даем шлюзу QUIK 1000 мс, чтобы ордер гарантированно материализовался в локальных таблицах.
                    # После этого принудительно обнуляем транзакционный ID и снимаем флаг защиты,
                    # предотвращая вечную блокировку Эшелона 1 на последующих тиках трекера!
                    await asyncio.sleep(1.0)
                    self.active_stop_trans_id = None
                    self.is_auto_stop_in_progress = False
                    self.is_processing_order_book = False
                    
                    logging.warning(f"[{self.cfg.FUT_SEC_CODE}] [УСПЕХ] Биржевой ОСО-стоп успешно выставлен и синхронизирован шлюзом!")
                    return
                except Exception as stop_err:
                    logging.error(f"[{self.cfg.FUT_SEC_CODE}] Не удалось автоматически выставить стопы на биржу: {stop_err}")
                    # Сбрасываем флаг ТОЛЬКО в случае физической ошибки отправки транзакции
                    self.is_auto_stop_in_progress = False
                    self.is_processing_order_book = False
                    return

            # --- СЦЕНАРИЙ 2: ШТАТНОЕ ИСПОЛНЕНИЕ (Позиция закрылась по стопу на сервере биржи) ---
            elif real_position == 0 and old_position != 0 and (self.tp_level is not None or self.sl_level is not None):
                exit_price = self.last_known_bid if old_position > 0 else self.last_known_ask
                
                # Логируем аномалию цены, но регистры СБРАСЫВАЕМ в любом случае!
                if exit_price <= 0:
                    logging.warning(f"[{self.cfg.FUT_SEC_CODE}] Позиция обнулилась, но кэш стакана равен 0.0. Пишем сделку по расчетному SL.")
                    exit_price = self.sl_level 
                
                logging.warning(f"[БИРЖЕВОЙ СТОП ПОЛНОСТЬЮ ИСПОЛНЕН] Позиция по {self.cfg.FUT_SEC_CODE} успешно ликвидирована сервером биржи.")
                
                direction_label = 'LONG' if old_position > 0 else 'SHORT'
                
                # ТОТАЛЬНЫЙ БАРЬЕР СЦЕНАРИЯ №2 (ФИКС СТЫКА МИНУТ ПРИ ФИКСАЦИИ СТОПА):
                # Чтобы исключить повторный ложный вход свечного таска на следующем баре,
                # когда индикаторы еще не пересчитались после закрытия позиции, мы берем
                # системное время и превентивно сдвигаем барьер блокировки на 1 минуту вперед.
                from datetime import timedelta
                self.last_entry_candle_dt = datetime.now() + timedelta(minutes=1)
                logging.warning(f"[{self.cfg.FUT_SEC_CODE}] [СИНХРОНИЗАЦИЯ ВЫХОДА] dt-барьер перевхода аппаратно смещен на: {self.last_entry_candle_dt}")

                # ХАРДВЕРНЫЙ ФИКС NAMEERROR (Инициализация точки отсчета ДО зануления):
                saved_entry_price = float(self.virtual_position_entry_price)

                # Жесткое тотальное обнуление памяти — шлюз полностью готов к новому циклу
                self.tp_level = self.sl_level = self.active_stop_trans_id = None
                self.current_trail_step = -1 
                self.is_auto_stop_in_progress = False # ИЗОЛЯЦИЯ ПАМЯТИ ПРИ ЛЕГИТИМНОМ ВЫХОДЕ

                # СБРОС ЗАМОК СТАКАНА ДО AWAIT: Гарантирует, что свечной таск во время паузы записи CSV 
                # не затрет флаги следующей (новой) торговой сессии в общем блоке finally метода трейлинга
                self.is_processing_order_book = False

                # Изолируем дисковый ввод-вывод через await: планировщик гарантированно 
                # завершит операцию в текущем тиковом такте до того, как свечной таск пойдет на проверку сигналов
                await self.save_real_trade_to_csv(
                    direction_label, 'AUTO_EXCHANGE_STOP', 
                    saved_entry_price, exit_price, abs(old_position)
                )
                return
            
            # --- СЦЕНАРИИ ДЛЯ АКТИВНОЙ ПОЗ РЫНКА (РОДИТЕЛЬСКИЙ ШЛЮЗ СОПРОВОЖДЕНИЯ) ---
            elif real_position != 0 and self.tp_level is not None and self.sl_level is not None:
                # ВСЯ логика сопровождения должна находиться СТРОГО внутри этого elif блока!
                if current_exchange_stop_num is None:
                    if self.is_auto_stop_in_progress:
                        return
                    return

                if self.is_auto_stop_in_progress:
                    self.is_auto_stop_in_progress = False

                last_trade_price = current_tick_price if current_tick_price is not None else round((self.last_known_ask + self.last_known_bid) / 2, self.cfg.PRECISION_NUM)
                if last_trade_price <= 0:
                    return

                entry_p = self.virtual_position_entry_price
                tp_dist = round(self.cfg.TAKE_PROFIT, self.cfg.PRECISION_NUM)
                pos_size = abs(real_position)

                current_profit_pts = last_trade_price - entry_p if real_position > 0 else entry_p - last_trade_price

                if hasattr(self.cfg, 'DYNAMIC_TRAIL_STEPS') and self.cfg.DYNAMIC_TRAIL_STEPS:
                    best_eligible_step_idx = -1
                    best_new_sl = self.sl_level
                    stop_operation = Operation.SELL if real_position > 0 else Operation.BUY
                    
                    # Сканируем матрицу ступеней
                    for step_idx, (trigger_pct, stop_pct) in enumerate(self.cfg.DYNAMIC_TRAIL_STEPS):
                        if step_idx <= self.current_trail_step:
                            continue
                        
                        target_trigger = round(tp_dist * trigger_pct, self.cfg.PRECISION_NUM)
                        if current_profit_pts >= target_trigger:
                            if real_position > 0: # LONG
                                calc_sl = round(entry_p + tp_dist * stop_pct, self.cfg.PRECISION_NUM)
                                if calc_sl > best_new_sl:
                                    best_new_sl = calc_sl
                                    best_eligible_step_idx = step_idx
                            else: # SHORT
                                calc_sl = round(entry_p - tp_dist * stop_pct, self.cfg.PRECISION_NUM)
                                if calc_sl < best_new_sl:
                                    best_new_sl = calc_sl
                                    best_eligible_step_idx = step_idx

                    # ИСПРАВЛЕНИЕ ПРИОРИТЕТА: Если зафиксировано интрабар-пробитие TP/SL, выполняем МГНОВЕННЫЙ ШТАТНЫЙ ВЫХОД
                    is_long_exit_zone = real_position > 0 and (last_trade_price >= self.tp_level or last_trade_price <= self.sl_level)
                    is_short_exit_zone = real_position < 0 and (last_trade_price <= self.tp_level or last_trade_price >= self.sl_level)

                    if is_long_exit_zone or is_short_exit_zone:
                        exit_type_label = "TAKE_PROFIT" if (real_position > 0 and last_trade_price >= self.tp_level) or (real_position < 0 and last_trade_price <= self.tp_level) else "STOP_LOSS"
                        logging.warning(f"[{self.cfg.FUT_SEC_CODE}] [ИНТРАБАР ПРОБИТИЕ] Цена {last_trade_price} пересекла уровень {exit_type_label}. Мгновенная активация штатного закрытия...")
                        
                        # Сразу взводим замок зачистки, полностью блокируя тиковый трекер
                        self.is_cleaning_in_progress = True
                        await self.emergency_clean_portfolio(current_tail=real_position)
                        return
                    else:
                        # Если зоны выхода нет, штатно выполняем перестановку ступеней условного OCO-ордера
                        if best_eligible_step_idx > self.current_trail_step and best_eligible_step_idx != -1:
                            calculated_step = best_eligible_step_idx
                            
                            # ПРЕВЕНТИВНЫЙ БАРЬЕР ТРАЛА: немедленно фиксируем намерение перехода в памяти,
                            # полностью исключая повторный вход тиков в блок модификации на время сетевой паузы
                            self.current_trail_step = calculated_step
                            self.is_stop_modification_in_progress = True
                            
                            logging.warning(
                                f"[{self.cfg.FUT_SEC_CODE}] БОЕВОЙ ТРАЛ: Блокировка тиков. Запуск модификации каскада. "
                                f"Переход на Ступень №{calculated_step + 1}. Новый расчетный SL: {best_new_sl}"
                            )
                            await asyncio.shield(
                                self._execute_physical_stop_modification(stop_operation, best_new_sl, pos_size, calculated_step)
                            )
                            return

                # --- ПОД-СЦЕНАРИЙ №3: ФОНОВЫЙ АСИНХРОННЫЙ АУДИТОР ЗАВИСШИХ ПОЗИЦИЙ ---
                # Срабатывает исключительно тогда, когда позиция на бирже удерживается (real_position != 0), 
                # но комплексный стоп-ордер физически пропал из локальных таблиц терминала (current_exchange_stop_num is None)
                if current_exchange_stop_num is None and not self.is_auto_stop_in_progress:
                    snapshot_entry_time = getattr(self, 'entry_execution_end_time', 0.0)
                    
                    # ЖЕСТКИЙ ПАТЧ: Вместо тяжелого общего флага взводим локальный атомарный замок аудитора
                    if getattr(self, 'is_audit_task_active', False):
                        self.is_processing_order_book = False
                        return
                        
                    self.is_audit_task_active = True
                    
                    async def delayed_tail_clean():
                        try:
                            await asyncio.sleep(2.5) # Даем сетевому шлюзу QUIK 2.5 секунды на прогрузку буфера лимитов
                            
                            # ЖЕСТКИЙ АРХИТЕКТУРНЫЙ ФИЛЬТР СЕССИЙ: Если за время паузы робот успел перезайти, гасим таск
                            if snapshot_entry_time != getattr(self, 'entry_execution_end_time', 0.0):
                                return
                                
                            final_pos = await self.get_real_quik_position()
                            if final_pos != 0:
                                # Опрашиваем таблицы строго один раз по истечении таймаута стабилизации
                                check_stop_existence = await self.get_real_active_stop_order_num()
                                if check_stop_existence is not None:
                                    logging.info(f"[{self.cfg.FUT_SEC_CODE}] [ФОНОВЫЙ АУДИТ] Лаг таблиц QUIK ликвидирован. Стоп №{check_stop_existence} найден на сервере. Сброс тревоги.")
                                    return
                                    
                                logging.critical(f"[{self.cfg.FUT_SEC_CODE}] [ФОНОВЫЙ АУДИТ] ПОДТВЕРЖДЕНО КЛИРИНГОМ: обнаружен незащищенный хвост позиций ({final_pos} лотов) без стоп-заявок! Запуск рубильника...")
                                await self.emergency_clean_portfolio(current_tail=final_pos)
                        finally:
                            # Гарантированно очищаем локальный замок аудита, освобождая высокоскоростной трекер
                            self.is_audit_task_active = False
                            
                    # Запускаем таск в фоновом потоке asyncio планировщика
                    asyncio.create_task(delayed_tail_clean())
                    
                    # МГНОВЕННЫЙ ВЫХОД: Прерываем текущий 100-миллисекундный тиковый такт. 
                    # Это спасает робота от падения в нижележащей линейной математике расчета каскадов трала.
                    return 

        except Exception as e:
            # Включаем exc_info=True: если упадет математика — выдаст полную трассировку строк в консоль
            logging.error(f"[{self.cfg.FUT_SEC_CODE}] КРИТИЧЕСКИЙ СБОЙ ВНУТРИ ТРЕЙЛИНГА: {e}", exc_info=True)
        finally:
            # ГАРАНТИРОВАННАЯ ЗАЧИСТКА РЕГИСТРОВ ОТ RACE CONDITION
            self.is_processing_order_book = False

    async def emergency_clean_portfolio(self, current_tail: int = None):
        """МОДУЛЬ АВАРИЙНОЙ ПОДЧИСТКИ: Экстренная нейтрализация рисков"""
        if self.is_cleaning_in_progress:
            return
        self.is_cleaning_in_progress = True

        try:
            tail_lots = current_tail if current_tail is not None else await self.get_real_quik_position()

            # 1. Снимаем активную комплексную стоп-заявку по реальному номеру
            real_stop_num = await self.get_real_active_stop_order_num()

            if real_stop_num:
                logging.info(f"[СНЯТИЕ СТОПОВ] Снятие комплексной стоп-заявки по реальному биржевому номеру: {real_stop_num}")
                try:
                    # Прямой низкоуровневый пакет отмены для QUIK Lua шлюза
                    from quik_python.data_structures import Transaction
                    from quik_python.data_structures import TransactionAction

                    kill_trans_obj = Transaction()
                    kill_trans_obj.ACTION = TransactionAction.KILL_STOP_ORDER
                    kill_trans_obj.CLASSCODE = str(self.cfg.FUT_CLASS_CODE)
                    kill_trans_obj.SECCODE = str(self.cfg.FUT_SEC_CODE)
                    kill_trans_obj.ACCOUNT = str(self.account_id)
                    kill_trans_obj.STOP_ORDER_KEY = str(real_stop_num)
                    
                    await self.qp_client.trading.send_transaction(kill_trans_obj)
                    logging.info(f"[СНЯТИЕ СТОПОВ] Приказ на аварийную отмену стопа {real_stop_num} успешно исполнен.")
                except Exception as k_err:
                    logging.warning(f"[СЕТЕВОЕ ПРЕДУПРЕЖДЕНИЕ] Не удалось отменить стоп-заявку {real_stop_num}: {k_err}")
            else:
                logging.info(f"[СНЯТИЕ СТОПОВ] Активных стоп-заявок для отмены не обнаружено.")

            # 2. Принудительно бьем по рынку встречным лотом для мгновенного закрытия
            if tail_lots != 0:
                clean_dir = Operation.SELL if tail_lots > 0 else Operation.BUY
                clean_quantity = abs(tail_lots)

                logging.warning(f"[РЫНОЧНЫЙ УДАР ПО ХВОСТУ] Направление: {clean_dir} | Объем: {clean_quantity}")

                # Строгое позиционное соответствие 9 аргументам send_order()
                await self.qp_client.orders.send_order(
                    str(self.cfg.FUT_CLASS_CODE),
                    str(self.cfg.FUT_SEC_CODE),
                    str(self.account_id),
                    clean_dir, 
                    Decimal("0"), 
                    int(clean_quantity),
                    TransactionType.M, 
                    ExecutionCondition.PUT_IN_QUEUE, 
                    'Robot_Emergency'  # <-- Строго 15 символов. Защита от ограничений QUIK Lua
                )

                direction_label = 'LONG' if tail_lots > 0 else 'SHORT'
                exit_price = self.last_known_bid if tail_lots > 0 else self.last_known_ask
                await self.save_real_trade_to_csv(direction_label, 'EMERGENCY_MARKET_CLEAN', self.virtual_position_entry_price, exit_price, clean_quantity)
                
                # ЖЕСТКИЙ СИНХРОННЫЙ БАРЬЕР: Ожидаем физического клиринга позиции в QUIK
                # Мы делаем до 10 попыток опроса каждые 200 мс (суммарно до 2 секунд контроля),
                # удерживая флаг is_cleaning_in_progress = True. Ни один параллельный таск 
                # не сможет протиснуться и открыть новые лоты, пока биржа не подтвердит ноль!
                confirmed_zero = False
                for attempt in range(10):
                    await asyncio.sleep(0.2)
                    fact_pos = await self.get_real_quik_position()
                    if fact_pos == 0:
                        confirmed_zero = True
                        logging.warning(f"[СИНХРОНИЗАЦИЯ ЗАЧИСТКИ] Биржа подтвердила обнуление портфеля на попытке №{attempt + 1}.")
                        break
                        
                if not confirmed_zero:
                    # ЗАЩИТНЫЙ РУБЕЖ: Запрещаем занулять позицию в памяти, если биржа не подтвердила факт закрытия!
                    logging.critical(f"[КАТАСТРОФА ШЛЮЗА] Ордер зачистки отправлен, но позиция по {self.cfg.FUT_SEC_CODE} на бирже равна {fact_pos} лотов! АВАРИЙНЫЙ ОСТАНОВ ИНСТРУМЕНТА.")
                    self.current_position_size = fact_pos  # Записываем фактический остаток рисков
                    raise SystemError(f"[КРИТИЧЕСКИЙ СБОЙ] Ручная или биржевая блокировка зачистки по {self.cfg.FUT_SEC_CODE}. Скрипт остановлен для спасения депо.")

                # Если ноль подтвержден — штатно очищаем память
                self.current_position_size = 0
                self.tp_level = self.sl_level = self.active_stop_trans_id = None
                
                # ЖЕСТКАЯ БЛОКИРОВКА СВЕЧНОГО ДВИЖКА (ЗАЩИТА ОТ ДВУХ ОПЕРАЦИЙ):
                # Принудительно заносим время последней обработанной свечи в барьер.
                # Теперь, даже если аварийный модуль закроет позицию за 200 мс и отключит замки,
                # свечной таск на этой минуте физически не сможет совершить повторный ложный вход!
                if getattr(self, 'last_processed_candle_dt', None) is not None:
                    self.last_entry_candle_dt = self.last_processed_candle_dt
                    
                logging.warning(f"[УСПЕШНАЯ ЗАЧИСТКА] Боевой портфель по инструменту {self.cfg.FUT_SEC_CODE} полностью очищен. Свечной шлюз заблокирован.")

        except Exception as fatal_err:
            logging.critical(f"[КАТАСТРОФИЧЕСКИЙ СБОЙ] Робот не смог очистить позицию самостоятельно! Ошибка: {fatal_err}")
            self.tp_level = self.sl_level = self.active_stop_trans_id = None
            raise ConnectionAbortedError(f"[СЕТЕВОЙ ТАЙМАУТ ШЛЮЗА] Срыв связи в момент зачистки фьючерса {self.cfg.FUT_SEC_CODE} для предотвращения двойного удара.")
        finally:
            # Гарантируем избыточную микропаузу перед открытием шлюзов для тикового трекера
            await asyncio.sleep(0.1)
            self.is_cleaning_in_progress = False
            
    async def main_live_trading(self, qp_stub):
        """БОЕВОЙ ДВИЖОК: Главный асинхронный цикл с приоритетным прогревом стакана"""
        from quik_python import Quik
        logging.info(f"[БОЕВОЙ ШЛЮЗ] Запуск цикла живых торгов для {self.cfg.FUT_SEC_CODE}...")
        
        while True:
            try:
                # Мгновенный сброс кэша рисков при переподключениях
                self.last_known_ask = 0.0
                self.last_known_bid = 0.0
                self.is_processing_order_book = False
                self.is_stop_modification_in_progress = False
                self.is_cleaning_in_progress = False
                
                async with Quik(port=self.cfg.QUIK_PORT) as qp:
                    self.qp_client = qp
                    logging.info(f"[СВЯЗЬ УСТАНОВЛЕНА] Боевой робот подключен к порту {self.cfg.QUIK_PORT}")
                    
                    # ШАГ 1: Принудительно заказываем стакан Level 2 в QUIK до синхронизации позиций
                    try:
                        await qp.order_book.subscribe(self.cfg.FUT_CLASS_CODE, self.cfg.FUT_SEC_CODE)
                        logging.info(f"[{self.cfg.FUT_SEC_CODE}] Программная подписка на стакан Level 2 успешно активирована.")
                    except Exception as sub_err:
                        logging.warning(f"[{self.cfg.FUT_SEC_CODE}] Не удалось подписаться на стакан: {sub_err}")
                    
                    # Даем QUIK 200мс на прокачку первых сетевых пакетов котировок от брокера
                    await asyncio.sleep(0.2)
                    
                    # ШАГ 2: Вызываем хук холодной синхронизации (теперь стакан гарантированно прогреется)
                    await self.sync_position_on_cold_start()
                    
                    # ШАГ 3: Подписываемся на минутные свечи для генерации сигналов входа
                    await qp.candles.subscribe(self.cfg.FUT_CLASS_CODE, self.cfg.FUT_SEC_CODE, self.M1_INTERVAL)
                    
                    # Изолированный параллельный запуск задач
                    candles_task = asyncio.create_task(self._analyze_candles_loop(qp))
                    trailing_task = asyncio.create_task(self._high_speed_trailing_loop(qp))

                    try:
                        # Ожидаем падения ПЕРВОГО из тасков (срыв связи сокета)
                        done, pending = await asyncio.wait(
                            [candles_task, trailing_task],
                            return_when=asyncio.FIRST_COMPLETED
                        )
                    finally:
                        # Жесткая зачистка памяти: принудительно убиваем все фоновые потоки сопровождения,
                        # полностью исключая появление параллельно работающих "зомби-циклов"
                        for task in [candles_task, trailing_task]:
                            if not task.done():
                                task.cancel()
                                try:
                                    await task
                                except asyncio.CancelledError:
                                    pass
                        logging.info(f"[{self.cfg.FUT_SEC_CODE}] Все асинхронные таски прошлой сессии уничтожены. Память стерильна.")
                    
            except Exception as conn_err:
                logging.error(f"[ОШИБКА БОЕВОГО ШЛЮЗА] Сбой сети для {self.cfg.FUT_SEC_CODE}: {conn_err}. Реконнект через 5 сек...")
                await asyncio.sleep(5)

    async def sync_position_on_cold_start(self):
        """БОЕВАЯ СИНХРОНИЗАЦИЯ: Восстановление интрадей-сетки уровней при холодном старте с глубокой отладкой и прогревом стакана"""
        logging.warning(f"[ХОЛОДНЫЙ СТАРТ] Инициализация хука синхронизации портфеля для {self.cfg.FUT_SEC_CODE}...")
        
        # ======================================================================
        # БЛОК 1: ПРИНУДИТЕЛЬНЫЙ ПРЕДВАРИТЕЛЬНЫЙ ПРОГРЕВ КЭША ЦЕН СТАКАНА
        # ======================================================================
        try:
            logging.info(f"[{self.cfg.FUT_SEC_CODE}] [СТАКАН ПРОГРЕВ] Отправка сетевого запроса get_quote_level2()...")
            init_ob = await self.qp_client.order_book.get_quote_level2(self.cfg.FUT_CLASS_CODE, self.cfg.FUT_SEC_CODE)
            
            if init_ob and hasattr(init_ob, 'offer') and hasattr(init_ob, 'bid') and init_ob.offer and init_ob.bid:
                # Безопасно вытаскиваем цены всех уровней стакана
                all_asks = [float(x.price if hasattr(x, 'price') else x.get('price', 0.0) if isinstance(x, dict) else x) for x in init_ob.offer]
                all_bids = [float(x.price if hasattr(x, 'price') else x.get('price', 0.0) if isinstance(x, dict) else x) for x in init_ob.bid]
                
                # Очищаем массивы цен от биржевых нулей
                all_asks = [x for x in all_asks if x > 0]
                all_bids = [x for x in all_bids if x > 0]
                
                if all_asks and all_bids:
                    self.last_known_ask = min(all_asks)
                    self.last_known_bid = max(all_bids)
                    logging.info(f"[{self.cfg.FUT_SEC_CODE}] [СТАКАН ПРОГРЕВ] Боевой кэш цен успешно инициализирован: {self.last_known_bid} / {self.last_known_ask}")
                else:
                    logging.warning(f"[{self.cfg.FUT_SEC_CODE}] [СТАКАН ПРОГРЕВ] Цены в стакане некорректны (asks: {len(all_asks)}, bids: {len(all_bids)})")
            else:
                logging.warning(f"[{self.cfg.FUT_SEC_CODE}] [СТАКАН ПРОГРЕВ] Получен пустой снимок стакана Level 2 от QUIK.")
        except Exception as ob_warm_err:
            logging.error(f"[{self.cfg.FUT_SEC_CODE}] [КРИТИКА СТАКАНА] Не удалось прогреть кэш цен: {ob_warm_err}")

        # ======================================================================
        # БЛОК 2: ПОЛНАЯ ОРИГИНАЛЬНАЯ ДИАГНОСТИКА ХОЛДИНГОВ И МАТЕМАТИКИ РОБОТА
        # ======================================================================
        try:
            logging.info("[ХОЛОДНЫЙ СТАРТ] Отправка сетевого запроса get_futures_client_holdings()...")
            all_holdings = await self.qp_client.trading.get_futures_client_holdings()

            if not all_holdings:
                logging.error("[ХОЛОДНЫЙ СТАРТ] Сервер QUIK вернул пустой ответ (None/Empty) на запрос таблицы холдингов!")
                logging.info(f"[{self.cfg.FUT_SEC_CODE}] Открытых позиций на бирже не обнаружено. Робот начинает работу с нуля.")
                return

            if not isinstance(all_holdings, list):
                logging.error(f"[ХОЛОДНЫЙ СТАРТ] Критическая аномалия типов: ожидался list, пришел {type(all_holdings)}")
                return

            logging.info(f"[ХОЛОДНЫЙ СТАРТ] Успешно получена таблица. Всего строк для анализа: {len(all_holdings)}")

            instrument_found = False
            for i, position in enumerate(all_holdings):
                pos_dict = position.to_dict() if hasattr(position, 'to_dict') else str(position)
                sec_code = str(pos_dict.get('sec_code', pos_dict.get('SECCODE', ''))).strip().upper()

                if sec_code == str(self.cfg.FUT_SEC_CODE).strip().upper():
                    instrument_found = True
                    logging.warning(f"[ХОЛОДНЫЙ СТАРТ] Есть совпадение по инструменту {self.cfg.FUT_SEC_CODE}!")

                    # 1. ПЕРВООЧЕРЕДНОЕ ИЗВЛЕЧЕНИЕ СРЕДНЕЙ ЦЕНЫ ВХОДА
                    entry_p_raw = pos_dict.get('avrposnprice', pos_dict.get('AVRPOSNPRICE', 0.0))
                    if not entry_p_raw or float(entry_p_raw) <= 0:
                        logging.error(f"[МАТЕМАТИКА РОБОТА] Сбой: Средняя цена входа из QUIK некорректна ({entry_p_raw}). Сетка не построена.")
                        return
                    
                    entry_p = float(entry_p_raw)
                    self.virtual_position_entry_price = round(entry_p, self.cfg.PRECISION_NUM)

                    # 2. ИЗВЛЕЧЕНИЕ ОБЪЕМА
                    net_val = pos_dict.get('totalnet', pos_dict.get('TOTALNET', pos_dict.get('current_net', 0)))
                    self.current_position_size = int(net_val) if net_val is not None else 0
                    
                    if self.current_position_size == 0:
                        logging.warning(f"[ХОЛОДНЫЙ СТАРТ] Строка найдена, но биржевой объем равен 0. Робот стартует с нуля.")
                        return

                    # 3. ЭКСТРЕННЫЙ АУДИТ И ВЫСТАВЛЕНИЕ СТОПОВ ПО ИНИЦИАЛИЗИРОВАННОЙ ЦЕНЕ
                    logging.info(f"[{self.cfg.FUT_SEC_CODE}] [ХОЛОДНЫЙ СТАРТ] Проверка наличия защитных ордеров на сервере брокера...")
                    check_stop_start = await self.get_real_active_stop_order_num()

                    if check_stop_start is not None:
                        logging.warning(f"[{self.cfg.FUT_SEC_CODE}] [ХОЛОДНЫЙ СТАРТ] Позиция защищена ордером №{check_stop_start}.")
                    else:
                        logging.critical(f"[{self.cfg.FUT_SEC_CODE}] [ХОЛОДНЫЙ СТАРТ] Обнаружена повальная позиция БЕЗ защиты! Выставляем OCO-стоп...")
                        self.is_auto_stop_in_progress = True
                        try:
                            stop_order = StopOrder()
                            stop_order.account = str(self.account_id)
                            stop_order.class_code = str(self.cfg.FUT_CLASS_CODE)
                            stop_order.sec_code = str(self.cfg.FUT_SEC_CODE)
                            stop_order.qty = int(abs(self.current_position_size))
                            stop_order.operation = Operation.BUY if self.current_position_size < 0 else Operation.SELL
                            stop_order.stop_order_type = StopOrderType.TAKE_PROFIT_STOP_LIMIT

                            tp_dist = round(self.cfg.TAKE_PROFIT, self.cfg.PRECISION_NUM)
                            if self.current_position_size > 0:
                                self.tp_level = round(entry_p + tp_dist, self.cfg.PRECISION_NUM)
                                self.sl_level = round(entry_p - self.cfg.STOP_LOSS, self.cfg.PRECISION_NUM)
                                exec_sl_price = self.sl_level - float(getattr(self.cfg, 'SLIPPAGE_POINTS', 0.02))
                            else:
                                self.tp_level = round(entry_p - tp_dist, self.cfg.PRECISION_NUM)
                                self.sl_level = round(entry_p + self.cfg.STOP_LOSS, self.cfg.PRECISION_NUM)
                                exec_sl_price = self.sl_level + float(getattr(self.cfg, 'SLIPPAGE_POINTS', 0.02))

                            stop_order.condition_price = float(self.round_by_step(self.tp_level))
                            stop_order.price = float(self.round_by_step(exec_sl_price))
                            stop_order.market_stop_price = "NO"
                            stop_order.offset = stop_order.spread = "0"
                            stop_order.offset_unit = stop_order.spread_unit = QuikEnumStringAdapter("PRICE_UNITS")
                            stop_order.condition_price2 = float(self.round_by_step(self.sl_level))

                            trans_res = await self.qp_client.stop_orders.create_stop_order(stop_order)
                            if trans_res and str(trans_res).strip().upper() != "FALSE" and str(trans_res).strip() != "0":
                                self.active_stop_trans_id = str(trans_res)
                            else:
                                self.active_stop_trans_id = None
                            logging.warning(f"[{self.cfg.FUT_SEC_CODE}] [ХОЛОДНЫЙ СТАРТ] Защитный стоп успешно отправлен. ID: {self.active_stop_trans_id}")
                            
                            # КЛЮЧЕВОЙ ИСПРАВЛЕНИЕ: Даем шлюзу время, выходим из метода.
                            # Высокоскоростной тиковый трекер сам подхватит сопровождение через Сценарий №1!
                            await asyncio.sleep(1.0)
                            return
                            
                        except Exception as cold_err:
                            logging.error(f"[ХОЛОДНЫЙ СТАРТ КРИТ] Ошибка выставления стопа: {cold_err}")
                            return
                        finally:
                            self.is_auto_stop_in_progress = False
                    # -----------------------------------------------------------
                    # Ниже идет код БЕЗ дублирования, если стоп НА БИРЖЕ ИЗНАЧАЛЬНО СУЩЕСТВОВАЛ:
                    direction = 'LONG' if self.current_position_size > 0 else 'SHORT'
                    logging.info(f"[{self.cfg.FUT_SEC_CODE}] [АНАЛИЗ ЦЕНЫ] Позиция защищена. Выполняем автоподхват уровней трала...")
                    entry_p = pos_dict.get('avrposnprice', pos_dict.get('AVRPOSNPRICE', 0.0))
                    logging.info(f"[АНАЛИЗ ЦЕНЫ] Получено значение из QUIK: {entry_p} (тип: {type(entry_p)})")
                    
                    if entry_p and float(entry_p) > 0:
                        entry_p = float(entry_p)
                        self.virtual_position_entry_price = round(entry_p, self.cfg.PRECISION_NUM)
                        logging.warning(f"[МАТЕМАТИКА РОБОТА] Базовая точка отсчета (Entry Price) принята: {self.virtual_position_entry_price}")
                        
                        logging.info(f"[МАТЕМАТИКА РОБОТА] Считывание настроек сетки: TP={self.cfg.TAKE_PROFIT}, SL={self.cfg.STOP_LOSS}")
                        
                        # Явно объявляем дистанцию тейк-профита для корректных расчетов
                        tp_dist = round(self.cfg.TAKE_PROFIT, self.cfg.PRECISION_NUM)

                        # Шаг 1: Расчет базовых уровней сетки в зависимости от направления холдинга
                        if self.current_position_size > 0: # ЛОНГ
                            self.tp_level = round(entry_p + tp_dist, self.cfg.PRECISION_NUM)
                            self.sl_level = round(entry_p - self.cfg.STOP_LOSS, self.cfg.PRECISION_NUM)
                        else: # ШОРТ
                            self.tp_level = round(entry_p - tp_dist, self.cfg.PRECISION_NUM)
                            self.sl_level = round(entry_p + self.cfg.STOP_LOSS, self.cfg.PRECISION_NUM)

                        # Шаг 2: БОЕВОЙ АДАПТЕР ХОЛОДНОГО ТРАЛА (Рассчитываем накопленный профит на лету)
                        if self.last_known_ask > 0 and self.last_known_bid > 0:
                            midprice = round((self.last_known_ask + self.last_known_bid) / 2, self.cfg.PRECISION_NUM)
                            current_profit_pts = midprice - entry_p if self.current_position_size > 0 else entry_p - midprice
                            
                            if hasattr(self.cfg, 'DYNAMIC_TRAIL_STEPS') and self.cfg.DYNAMIC_TRAIL_STEPS:
                                logging.warning(f"[{self.cfg.FUT_SEC_CODE}] [АУДИТ ТРАЛА] Анализ рынка при запуске. Текущий профит: {current_profit_pts} п.")
                                
                                for step_idx, (trigger_pct, stop_pct) in enumerate(self.cfg.DYNAMIC_TRAIL_STEPS):
                                    target_trigger = round(tp_dist * trigger_pct, self.cfg.PRECISION_NUM)
                                    
                                    if current_profit_pts >= target_trigger:
                                        # Рассчитываем новый подтянутый Стоп-Лосс для авто-стопа
                                        if self.current_position_size > 0: # LONG
                                            new_sl = round(entry_p + tp_dist * stop_pct, self.cfg.PRECISION_NUM)
                                            if new_sl > self.sl_level:
                                                self.sl_level = new_sl
                                                self.current_trail_step = step_idx
                                        else: # SHORT
                                            new_sl = round(entry_p - tp_dist * stop_pct, self.cfg.PRECISION_NUM)
                                            if new_sl < self.sl_level:
                                                self.sl_level = new_sl
                                                self.current_trail_step = step_idx
                                                
                                if self.current_trail_step != -1:
                                    logging.warning(
                                        f"[{self.cfg.FUT_SEC_CODE}] [АУДИТ ТРАЛА УСПЕХ] Робот запущен в зоне прибыли! "
                                        f"Авто-подхват Ступени №{self.current_trail_step + 1}. Уровень комплексного SL смещен на: {self.sl_level}"
                                    )

                        # Шаг 3: Красивый финальный лог верификации параметров
                        logging.warning(
                            f"\n===========================================================\n"
                            f"[СИНХРОНИЗАЦИЯ УСПЕШНА] ВСЕ ПАРАМЕТРЫ СДЕЛКИ ВОССТАНОВЛЕНЫ!\n"
                            f"-> Инструмент: {self.cfg.FUT_SEC_CODE}\n"
                            f"-> Фирма (firmid): {pos_dict.get('firmid', 'NOT_FOUND')}\n"
                            f"-> Счет (trdaccid): {pos_dict.get('trdaccid', 'NOT_FOUND')}\n"
                            f"-> Направление: {direction} ({abs(self.current_position_size)} лотов)\n"
                            f"-> Средняя цена входа (из холдинга): {self.virtual_position_entry_price}\n"
                            f"-> Расчетный Take-Profit: {self.tp_level}\n"
                            f"-> Итоговый Stop-Loss (с учетом трала): {self.sl_level}\n"
                            f"==========================================================="
                        )
                        return
                    else:
                        logging.error(f"[МАТЕМАТИКА РОБОТА] Сбой: Средняя цена входа из QUIK некорректна ({entry_p}). Сетка не построена.")
                        return

            if not instrument_found:
                logging.info(f"[{self.cfg.FUT_SEC_CODE}] В таблице холдингов нет совпадений. Робот начинает работу с чистого листа.")

        except Exception as e:
            logging.error(f"[ОШИБКА ЦИКЛА СИНХРОНИЗАЦИИ] Критическое падение хука холодного старта: {e}")
            
    def is_market_clearing_time(self) -> bool:
        """
        БОЕВОЙ ВАЛИДАТОР СЕССИИ: Синхронизирует круглосуточный цикл с check_moex_trading_allowed.
        Автоматически блокирует минутный анализатор в выходные и в периоды ночного клиринга.
        """
        from main import check_moex_trading_allowed
        return not check_moex_trading_allowed()
        
    async def _execute_physical_stop_modification(self, stop_operation, new_sl_level: float, pos_size: int, step_idx: int):
        """
        Внутренний изолированный контур физической модификации OCO-стопа на сервере брокера.
        Снимает старую стоп-заявку, выдерживает паузу и выставляет новую.
        """
        # Включаем жесткий атомарный замок от Race Condition на быстрых тиках
        self.is_stop_modification_in_progress = True
        
        # Гарантированно уничтожаем старый кэш транзакции, переводя его в системный None тип
        self.active_stop_trans_id = None
        
        try:
            logging.warning(
                f"[БОЕВОЙ ТРАЛ] Инициирована физическая перезапись OCO-стопа для {self.cfg.FUT_SEC_CODE}. "
                f"Целевой SL: {new_sl_level}"
            )
            
            # Шаг А: Снятие активного OCO-стопа на сервере биржи по его реальному номеру
            logging.info(f"[{self.cfg.FUT_SEC_CODE}] [БОЕВОЙ ТРАЛ] Поиск старого ордера для его отмены...")
            real_stop_num = await self.get_real_active_stop_order_num()
            
            # Страховочный эшелон: если QUIK лагает, даем ему 100 мс и опрашиваем таблицы повторно
            if real_stop_num is None:
                logging.warning(f"[{self.cfg.FUT_SEC_CODE}] [БОЕВОЙ ТРАЛ] Ордер не найден на первом такте. Пауза 100мс на лаг таблиц...")
                await asyncio.sleep(0.1)
                real_stop_num = await self.get_real_active_stop_order_num()
                
            if real_stop_num:
                try:
                    logging.warning(f"[{self.cfg.FUT_SEC_CODE}] [БОЕВОЙ ТРАЛ] Отправка приказа на снятие старого стоп-ордера №{real_stop_num}...")
                    
                    from quik_python.data_structures import Transaction
                    from quik_python.data_structures import TransactionAction
                    
                    kill_trans_obj = Transaction()
                    kill_trans_obj.ACTION = TransactionAction.KILL_STOP_ORDER
                    kill_trans_obj.CLASSCODE = str(self.cfg.FUT_CLASS_CODE)
                    kill_trans_obj.SECCODE = str(self.cfg.FUT_SEC_CODE)
                    kill_trans_obj.ACCOUNT = str(self.account_id)
                    kill_trans_obj.STOP_ORDER_KEY = str(real_stop_num)
                    
                    await self.qp_client.trading.send_transaction(kill_trans_obj)
                    logging.info(f"[{self.cfg.FUT_SEC_CODE}] [БОЕВОЙ ТРАЛ] Приказ на отмену стопа {real_stop_num} ушел. Ожидаем подтверждения от КВИК...")
                    
                    # 🔥 ШЛЮЗОВОЙ ЦИКЛ СИНХРОНИЗАЦИИ: Ждем физического удаления заявки из таблиц брокера
                    confirmed_deletion = False
                    for attempt in range(15):  # 15 попыток * 200 мс = до 3 секунд глубокого мониторинга
                        await asyncio.sleep(0.2)
                        check_num = await self.get_real_active_stop_order_num()
                        
                        # Если функция вернула другой номер или None — старый ордер успешно уничтожен биржей
                        if check_num != real_stop_num:
                            confirmed_deletion = True
                            logging.info(f"[{self.cfg.FUT_SEC_CODE}] [БОЕВОЙ ТРАЛ СИНХРО] Подтверждено! Ордер {real_stop_num} удален на попытке №{attempt + 1}.")
                            break
                            
                    if not confirmed_deletion:
                        logging.error(f"[{self.cfg.FUT_SEC_CODE}] [БОЕВОЙ ТРАЛ КРИТ] Биржа зависла! Стоп {real_stop_num} не снялся за 3 сек. Выставление заблокировано во избежание дублирования!")
                        return  # Мгновенно прерываем такт модификации. Замок снимется автоматически в блоке finally
                        
                except Exception as kill_err:
                    logging.error(f"[{self.cfg.FUT_SEC_CODE}] [БОЕВОЙ ТРАЛ] Ошибка при снятии старого стопа {real_stop_num}: {kill_err}. Модификация прервана.")
                    return
            else:
                logging.critical(
                    f"[{self.cfg.FUT_SEC_CODE}] [БОЕВОЙ ТРАЛ АНОМАЛИЯ] Активная стоп-заявка не найдена в QUIK! Выставляем новый стоп поверх пустоты."
                )
                # Микропауза 100мс для окончательной стабилизации буфера лимитов
                await asyncio.sleep(0.1)

            # Шаг В: Сборка новой структуры комплексного OCO Стоп-Заявки
            stop_order = StopOrder()
            stop_order.account = str(self.account_id)
            stop_order.class_code = str(self.cfg.FUT_CLASS_CODE)
            stop_order.sec_code = str(self.cfg.FUT_SEC_CODE)
            stop_order.qty = int(pos_size)  # Выставляем строго на фактический текущий объем холдинга
            stop_order.operation = stop_operation
            # Указываем тип, который на Срочном рынке Мосбиржи гарантированно регистрируется как тип 9
            stop_order.stop_order_type = StopOrderType.TAKE_PROFIT_STOP_LIMIT
            
            # Тейк-Профит остается неизменным, как в первоначальном ордере
            stop_order.condition_price = float(self.round_by_step(self.tp_level))

            # Расчет цены лимитной части Стоп-Лосса с учетом нативного проскальзывания
            slippage_val = float(getattr(self, 'current_atr_slippage', float(getattr(self.cfg, 'SLIPPAGE_POINTS', 0.02))))
            if stop_operation == Operation.SELL:
                exec_sl_price = new_sl_level - slippage_val
            else:
                exec_sl_price = new_sl_level + slippage_val

            stop_order.price = float(self.round_by_step(exec_sl_price))
            stop_order.market_stop_price = "NO"  # Запрет рыночного проскальзывания лимитки брокером
            stop_order.offset = "0"
            stop_order.spread = "0"
            stop_order.offset_unit = QuikEnumStringAdapter("PRICE_UNITS")
            stop_order.spread_unit = QuikEnumStringAdapter("PRICE_UNITS")
            
            # Активируем новый подтянутый в безубыток/прибыль уровень Стоп-Лосса (+20%)
            stop_order.condition_price2 = float(self.round_by_step(new_sl_level))

            # Шаг Г: Отправка измененной транзакции в торговую систему
            trans_res = await self.qp_client.stop_orders.create_stop_order(stop_order)
            
            trans_status = str(trans_res).strip().upper() if trans_res is not None else ""
            is_transaction_ok = trans_res and trans_status != "0" and trans_status != "FALSE" and trans_status != ""
            
            if is_transaction_ok:
                # Пишем строковый ID транзакции ТОЛЬКО если она реально создана биржей
                self.active_stop_trans_id = str(trans_res)
                self.sl_level = new_sl_level 
                self.current_trail_step = step_idx 
                logging.warning(
                    f"[БОЕВОЙ ТРАЛ УСПЕШНО ВНЕДРЕН] Инструмент: {self.cfg.FUT_SEC_CODE} | "
                    f"Новая ступень зафиксирована на сервере биржи. Текущий SL: {self.sl_level} | ID транзакции: {self.active_stop_trans_id}"
                )
            else:
                # Если биржа отклонила ордер, пишем честный Python-None тип, а не текст "None"
                self.active_stop_trans_id = None
                logging.critical(f"[{self.cfg.FUT_SEC_CODE}] КАТАСТРОФА ТРАЛА: Биржа отклонила транзакцию нового стопа! Старый стоп уже снят! Запуск экстренной очистки...")
                
                # ВЫСТАВЛЯЕМ ГЛУХОЙ БЛОКИРУЮЩИЙ ЗАМОК:
                # Удерживаем замок модификации активным. Метод аварийной зачистки обнулит позицию.
                # Мы принудительно прерываем выполнение через return. Блок finally снимет флаг,
                # но перед этим мы засыпаем, гарантируя, что ни один параллельный свечной таск
                # не успеет проскочить шлюз в момент смены минутных баров!
                await self.emergency_clean_portfolio()
                await asyncio.sleep(2.0)
                return

        except Exception as e:
            logging.error(f"[КРИТИЧЕСКИЙ СБОЙ МОДИФИКАЦИИ ТРАЛА] На {self.cfg.FUT_SEC_CODE}: {e}")
        finally:
            # ЖЕСТКИЙ ПАТЧ: Сбрасываем флаг модификации при ЛЮБОМ исходе, возвращая трекер к жизни
            self.is_stop_modification_in_progress = False
            # Гарантированно очищаем сопутствующий замок стакана для обработки следующего тика
            self.is_processing_order_book = False