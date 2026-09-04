"""Общие настройки проекта QUIK.

Здесь находятся только параметры, одинаковые для всех инструментов.
Инструментоспецифичные настройки остаются в соответствующем config.py.
"""

import logging
import numpy as np
from datetime import datetime


# Настройка логирования в консоль
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


# Общие настройки срочного рынка
FUT_CLASS_CODE = "SPBFUT"

# Общие параметры торговли
VOLUME = 1
SLIPPAGE_POINTS = 0.02
INITIAL_CASH = 264000.0
PRECISION_NUM_DEPO_RUB = 2

# Общие настройки производительности
CPU_OPTIMIZATION_USAGE_PERCENT = 0.92

# Общие имена файлов отчетов
LIVE_REPORT_CSV = "live_dry_run_report.csv"
REAL_REPORT_CSV = "real_trading_report.csv"

# Общие суффиксы для файлов бэктеста/оптимизации
CSV_PATH_ADD_4MONTH_PATH = "_4months"
CSV_PATH_ADD_1MONTH_PATH = "_1months"
CSV_PATH_ADD_STRESS_PERIOD = "_stress_period"

# Дата изменения регламента
REGLAMENT_CHANGE_DATE = datetime(2026, 7, 14)

# Боевой счет.
# В рабочей конфигурации заменить на реальный номер счета из QUIK.
REAL_ACCOUNT_ID = "xxx"

# Значения по умолчанию для оптимизации многоступенчатого трейлинга
DYNAMIC_TRAIL_STEPS_STANDART_OPTIMIZATION = []

# Общая сетка для анализа величины трейлинга
TRALL_RANGE = [
    round(x, 2)
    for x in np.arange(0.05, 0.95 + 0.01, 0.10)
]
