import os
import pandas as pd
import logging
import time

def load_top_matrix_setups(instrument_folder, cfg, top_n=40):
    """
    Открывает файл результатов матричной фильтрации фьючерса.
    Извлекает первые 3 критических параметра для оптимизации трала
    и округляет их согласно PRECISION_NUM из конфигурации инструмента.
    """
    csv_filename = "matrix_filter_results.csv"
    csv_path = os.path.join(instrument_folder, csv_filename)
    
    # Извлекаем шаг округления инструмента (дефолт = 2, если параметр не найден)
    precision = getattr(cfg, 'PRECISION_NUM', 2)
    
    if not os.path.exists(csv_path):
        logging.error(f"[ОШИБКА ПАРСЕРА] Файл {csv_path} не найден! Прогон трала невозможен.")
        return []

    try:
        # Читаем CSV с разделителем точка с запятой
        df = pd.read_csv(csv_path, sep=';')
        
        # Защита: Приводим названия всех колонок к единому регистру и чистим пробелы
        df.columns = [str(c).strip().title() for c in df.columns]
        
        # Ищем точные соответствия названий колонок в CSV
        col_trigger = 'Trigger_Spread'
        col_tp = 'Take_Profit'
        col_sl = 'Stop_Loss'
        
        # Проверяем физическое наличие целевых колонок в файле
        if not all(col in df.columns for col in [col_trigger, col_tp, col_sl]):
            logging.critical(f"[СБОЙ СТРУКТУРЫ] В CSV не найдены обязательные поля Trigger/Tp/Sl! Колонки файла: {list(df.columns)}")
            return []
            
        # Отсекаем строго первые ТОП-N строк
        df_top = df.head(top_n)
        
        # Собираем чистый список кортежей параметров входа с округлением по конфигу бумаги
        setups_pool = []
        for _, row in df_top.iterrows():
            # Округляем каждый параметр в соответствии с точностью фьючерса (PRECISION_NUM)
            trigger = round(float(row[col_trigger]), precision)
            tp = round(float(row[col_tp]), precision)
            sl = round(float(row[col_sl]), precision)
            setups_pool.append((trigger, tp, sl))
            
        logging.info(f"[УСПЕХ CSV] Загружено и округлено {len(setups_pool)} сетапов (Точность инструмента: {precision} знаков).")
        
        return setups_pool
        
    except Exception as e:
        logging.error(f"Критический сбой при чтении matrix_filter_results.csv: {e}")
        return []