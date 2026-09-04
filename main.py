import os
import sys
import asyncio
import logging
import importlib.util
import argparse

# Принудительно подключаем ядро core и форк backtrader
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
import core

# Импортируем готовые классы из пакета core
from core.engine import LiveTradingEngine
from core.real_engine import RealTradingEngine
from quik_python import Quik

def load_instrument_config(folder_name):
    """Динамически загружает конфиг из папки выбранного фьючерса"""
    config_path = os.path.join(folder_name, "config.py")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Критическая ошибка: Конфигурация не найдена по пути {config_path}")

    spec = importlib.util.spec_from_file_location("dynamic_config", config_path)
    cfg = importlib.util.module_from_spec(spec)
    sys.modules["dynamic_config"] = cfg
    spec.loader.exec_module(cfg)
    return cfg

def check_moex_trading_allowed() -> bool:
    """Проверяет доступность торгов на Срочном рынке Мосбиржи (ЕТС 2026)"""
    from datetime import datetime, time
    now_dt = datetime.now()
    now_time = now_dt.time()
    weekday = now_dt.weekday()  # 0-Понедельник, ..., 6-Воскресенье

    # Тотальный барьер: Ночной клиринг ЕТС Mark-to-market (Торги остановлены абсолютно всегда)
    if now_time >= time(23, 50, 0) or now_time < time(0, 30, 0):
        logging.error(f"[БЛОКИРОВКА СЕССИИ] Срочный рынок закрыт на плановый ночной клиринг ЕТС (23:50 - 00:30 мск).")
        return False

    # Расписание для будних дней (Понедельник — Пятница: 06:50 - 23:50)
    if 0 <= weekday <= 4:
        if now_time < time(7, 0, 0):
            logging.error(f"[БЛОКИРОВКА СЕССИИ] Торговый день еще не начался. Старт аукциона открытия в 06:50 мск.")
            return False
        return True

    # Расписание для выходных дней (Суббота — Воскресенье: 09:50 - 19:00)
    else:
        if now_time < time(10, 0, 0) or now_time >= time(19, 0, 0):
            logging.error(f"[БЛОКИРОВКА СЕССИИ] Сессия выходного дня закрыта. Время работы шлюзов: 09:50 - 19:00 мск.")
            return False
        return True
        
def check_moex_pretrading_allowed() -> bool:
    """Проверяет доступность торгов на Срочном рынке Мосбиржи (ЕТС 2026)"""
    from datetime import datetime, time
    now_dt = datetime.now()
    now_time = now_dt.time()
    weekday = now_dt.weekday()  # 0-Понедельник, ..., 6-Воскресенье

    # Тотальный барьер: Ночной клиринг ЕТС Mark-to-market (Торги остановлены абсолютно всегда)
    if now_time >= time(23, 50, 0) or now_time < time(0, 30, 0):
        logging.error(f"[БЛОКИРОВКА СЕССИИ] Срочный рынок закрыт на плановый ночной клиринг ЕТС (23:50 - 00:30 мск).")
        return False

    # Расписание для будних дней (Понедельник — Пятница: 06:50 - 23:50)
    if 0 <= weekday <= 4:
        if now_time < time(6, 50, 0):
            logging.error(f"[БЛОКИРОВКА СЕССИИ] Торговый день еще не начался. Старт аукциона открытия в 06:50 мск.")
            return False
        return True

    # Расписание для выходных дней (Суббота — Воскресенье: 09:50 - 19:00)
    else:
        if now_time < time(9, 50, 0) or now_time >= time(19, 0, 0):
            logging.error(f"[БЛОКИРОВКА СЕССИИ] Сессия выходного дня закрыта. Время работы шлюзов: 09:50 - 19:00 мск.")
            return False
        return True

async def start_live_robot(cfg):
    """Инициализирует коннектор QUIK и запускает изолированный движок инструмента"""
    if not check_moex_pretrading_allowed():
        return
        
    engine = LiveTradingEngine(cfg)
    async with Quik(port=cfg.QUIK_PORT) as qp:
        await engine.main_live_trading(qp)

async def start_real_robot(cfg):
    """Инициализирует коннектор QUIK и запускает БОЕВОЙ движок на реальном счете"""
    if not check_moex_pretrading_allowed():
        return
        
    engine = RealTradingEngine(cfg)
    async with Quik(port=cfg.QUIK_PORT) as qp:
        await engine.main_live_trading(qp)

def start_application():
    # Инициализируем парсер аргументов командной строки
    parser = argparse.ArgumentParser(description="Мульти-инструментальный торговый робот для QUIK (Фьючерсы MOEX)")
    parser.add_argument('--work-mode', type=str, choices=["1", "2", "3", "4", "5", "6", "7", "8", "9"], help='Режим работы робота (1-9)')
    parser.add_argument('--selected-forts', type=str, help='Имя рабочей папки инструмента (например, 01_SILVER)')
    args = parser.parse_args()

    # Гибридная логика: CLI-аргументы имеют приоритет над ручным вводом
    if args.work_mode and args.selected_forts:
        mode_choice = args.work_mode
        instrument_folder = args.selected_forts
        print(f"\n[АВТОЗАПУСК] Режим: {mode_choice} | Инструмент: {instrument_folder}")
    else:
        print("\n=== МУЛЬТИ-ИНСТРУМЕНТАЛЬНЫЙ ТОРГОВЫЙ РОБОТ ===")
        print("Доступные режимы:")
        print(" 1 - Запустить ИСТОРИЧЕСКИЙ БЭКТЕСТ (Один файл на проект)")
        print(" 2 - Запустить МАСШТАБНУЮ ОПТИМИЗАЦИЮ ПАРАМЕТРОВ (Многопоточно) 1 месяц")
        print(" 3 - Запустить МАСШТАБНУЮ ОПТИМИЗАЦИЮ ПАРАМЕТРОВ (Многопоточно) 4 месяца")
        print(" 4 - Запустить МАСШТАБНУЮ ОПТИМИЗАЦИЮ ПАРАМЕТРОВ (Многопоточно) Стресс-тест / Распил (Отрезок жесткого боковика 2-3 недели)")
        print(" 5 - Запустить ЖИВОЙ СУХОЙ ТЕСТ В СТАКАНЕ (Один файл на проект)")
        print(" 6 - ЗАПУСТИТЬ БОЕВУЮ ТОРГОВЛЮ НА РЕАЛЬНОМ СЧЕТЕ (!!!НАСТОЯЩИЕ ДЕНЬГИ)")
        print(" 7 - Запустить глобальную оптимизацию сеток Трейлинг-Стопа 1 месяц")
        print(" 8 - Запустить глобальную оптимизацию сеток Трейлинг-Стопа 4 месяца")
        print(" 9 - Запустить глобальную оптимизацию сеток Трейлинг-Стопа Стресс-тест / Распил (Отрезок жесткого боковика 2-3 недели)")

        mode_choice = input("Введите цифру режима: ").strip()

        if mode_choice not in ["1", "2", "3", "4", "5", "6", "7", "8", "9"]:
            print("Ошибка: Неверный режим.")
            return

        # Для всех режимов робот интерактивно запрашивает рабочую папку здесь
        instrument_folder = input("Введите имя папки инструмента (например, 01_SILVER): ").strip()

    try:
        # Загружаем конфиг выбранного инструмента
        cfg = load_instrument_config(instrument_folder)
    except Exception as e:
        print(e)
        return

    # ИСТОРИЧЕСКИЙ БЭКТЕСТ (Один файл на проект)
    if mode_choice == "1":
        import backtester
        backtester.run_instrument_backtest(instrument_folder, cfg)

    # Если выбрана оптимизация, у неё свой внутренний интерактивный опрос папки
    elif mode_choice == "2":
        import optimize
        optimize.run_optimization("CSV_PATH_ADD_1MONTH_PATH", "TEST_OPTIMIZE_CSV_PATH_1MONTH_PATH", instrument_folder, cfg)
        return

    # Если выбрана оптимизация, у неё свой внутренний интерактивный опрос папки
    elif mode_choice == "3":
        import optimize
        optimize.run_optimization("CSV_PATH_ADD_4MONTH_PATH", "TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH", instrument_folder, cfg)
        return

    # Если выбрана оптимизация, у неё свой внутренний интерактивный опрос папки
    elif mode_choice == "4":
        import optimize
        optimize.run_optimization("CSV_PATH_ADD_STRESS_PERIOD", "TEST_OPTIMIZE_CSV_PATH_STRESS_PERIOD", instrument_folder, cfg)
        return

    # Запускаем асинхронное ядро живых торгов с динамическим конфигом, сухой тест
    elif mode_choice == "5":
        try:
            asyncio.run(start_live_robot(cfg))
        except KeyboardInterrupt:
            print(f"\n[СИСТЕМА] Робот для {cfg.FUT_SEC_CODE} принудительно остановлен пользователем.")
    
    # Боевой робот
    elif mode_choice == "6":
        # Если запуск автоматический (из терминала), защитный запрос на ручное подтверждение пропускается
        if not (args.work_mode and args.selected_forts):
            confirm = input(f"ВНИМАНИЕ! Вы запускаете БОЕВОЙ РЕЖИМ по инструменту {cfg.FUT_SEC_CODE}.\nВы уверены? (y/n): ").strip().lower()
            if confirm not in ['д', 'y', 'yes']:
                print("Запуск боевого робота отменен.")
                return
        else:
            logging.warning(f"[БЕЗОПАСНОСТЬ] Боевой контур запущен автоматически через CLI. Защитный шлюз подтверждения пройден.")
        try:
            asyncio.run(start_real_robot(cfg))
        except KeyboardInterrupt:
            print(f"\n[СИСТЕМА] Боевой робот для {cfg.FUT_SEC_CODE} экстренно остановлен пользователем.")
            
    elif mode_choice == "7": 
        # Импортируем наш вынесенный инструмент калькулятора-генератора сеток трала
        from trail.trail_generator import run_standalone_trail_generation
        
        # Передаем управление и загруженный объект конфигурации фьючерса в изолированный модуль
        run_standalone_trail_generation("CSV_PATH_ADD_1MONTH_PATH", "TEST_OPTIMIZE_CSV_PATH_1MONTH_PATH", cfg)
        
        # Принудительно возвращаем управление в интерфейс, исключая падение по цепочке кода
        return
            
    elif mode_choice == "8": 
        # Импортируем наш вынесенный инструмент калькулятора-генератора сеток трала
        from trail.trail_generator import run_standalone_trail_generation
        
        # Передаем управление и загруженный объект конфигурации фьючерса в изолированный модуль
        run_standalone_trail_generation("CSV_PATH_ADD_4MONTH_PATH", "TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH", cfg)
        
        # Принудительно возвращаем управление в интерфейс, исключая падение по цепочке кода
        return
            
    elif mode_choice == "9": 
        # Импортируем наш вынесенный инструмент калькулятора-генератора сеток трала
        from trail.trail_generator import run_standalone_trail_generation
        
        # Передаем управление и загруженный объект конфигурации фьючерса в изолированный модуль
        run_standalone_trail_generation("CSV_PATH_ADD_STRESS_PERIOD", "TEST_OPTIMIZE_CSV_PATH_STRESS_PERIOD", cfg)
        
        # Принудительно возвращаем управление в интерфейс, исключая падение по цепочке кода
        return

if __name__ == '__main__':
    start_application()