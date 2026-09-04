import os
import glob
import pandas as pd

# 1. Настройка путей (автоматический поиск файлов трал-оптимизации)
# Файлы должны иметь префиксы оптимизации сеток трала (например, из режимов 7, 8, 9)
csv_files = glob.glob("optimization_results_*_trail*.csv")

print(f"Найдено файлов для анализа трала: {len(csv_files)}")

# Переменные для хранения датафреймов фаз рынка
df_4m = None
df_1m = None
df_stress = None

# Автоматически распределяем файлы по периодам на основе их названий
for file in csv_files:
    if "4months" in file:
        df_4m = pd.read_csv(file, sep=";")
    elif "1months" in file:
        df_1m = pd.read_csv(file, sep=";")
    elif "stress" in file:
        df_stress = pd.read_csv(file, sep=";")

# Проверка наличия всех трех матриц для проведения сквозного анализа
if df_4m is None or df_1m is None or df_stress is None:
    raise ValueError("Ошибка: Для анализа необходимы все 3 файла трала (4months, 1months, stress)!")

# КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ 1: Добавляем конфигурацию шагов трала в ключи связи таблиц
# Подразумевается, что в вашем CSV-файле шаги оптимизации трала записаны в колонку 'Trail_Steps_Config'
key_params = ["Trigger_Spread", "Take_Profit", "Stop_Loss", "Trail_Steps_Config"]

# Проверяем, есть ли колонка конфигурации трала в файлах, если нет — ищем похожую
for df in [df_4m, df_1m, df_stress]:
    if "Trail_Steps_Config" not in df.columns:
        # Автоматический перехват, если колонка названа иначе (например, по имени параметра стратегии)
        possible_cols = [c for c in df.columns if "trail" in c.lower() or "step" in c.lower()]
        if possible_cols:
            df.rename(columns={possible_cols[0]: "Trail_Steps_Config"}, inplace=True)
        else:
            raise KeyError("Критическая ошибка: В файлах результатов отсутствует колонка конфигурации ступеней трала!")

# 2. Применение первичных жестких фильтров к каждой таблице (минимальное число сделок)
df_4m = df_4m[df_4m["Total_Trades"] >= 100]       # Для макро-трендов (4 месяца)
df_1m = df_1m[df_1m["Total_Trades"] >= 40]         # Локальный форвард (30 дней)
df_stress = df_stress[df_stress["Total_Trades"] >= 5] # Период распила/боковика (2 недели)

# 3. Сквозное пересечение трех диапазонов (Матричная триада трала)
merged = df_4m.merge(df_1m, on=key_params, suffixes=("_4m", "_1m"))
final_matrix = merged.merge(df_stress, on=key_params)

# Переименуем колонки стресс-периода для жесткого соответствия целевой структуре
final_matrix.rename(
    columns={
        "Total_Trades": "Total_Trades_str",
        "Win_Rate_Pct": "Win_Rate_str",
        "Max_DD_Pct": "Max_DD_str",
        "Recovery_Factor": "Rec_Factor_str",
        "Total_Contracts": "Total_Contracts_str",
        "Total_Commission": "Total_Commission_str",
        "Final_Cash": "Final_Cash_str",
        "Net_Profit": "Net_Profit_str",
    },
    inplace=True,
)

# 4. Модифицированный фильтр "Параметрического Острова" под трейлинг-стопы
def count_profitable_trail_neighbors(row, full_dataframe, step_tp=25, step_sl=25):
    tp = row["Take_Profit"]
    sl = row["Stop_Loss"]
    ts = row["Trigger_Spread"]
    
    # Соседями считаются конфигурации с одинаковым триггером и сеткой трала,
    # но находящиеся в устойчивой зоне близлежащих TP и SL
    neighbors = full_dataframe[
        (full_dataframe["Trigger_Spread"] == ts) &
        (full_dataframe["Trail_Steps_Config"] == row["Trail_Steps_Config"]) &
        (full_dataframe["Take_Profit"].between(tp - step_tp, tp + step_tp)) &
        (full_dataframe["Stop_Loss"].between(sl - step_sl, sl + step_sl))
    ]
    
    # КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ 2: Уходим от хардкода 70к. Сет считается прибыльным, 
    # если его чистый профит на 1 месяцах выше нуля (не сливает локальный форвард)
    return len(neighbors[neighbors["Net_Profit_1m"] > 0])

# Запускаем расчет стабильности окружения сеток
if not final_matrix.empty:
    final_matrix["Island_Stability"] = final_matrix.apply(
        count_profitable_trail_neighbors, axis=1, full_dataframe=final_matrix
    )
    
    # Фильтруем: оставляем устойчивые плато (минимум 3 прибыльных соседа, исключаем случайные пики)
    final_stable_sets = final_matrix[final_matrix["Island_Stability"] >= 3]
    
    # Сортируем по качеству: приоритет устойчивости Фактора Восстановления на длительном периоде
    if "Recovery_Factor_4m" in final_stable_sets.columns:
        final_stable_sets = final_stable_sets.sort_values(by="Recovery_Factor_4m", ascending=False)
    else:
        final_stable_sets = final_stable_sets.sort_values(by="Recovery_Factor_1m", ascending=False)
        
    # Формируем точный порядок колонок согласно вашему ТЗ
    target_columns = [
        "Trigger_Spread", "Take_Profit", "Stop_Loss", "Trail_Steps_Config",
        "Total_Trades_4m", "Win_Rate_Pct_4m", "Max_DD_Pct_4m", "Recovery_Factor_4m", "Total_Contracts_4m", "Total_Commission_4m", "Final_Cash_4m", "Net_Profit_4m",
        "Total_Trades_1m", "Win_Rate_Pct_1m", "Max_DD_Pct_1m", "Recovery_Factor_1m", "Total_Contracts_1m", "Total_Commission_1m", "Final_Cash_1m", "Net_Profit_1m",
        "Total_Trades_str", "Win_Rate_str", "Max_DD_str", "Rec_Factor_str", "Total_Contracts_str", "Total_Commission_str", "Final_Cash_str", "Net_Profit_str",
        "Island_Stability"
    ]
    
    # Перестраиваем DataFrame под структуру ТЗ (с исключением отсутствующих колонок на всякий случай)
    existing_cols = [c for c in target_columns if c in final_stable_sets.columns]
    final_stable_sets = final_stable_sets[existing_cols]

    # 5. Сохранение итогового ТОП-листа робастных конфигураций трала
    output_name = "matrix_filter_trail_results.csv"
    final_stable_sets.to_csv(output_name, sep=";", index=False)
    
    print(f"\n Найдено устойчивых конфигураций трала: {len(final_stable_sets)}")
    print(f"Результаты выгружены в файл: {output_name}")
    
    # Выведем на экран ТОП-3 лучших сета по тралу
    print("\n=== ТОП-3 РОБАСТНЫХ СЕТА ТРАЛА ДЛЯ ЗАПУСКА НА РЕАЛ ===")
    print(final_stable_sets[["Trigger_Spread", "Take_Profit", "Stop_Loss", "Recovery_Factor_1m", "Rec_Factor_str", "Net_Profit_4m"]].head(3))
else:
    print("\n Сквозных пересечений параметров трала не найдено. Снизьте жесткость первичных фильтров по сделкам.")