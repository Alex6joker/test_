"""Конфигурация инструмента SILVER."""

import numpy as np

from config_common import (
    FUT_CLASS_CODE,
    VOLUME,
    SLIPPAGE_POINTS,
    PRECISION_NUM_DEPO_RUB,
    INITIAL_CASH,
    CPU_OPTIMIZATION_USAGE_PERCENT,
    LIVE_REPORT_CSV,
    REAL_REPORT_CSV,
    CSV_PATH_ADD_4MONTH_PATH,
    CSV_PATH_ADD_1MONTH_PATH,
    CSV_PATH_ADD_STRESS_PERIOD,
    REGLAMENT_CHANGE_DATE,
    REAL_ACCOUNT_ID,
    TRALL_RANGE,
)

# Код инструмента в QUIK
FUT_SEC_CODE = "SVU6"

# Финальные параметры лучшей комбинации (Строка 10)
TRIGGER_SPREAD = 0.12
TAKE_PROFIT = 0.90
STOP_LOSS = 0.60
OFFER_RISK = 0.02

# Коэффициент запаса по деньгам для ГО
SAFETY_FACTOR = 1.1

# Реальные спецификации срочного рынка
REAL_MARGIN = 9300.89
REAL_COMMISSION = 13.81
REAL_MULT = 728.3

# Файлы исторических данных
TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH = "silver_m1_4months.csv"
TEST_OPTIMIZE_CSV_PATH_1MONTH_PATH = "silver_m1_1months.csv"
TEST_OPTIMIZE_CSV_PATH_STRESS_PERIOD = "silver_m1_stress_period.csv"

QUIK_PORT = 34130

VOLATILITY_PRECISION_NUM = ".2f"
PRECISION_NUM = 2
PRECISION_NUM_DEPO_RUB = 2

# Сетки параметров оптимизации
TRIGGER_RANGE = [
    round(x, 2)
    for x in np.arange(0.10, 0.50 + 0.01, 0.01)
]
TP_RANGE = [
    round(x, 2)
    for x in np.arange(0.40, 2.00 + 0.05, 0.05)
]
SL_RANGE = [
    round(x, 2)
    for x in np.arange(0.60, 2.50 + 0.10, 0.10)
]

DYNAMIC_TRAIL_STEPS = [
    (0.25, 0.05),
    (0.65, 0.35),
    (0.95, 0.65),
]

# Пустые состояния оптимизации принадлежат конкретному инструменту.
DYNAMIC_TRAIL_STEPS_STANDART_OPTIMIZATION = []
OPTIMIZED_TRAIL_STEPS = []
