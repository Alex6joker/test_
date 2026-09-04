import os
import sys
import uuid
import pandas as pd
import backtrader as bt
import multiprocessing
import importlib.util
import math
from concurrent.futures import ProcessPoolExecutor
import time  # Добавьте в самый верх файла к остальным импортам

# Импортируем компоненты из ядра
from core.backtest_engine import RealisticFuturesStrategy, ContractVolumeAnalyzer

# Глобальный контекст для передачи настроек в воркеры (избегаем ошибок сериализации)
_worker_cfg = None

def init_worker(cfg_path):
    """Инициализатор процессов: подгружает форк backtrader и конфиг фьючерса в каждый поток"""
    import core.patch_backtrader
    global _worker_cfg
    spec = importlib.util.spec_from_file_location("worker_config", cfg_path)
    _worker_cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_worker_cfg)

def run_single_backtest(params):
    """Один бэктест внутри изолированного процесса с учетом динамической сетки трала"""
    # Распаковываем кортеж из 5 элементов (добавлен trail_grid)
    trigger, tp, sl, trail_grid, processed_path = params
    global _worker_cfg

    try:
        cerebro = bt.Cerebro(maxcpus=1)
        cerebro.addstrategy(
            RealisticFuturesStrategy, 
            trigger=trigger, 
            tp=tp, 
            sl=sl, 
            risk=_worker_cfg.OFFER_RISK,
            real_mult=_worker_cfg.REAL_MULT,
            real_margin=_worker_cfg.REAL_MARGIN,
            safety_factor=_worker_cfg.SAFETY_FACTOR,
            precision_num=_worker_cfg.PRECISION_NUM,
            dynamic_trail_steps=trail_grid
        )
        
        data = bt.feeds.GenericCSVData(
            dataname=processed_path, sep=',', dtformat='%Y%m%d %H%M%S',
            timeframe=bt.TimeFrame.Minutes, datetime=0, time=-1,
            open=1, high=2, low=3, close=4, volume=5, openinterest=-1, header=0
        )
        cerebro.adddata(data)
        cerebro.broker.setcash(_worker_cfg.INITIAL_CASH)
        
        commission_val = _worker_cfg.REAL_COMMISSION / 2
        cerebro.broker.setcommission(
            commission=commission_val, margin=_worker_cfg.REAL_MARGIN, mult=_worker_cfg.REAL_MULT,
            stocklike=False, commtype=bt.CommInfoBase.COMM_FIXED
        )
        
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
        cerebro.addanalyzer(ContractVolumeAnalyzer, _name='volume_tracker')
        cerebro.addanalyzer(bt.analyzers.drawdown.DrawDown, _name='drawdown_tracker')
        
        strategies = cerebro.run()
        first_strat = strategies[0]
            
        trade_analysis = first_strat.analyzers.trades.get_analysis()
        total_trades = 0
        win_trades = 0
        win_rate = 0.0
        
        if 'total' in trade_analysis and 'total' in trade_analysis['total']:
            total_trades = trade_analysis['total']['total']
        if 'won' in trade_analysis and 'total' in trade_analysis['won']:
            win_trades = trade_analysis['won']['total']
        if total_trades > 0:
            win_rate = round((win_trades / total_trades) * 100, 2)
        
        contracts_volume = first_strat.analyzers.volume_tracker.get_analysis()
        accumulated_commission = contracts_volume * float(_worker_cfg.REAL_COMMISSION / 2)
        final_value = first_strat.broker.getvalue()
        actual_net_profit = final_value - _worker_cfg.INITIAL_CASH
        
        # Сбор данных о пиковых рисках системы
        dd_info = first_strat.analyzers.drawdown_tracker.get_analysis()
        max_drawdown_pct = round(dd_info['max']['drawdown'], 2)
        max_drawdown_rub = float(dd_info['max']['moneydown'])

        # --- ИНИЦИАЛИЗАЦИЯ РОБАСТНЫХ МЕТРИК (ЗАЩИТА ОТ ОТСУТСТВИЯ СДЕЛКАХ) ---
        recovery_factor = 0.0
        profit_factor = 0.0

        # Извлекаем валовую прибыль и убыток из анализатора сделок Backtrader
        if total_trades > 0:
            gross_profit = float(trade_analysis.get('pnl', {}).get('gross', {}).get('total', 0.0))
            # В Backtrader убыток пишется со знаком минус, берем модуль для математических операций
            gross_loss = abs(float(trade_analysis.get('pnl', {}).get('gross', {}).get('lost', 0.0)))
            
            # Расчет Profit Factor с тотальной защитой от деления на ноль
            if gross_profit > 0 and gross_loss == 0:
                profit_factor = round(gross_profit / 1.0, 2) # Грааль без убыточных сделок
            elif gross_profit > 0 and gross_loss > 0:
                profit_factor = round(gross_profit / gross_loss, 2)
            else:
                profit_factor = 0.0

        # --- СКОРИНГ ПАРАМЕТРОВ ПРИ НАЛИЧИИ РЕАЛЬНОЙ ПРИБЫЛИ ---
        if total_trades > 0 and actual_net_profit > 0:
            # Задаем минимальный порог рублевой просадки (0.5% от депо) против деления на микро-числа
            safe_drawdown_rub = max(max_drawdown_rub, float(_worker_cfg.INITIAL_CASH) * 0.005)
            
            # 1. Базовое математическое отношение прибыли к рублевому риску
            raw_rf = actual_net_profit / safe_drawdown_rub

            # 2. Матричный фильтр по частоте сделок (Защита от случайной подгонки)
            if total_trades < 15:
                recovery_factor = round(raw_rf * 0.20, 2)   # Жесткий штраф 80% за малую выборку
            elif total_trades < 30:
                recovery_factor = round(raw_rf * 0.70, 2)   # Умеренный штраф 30%
            else:
                recovery_factor = round(raw_rf, 2)          # Полноценная статистическая выборка

            # 3. Фильтр математического ожидания (Штраф за низкий Win Rate)
            if win_rate < 40.0:
                recovery_factor = round(recovery_factor * 0.50, 2) # Режем RF на 50%

            # 4. ЖЕСТКИЙ ФИЛЬТР ШУМА ПО PROFIT FACTOR
            if profit_factor < 1.20:
                # Системы с PF < 1.2 генерируют доход на грани шума и комиссий. Штраф 60%
                recovery_factor = round(recovery_factor * 0.40, 2)
            elif profit_factor < 1.45:
                # Промежуточная зона стабильности. Штраф 15%
                recovery_factor = round(recovery_factor * 0.85, 2)
        
        results_dict = {
            'Trigger_Spread': trigger,
            'Take_Profit': tp,
            'Stop_Loss': sl,
            'Trail_Grid_Structure': str(trail_grid) if trail_grid else "[] (БЕЗ ТРАЛА)", # Идентификатор для Excel
            'Steps_Count': len(trail_grid),
            'Total_Trades': total_trades,
            'Win_Rate_Pct': win_rate,
            'Profit_Factor': profit_factor, # Новое поле в отчете для жесткой фильтрации
            'Max_DD_Pct': max_drawdown_pct, 
            'Recovery_Factor': recovery_factor, 
            'Total_Contracts': contracts_volume,
            'Total_Commission': round(accumulated_commission, 2),
            'Final_Cash': round(final_value, 2),
            'Net_Profit': round(actual_net_profit, 2)
        }
        
        del data
        del cerebro
        return results_dict
        
    except Exception as e:
        import traceback # Подключаем встроенный трассировщик
        print(f"\n[КРИТИЧЕСКАЯ ОШИБКА В ПОТОКЕ]:")
        traceback.print_exc() # Выводим полную цепочку падения (строку и файл)
        return None

def run_optimization(base_setups_pool, valid_trail_grids, month_csv_add_str, field_str, instrument_folder, cfg):
    """Глобальный запуск оптимизации: перебор ТОП-базовых сетапов и каскадных сеток трала"""

    config_path = os.path.join(instrument_folder, "config.py")
    
    csv_path = os.path.join(instrument_folder, getattr(cfg, field_str))
    results_file = os.path.join(instrument_folder, "optimization_results")
    results_file = results_file + getattr(cfg, month_csv_add_str)
    results_file_ext = ".csv"
    
    if not os.path.exists(csv_path):
        print(f"Ошибка: Файл данных {csv_path} не найден!")
        return
        
    unique_id = uuid.uuid4().hex[:8]
    processed_path = f"temp_opt_ready_{unique_id}.csv"
    
    try:
        # Парсинг и кэширование данных в один CSV для воркеров
        raw_df = pd.read_csv(csv_path, sep=';', dtype=str)
        # Переводим регистр колонок DataFrame напрямую
        raw_df.columns = [str(c).upper() for c in raw_df.columns]
        
        try:
            actual_open_list = [c for c in raw_df.columns if 'OPEN' in c or 'ОТКР' in c]
            actual_high_list = [c for c in raw_df.columns if 'HIGH' in c or 'МАКС' in c]
            if not actual_open_list or not actual_high_list:
                raise IndexError
            actual_open = actual_open_list[0]
            actual_high = actual_high_list[0]
            actual_date = [c for c in raw_df.columns if 'DATE' in c or 'ДАТА' in c][0]
            actual_time = [c for c in raw_df.columns if 'TIME' in c or 'ВРЕМЯ' in c][0]
            actual_low = [c for c in raw_df.columns if 'LOW' in c or 'МИН' in c][0]
            actual_close = [c for c in raw_df.columns if 'CLOSE' in c or 'ЗАКР' in c][0]
            actual_vol = [c for c in raw_df.columns if 'VOL' in c or 'ОБЪЕМ' in c][0]
        except IndexError:
            print(f"Критическая ошибка: Несовпадение структуры CSV! Доступные колонки: {list(raw_df.columns)}")
            return
        
        raw_df['TIMESTRING'] = (
            raw_df[actual_date].astype(str).str.replace('-', '', regex=False).str.replace('.', '', regex=False) + ' ' + 
            raw_df[actual_time].astype(str).str.replace(':', '', regex=False).str.zfill(6)
        )
        
        ready_df = pd.DataFrame()
        ready_df['DateTime'] = raw_df['TIMESTRING']
        ready_df['Open'] = raw_df[actual_open].astype(float)
        ready_df['High'] = raw_df[actual_high].astype(float)
        ready_df['Low'] = raw_df[actual_low].astype(float)
        ready_df['Close'] = raw_df[actual_close].astype(float)
        ready_df['Volume'] = raw_df[actual_vol].astype(float).round().astype(int)
        ready_df.to_csv(processed_path, index=False)
        
        # Ленивый генератор задач: извлекает таски по одной, полностью исключая забивание памяти IPC-буфера
        def tasks_generator():
            for (trigger, tp, sl) in base_setups_pool:
                for trail_grid in valid_trail_grids:
                    yield (trigger, tp, sl, trail_grid, processed_path)

        # Вычисляем общий объем математической матрицы математически
        total_tasks = len(base_setups_pool) * len(valid_trail_grids)
                    
        print(f"Сгенерировано {total_tasks} комбинаций параметров для {cfg.FUT_SEC_CODE}.")
        cores = max(1, math.floor(multiprocessing.cpu_count() * cfg.CPU_OPTIMIZATION_USAGE_PERCENT))
        
        # Инициализируем пул процессов
        list_results = []
        
        print(f"[ОПТИМИЗАЦИЯ] Запуск расчетов на {cores} ядрах ОС...")
        
        # Фиксируем время старта оптимизации
        start_time = time.time()
        
        with ProcessPoolExecutor(max_workers=cores, initializer=init_worker, initargs=(config_path,)) as executor:
            # Уменьшаем чанк до cores для мгновенного прорыва буфера и моментального старта прогресс-бара
            results_generator = executor.map(run_single_backtest, tasks_generator(), chunksize=cores)
            
            for idx, result in enumerate(results_generator, 1):
                list_results.append(result)
                
                # Считаем процент выполнения
                pct_complete = (idx / total_tasks) * 100

                # Расчет прошедшего времени
                elapsed_time = time.time() - start_time
                el_min, el_sec = int(elapsed_time // 60), int(elapsed_time % 60)
                time_str = f"{el_min:02d}:{el_sec:02d}"
                
                speed = idx / elapsed_time  # Количество задач в секунду
                remaining_tasks = total_tasks - idx
                eta_seconds = remaining_tasks / speed
                eta_min, eta_sec = int(eta_seconds // 60), int(eta_seconds % 60)
                eta_str = f"{eta_min:02d}:{eta_sec:02d}"

                # Универсальный прогресс-бар с ANSI-очисткой хвоста строки и ETA
                print(f"\r [ТРАЛ-ОПТИМИЗАЦИЯ] Прогресс: {idx}/{total_tasks} | {pct_complete:.2f}% | Прошло: {time_str} | Осталось (ETA): {eta_str}\033[K", end='', flush=True)

            # Переводим каретку строго ОДИН раз после полного выхода из цикла воркеров
            print() 
            
            # Принудительно гасим пул, закрывая все потоки
            executor.shutdown(wait=True)

        # ТОЧКА БЕЗОПАСНОГО УДАЛЕНИЯ КЭША: Все воркеры остановлены процессором, удаляем временный файл
        if os.path.exists(processed_path):
            os.remove(processed_path)
            
        # Отрезаем комбинации, которые отработали хуже фактического INITIAL_CASH из переданного в функцию cfg
        # Главный поток видит объект cfg фьючерса напрямую, гарантируя точность фильтрации
        init_cash = float(getattr(cfg, 'INITIAL_CASH', 100000.0))
        results = [r for r in list_results if r is not None and r.get('Final_Cash', 0) >= init_cash]
        results = [r for r in results if r is not None and r.get('Total_Trades', 0) > 0]
        df_results = pd.DataFrame(results)
        
        if not df_results.empty:
            df_results = df_results.sort_values(by='Final_Cash', ascending=False)
            print(f"\n=== ТОП-10 КОМБИНАЦИЙ ДЛЯ {cfg.FUT_SEC_CODE} (СОРТИРОВКА ПО КОНЕЧНОЙ СУММЕ ДЕПОЗИТА) ===")
            print(df_results.head(10).to_string(index=False))
            finalcashfile = results_file + "_trailGrid_finalCash" + results_file_ext
            
            df_results.to_csv(finalcashfile, index=False, sep=';')
            print(f"\n[УСПЕХ ТРАЛА] Топ по балансу сохранен: {finalcashfile}")
            
            df_results = df_results.sort_values(by='Recovery_Factor', ascending=False)
            print(f"\n=== ТОП-10 КОМБИНАЦИЙ ДЛЯ {cfg.FUT_SEC_CODE} (СОРТИРОВКА ПО ФАКТОРУ ВОССТАНОВЛЕНИЯ) ===")
            print(df_results.head(10).to_string(index=False))
            recfactorfile = results_file + "_trailGrid_recFactor" + results_file_ext
            
            df_results.to_csv(recfactorfile, index=False, sep=';')
            print(f"[УСПЕХ ТРАЛА] Топ по фактору восстановления сохранен: {recfactorfile}")
        else:
            print("Ошибка: Оптимизация не дала результатов.")
            
    finally:
        pass

if __name__ == '__main__':
    run_optimization()