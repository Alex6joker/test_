import os
import sys
import asyncio
import logging
import importlib.util
import argparse

sys.path.append(os.path.abspath(os.path.dirname(__file__)))
import core
from core.engine import LiveTradingEngine
from core.real_engine import RealTradingEngine
from quik_python import Quik


def load_instrument_config(folder_name):
    """Динамически загружает конфиг из папки выбранного фьючерса."""
    config_path = os.path.join(folder_name, "config.py")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Критическая ошибка: Конфигурация не найдена по пути {config_path}")
    spec = importlib.util.spec_from_file_location("dynamic_config", config_path)
    cfg = importlib.util.module_from_spec(spec)
    sys.modules["dynamic_config"] = cfg
    spec.loader.exec_module(cfg)
    return cfg


def check_moex_trading_allowed() -> bool:
    from datetime import datetime, time
    now_dt = datetime.now()
    now_time = now_dt.time()
    weekday = now_dt.weekday()
    if now_time >= time(23, 50, 0) or now_time < time(0, 30, 0):
        logging.error("[БЛОКИРОВКА СЕССИИ] Срочный рынок закрыт на плановый ночной клиринг ЕТС (23:50 - 00:30 мск).")
        return False
    if 0 <= weekday <= 4:
        if now_time < time(7, 0, 0):
            logging.error("[БЛОКИРОВКА СЕССИИ] Торговый день еще не начался. Старт аукциона открытия в 06:50 мск.")
            return False
        return True
    if now_time < time(10, 0, 0) or now_time >= time(19, 0, 0):
        logging.error("[БЛОКИРОВКА СЕССИИ] Сессия выходного дня закрыта. Время работы шлюзов: 09:50 - 19:00 мск.")
        return False
    return True


def check_moex_pretrading_allowed() -> bool:
    from datetime import datetime, time
    now_dt = datetime.now()
    now_time = now_dt.time()
    weekday = now_dt.weekday()
    if now_time >= time(23, 50, 0) or now_time < time(0, 30, 0):
        logging.error("[БЛОКИРОВКА СЕССИИ] Срочный рынок закрыт на плановый ночной клиринг ЕТС (23:50 - 00:30 мск).")
        return False
    if 0 <= weekday <= 4:
        if now_time < time(6, 50, 0):
            logging.error("[БЛОКИРОВКА СЕССИИ] Торговый день еще не начался. Старт аукциона открытия в 06:50 мск.")
            return False
        return True
    if now_time < time(9, 50, 0) or now_time >= time(19, 0, 0):
        logging.error("[БЛОКИРОВКА СЕССИИ] Сессия выходного дня закрыта. Время работы шлюзов: 09:50 - 19:00 мск.")
        return False
    return True


async def start_live_robot(cfg):
    if not check_moex_pretrading_allowed():
        return
    engine = LiveTradingEngine(cfg)
    async with Quik(port=cfg.QUIK_PORT) as qp:
        await engine.main_live_trading(qp)


async def start_real_robot(cfg):
    if not check_moex_pretrading_allowed():
        return
    engine = RealTradingEngine(cfg)
    async with Quik(port=cfg.QUIK_PORT) as qp:
        await engine.main_live_trading(qp)


def start_application():
    parser = argparse.ArgumentParser(description="Мульти-инструментальный торговый робот для QUIK (Фьючерсы MOEX)")
    parser.add_argument('--work-mode', type=str, choices=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"], help='Режим работы робота (1-10)')
    parser.add_argument('--selected-forts', type=str, help='Имя рабочей папки инструмента (например, 01_SILVER)')
    args = parser.parse_args()

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
        print("10 - Выполнить ПОЛНЫЙ CAUSAL AUDIT backtest (Trail / SL / TP)")
        mode_choice = input("Введите цифру режима: ").strip()
        if mode_choice not in ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]:
            print("Ошибка: Неверный режим.")
            return
        if mode_choice == "10":
            from backtest_audit import run_causal_audit
            log_path = input("Введите путь к backtest_diagnostic.log (Enter = logs/backtest_diagnostic.log): ").strip()
            if not log_path:
                log_path = os.path.join("logs", "backtest_diagnostic.log")
            try:
                run_causal_audit(log_path)
            except Exception as e:
                print(f"[CAUSAL AUDIT] Критическая ошибка: {e}")
            return
        instrument_folder = input("Введите имя папки инструмента (например, 01_SILVER): ").strip()

    if mode_choice == "10":
        from backtest_audit import run_causal_audit
        log_path = os.path.join("logs", "backtest_diagnostic.log")
        try:
            run_causal_audit(log_path)
        except Exception as e:
            print(f"[CAUSAL AUDIT] Критическая ошибка: {e}")
        return

    try:
        cfg = load_instrument_config(instrument_folder)
    except Exception as e:
        print(e)
        return

    if mode_choice == "1":
        import backtester
        backtester.run_instrument_backtest(instrument_folder, cfg)
    elif mode_choice == "2":
        import optimize
        optimize.run_optimization("CSV_PATH_ADD_1MONTH_PATH", "TEST_OPTIMIZE_CSV_PATH_1MONTH_PATH", instrument_folder, cfg)
        return
    elif mode_choice == "3":
        import optimize
        optimize.run_optimization("CSV_PATH_ADD_4MONTH_PATH", "TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH", instrument_folder, cfg)
        return
    elif mode_choice == "4":
        import optimize
        optimize.run_optimization("CSV_PATH_ADD_STRESS_PERIOD", "TEST_OPTIMIZE_CSV_PATH_STRESS_PERIOD", instrument_folder, cfg)
        return
    elif mode_choice == "5":
        try:
            asyncio.run(start_live_robot(cfg))
        except KeyboardInterrupt:
            print(f"\n[СИСТЕМА] Робот для {cfg.FUT_SEC_CODE} принудительно остановлен пользователем.")
    elif mode_choice == "6":
        if not (args.work_mode and args.selected_forts):
            confirm = input(f"ВНИМАНИЕ! Вы запускаете БОЕВОЙ РЕЖИМ по инструменту {cfg.FUT_SEC_CODE}.\nВы уверены? (y/n): ").strip().lower()
            if confirm not in ['д', 'y', 'yes']:
                print("Запуск боевого робота отменен.")
                return
        else:
            logging.warning("[БЕЗОПАСНОСТЬ] Боевой контур запущен автоматически через CLI. Защитный шлюз подтверждения пройден.")
        try:
            asyncio.run(start_real_robot(cfg))
        except KeyboardInterrupt:
            print(f"\n[СИСТЕМА] Боевой робот для {cfg.FUT_SEC_CODE} экстренно остановлен пользователем.")
    elif mode_choice == "7":
        from trail.trail_generator import run_standalone_trail_generation
        run_standalone_trail_generation("CSV_PATH_ADD_1MONTH_PATH", "TEST_OPTIMIZE_CSV_PATH_1MONTH_PATH", cfg)
        return
    elif mode_choice == "8":
        from trail.trail_generator import run_standalone_trail_generation
        run_standalone_trail_generation("CSV_PATH_ADD_4MONTH_PATH", "TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH", cfg)
        return
    elif mode_choice == "9":
        from trail.trail_generator import run_standalone_trail_generation
        run_standalone_trail_generation("CSV_PATH_ADD_STRESS_PERIOD", "TEST_OPTIMIZE_CSV_PATH_STRESS_PERIOD", cfg)
        return


if __name__ == '__main__':
    start_application()
