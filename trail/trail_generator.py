import os
import itertools
import numpy as np
from trail.trail_combo_generator import load_top_matrix_setups

def run_standalone_trail_generation(month_csv_add_str, field_str, cfg):
    """
    Автономное расчетное ядро генерации многоступенчатого трайлинг-стопа.
    Полностью изолировано от торговых движков и бэктестеров.
    """
    print("\n=== АВТОГЕНЕРАЦИЯ И АНАЛИЗ СЕТОК ТРАЛА С КВАНТОВАНИЕМ 15% ===")
    
    # 1. Формируем сквозной диапазон процентов с шагом 5% (от 5% до 90% пути к тейку)
    pct_range = cfg.TRALL_RANGE
    
    print(f"[НАСТРОЙКИ] Базовый сет параметров перебора: {[int(x*100) for x in pct_range]} %")
    print("[ПРОЦЕСС] Генерация логических каскадов с фильтрами зазоров 15%...")

    # Переменная для хранения итоговых валидных конфигураций сеток трала
    valid_trail_grids = []
    # Базовый вариант работы робота ВООБЩЕ БЕЗ ТРАЛА (бинарный TP/SL)
    valid_trail_grids.append([])

    # 2. Перебираем глубину каскада поджатия от 1 до 4 ступеней
    for depth in range(1, 5):
        # Шаг А: Генерируем уникальные, строго возрастающие цепочки триггеров
        for triggers_comb in itertools.combinations(pct_range, depth):
            
            # ЖЕСТКОЕ УСЛОВИЕ 1: Шаг между триггерами TP должен быть не менее 15%
            is_trigger_step_valid = True
            for i in range(1, depth):
                if (triggers_comb[i] - triggers_comb[i-1]) < 0.15:
                    is_trigger_step_valid = False
                    break
                    
            if not is_trigger_step_valid:
                continue

            # Шаг Б: Для текущей цепочки триггеров генерируем строго возрастающие цепочки стопов
            for stops_comb in itertools.combinations(pct_range, depth):
                
                is_valid_cascade = True
                for i in range(depth):
                    t_curr = triggers_comb[i]
                    s_curr = stops_comb[i]
                    
                    # ЖЕСТКОЕ УСЛОВИЕ 2: На каждой конкретной ступени разница между Trigger и Stop должна быть >= 15%
                    if (t_curr - s_curr) < 0.15:
                        is_valid_cascade = False
                        break
                        
                if is_valid_cascade:
                    grid_structure = [(triggers_comb[i], stops_comb[i]) for i in range(depth)]
                    valid_trail_grids.append(grid_structure)

    # 3. СОРТИРОВКА ДЛЯ КРАСИВОГО ОТОБРАЖЕНИЯ (Сначала пустые, затем 1, 2, 3 и 4-ступенчатые)
    valid_trail_grids.sort(key=lambda x: (len(x), str(x)))

    # 4. ФОРМАТИРОВАННЫЙ ВЫВОД ГОТОВОГО СИНТАКСИСА PYTHON В КОНСОЛЬ
    sec_code = getattr(cfg, 'FUT_SEC_CODE', 'UNKNOWN_FORTS')
    print(f"\n[УСПЕХ] Сгенерировано {len(valid_trail_grids)} логически верных конфигураций массива для фьючерса {sec_code}.\n")
    print("-" * 90)
    
    # === ИНТЕГРАЦИЯ С МАТРИЧНЫМ ФИЛЬТРОМ ИНСТРУМЕНТА ===
    print("\n[ЭТАП 2] Запуск интеграции с результатами исторического матричного фильтра...")
    
    # Вычисляем путь к папке текущего запущенного инструмента на базе переданного cfg
    import os
    instrument_folder = os.path.dirname(cfg.__file__)
    
    # Вызываем функцию чтения CSV, передавая объект конфигурации cfg для контроля PRECISION_NUM
    base_setups_pool = load_top_matrix_setups(instrument_folder, cfg, top_n=40)
    
    if base_setups_pool:
        total_backtests_to_run = len(base_setups_pool) * len(valid_trail_grids)
        print(f"[КАЛЬКУЛЯТОР ЗАДАЧ] Для {len(base_setups_pool)} базовых сетапов и {len(valid_trail_grids)} сеток трала")
        print(f"                     требуется выполнить всего: {total_backtests_to_run} бэктестов.")
        print("-" * 90)
        
        # Импортируем переписанное ядро оптимизатора трала
        from trail.trail_optimize import run_optimization
        
        print("[СТАРТ] Запуск пула процессов ОС на 88% мощности CPU...")
        # Вызываем расчет под 4-месячный исторический интервал по умолчанию
        run_optimization(
            base_setups_pool, 
            valid_trail_grids, 
            month_csv_add_str, 
            field_str, 
            instrument_folder, 
            cfg
        )
    else:
        print("[ВНИМАНИЕ] Пул базовых сетапов пуст. Проверьте наличие matrix_filter_results.csv.")

    return