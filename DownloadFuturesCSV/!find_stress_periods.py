import os
import pandas as pd
import numpy as np

# 1. Список путей к вашим файлам
file_paths = [
    r"..\01_SILVER\silver_m1_4months.csv",
    r"..\02_NGN\natgas_m1_4months.csv",
    r"..\03_BRENT\brent_m1_4months.csv",
    r"..\04_SBER\sber_m1_4months.csv"
]

# Параметры окна для поиска (2 недели = 14 дней * 24 часа = 336 часов)
window = 336 

# 2. Цикл циклической обработки файлов
for path in file_paths:
    file_name = os.path.basename(path)
    # Определяем папку, в которой лежит текущий файл
    file_dir = os.path.dirname(path)
    
    print(f"=== Обработка инструмента: {file_name} ===")
    
    try:
        # Загрузка текущего файла
        df = pd.read_csv(path, sep=';')

        # Подготовка и парсинг даты/времени
        date_str = df['<DATE>'].astype(str).str.strip()
        time_str = df['<TIME>'].astype(str).str.strip().str.zfill(6)
        datetime_combined = date_str + time_str
        
        df['DATETIME'] = pd.to_datetime(datetime_combined, format='%Y%m%d%H%M%S')
        df.set_index('DATETIME', inplace=True)

        # Ресемплинг в часовые бары
        df_hourly = df['<CLOSE>'].resample('h').last().dropna().to_frame()

        # Расчет Коэффициента Эффективности (ER)
        direction = df_hourly['<CLOSE>'].diff(window).abs()
        volatility = df_hourly['<CLOSE>'].diff().abs().rolling(window).sum()
        df_hourly['ER'] = direction / volatility

        # Расчет локального сужения волатильности (стандартное отклонение)
        df_hourly['Std'] = df_hourly['<CLOSE>'].rolling(window).std()

        # Ранжируем участки и ищем идеальный "распил"
        df_hourly['Stress_Score'] = df_hourly['ER'] * df_hourly['Std']
        best_flat = df_hourly['Stress_Score'].idxmin()

        # Вычисляем границы 14-дневного диапазона
        end_date = best_flat
        start_date = end_date - pd.Timedelta(days=14)

        print(f"Найден период: с {start_date} по {end_date}")

        # Обрезаем исходный минутный датафрейм
        df_stress = df.loc[start_date:end_date].copy()

        # Генерируем новое имя файла (берём первые две части из старого имени, например 'svu6_m1')
        # Разделяем по нижнему подчеркиванию и берем первые два элемента
        name_parts = file_name.split('_')
        prefix = f"{name_parts[0]}_{name_parts[1]}" if len(name_parts) > 1 else "asset"
        output_file_name = f"{prefix}_stress_period.csv"
        
        # Собираем полный путь для сохранения в ту же папку
        output_path = os.path.join(file_dir, output_file_name)

        # Возвращаем структуру QUIK (убираем индекс DATETIME)
        df_stress.reset_index(drop=True, inplace=True)
        
        # Сохраняем результат
        df_stress.to_csv(output_path, sep=';', index=False)
        print(f"Файл успешно сохранен в родную папку: {output_path}")
        print(f"Количество строк: {len(df_stress)}")
        print("-" * 50 + "\n")
        
    except Exception as e:
        print(f"Ошибка при обработке файла {file_name}: {e}")
        print("-" * 50 + "\n")

print("Все файлы успешно обработаны и сохранены по исходным папкам.")