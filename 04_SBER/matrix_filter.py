import os
import glob
import pandas as pd

# 1. Настройка путей (скрипт автоматически найдет файлы в папке запуска)
# Поместите этот скрипт в каталог, где лежат ваши CSV-результаты оптимизации
csv_files = [f for f in glob.iglob("optimization_results_*_finalCash.csv") if "trail" not in f]

print(f"Найдено файлов для анализа: {len(csv_files)}")

# Переменные для хранения датафреймов
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
    raise ValueError("Ошибка: Для анализа необходимы все 3 файла (4months, 1months, stress)!")

# Ключевые параметры для связи таблиц
key_params = ["Trigger_Spread", "Take_Profit", "Stop_Loss"]

# 2. Применение первичных жестких фильтров к каждой таблице (минимальное число сделок)
df_4m = df_4m[df_4m["Total_Trades"] >= 100]          # Для 4 месяцев порог сделок выше
df_1m = df_1m[df_1m["Total_Trades"] >= 40]          # Микро-тест 30 дней
df_stress = df_stress[df_stress["Total_Trades"] >= 5]  # Стресс-период 2 недели

# 3. Сквозное пересечение трех диапазонов (Матричная триада)
merged = df_4m.merge(df_1m, on=key_params, suffixes=("_4m", "_1m"))
final_matrix = merged.merge(df_stress, on=key_params)

# Переименуем колонки стресс-периода для наглядности
final_matrix.rename(
    columns={
        "Total_Trades": "Total_Trades_str",
        "Win_Rate_Pct": "Win_Rate_str",
        "Max_DD_Pct": "Max_DD_str",
        "Recovery_Factor": "Rec_Factor_str",
        "Net_Profit": "Net_Profit_str",
    },
    inplace=True,
)

# 4. Фильтр "Параметрического Острова"
# Метод проверяет, сколько прибыльных соседей есть у каждой точки в 3D пространстве параметров
def count_profitable_neighbors(row, full_dataframe, step_tp=25, step_sl=25):
    tp = row["Take_Profit"]
    sl = row["Stop_Loss"]
    ts = row["Trigger_Spread"]
    
    # Ищем соседей в пределах одного шага по TP и SL
    neighbors = full_dataframe[
        (full_dataframe["Trigger_Spread"] == ts) &
        (full_dataframe["Take_Profit"].between(tp - step_tp, tp + step_tp)) &
        (full_dataframe["Stop_Loss"].between(sl - step_sl, sl + step_sl))
    ]
    # Нам нужны только прибыльные соседи
    return len(neighbors[neighbors["Net_Profit_1m"] > 70000])

# Запускаем расчет стабильности окружения (минимум 3 прибыльных соседа вокруг сета)
if not final_matrix.empty:
    final_matrix["Island_Stability"] = final_matrix.apply(
        count_profitable_neighbors, axis=1, full_dataframe=final_matrix
    )
    # Фильтруем: оставляем только устойчивые "острова" (исключаем одиночные пики подгонки)
    final_stable_sets = final_matrix[final_matrix["Island_Stability"] >= 3]
    
    # Сортируем по качеству: приоритет фактору восстановления на 4 месяцах
    final_stable_sets = final_stable_sets.sort_values(by="Recovery_Factor_1m", ascending=False)
    
    # 5. Сохранение итогового ТОП-листа робастных параметров
    output_name = "matrix_filter_results.csv"
    final_stable_sets.to_csv(output_name, sep=";", index=False)
    
    print(f"\n Найдено устойчивых конфигураций: {len(final_stable_sets)}")
    print(f"Результаты выгружены в файл: {output_name}")
    
    # Выведем на экран ТОП-3 лучших сета
    print("\n=== ТОП-3 РОБАСТНЫХ СЕТА ДЛЯ ЗАПУСКА НА РЕАЛ ===")
    print(final_stable_sets[key_params + ["Recovery_Factor_1m", "Rec_Factor_str", "Net_Profit_4m"]].head(3))
else:
    print("\n Сквозных пересечений не найдено. Снизьте жесткость фильтров по сделкам.")