"""Конфигурация инструмента BRENT."""

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
FUT_SEC_CODE = "BRU6"

# Ориентировочные параметры для старта до получения данных оптимизации
TRIGGER_SPREAD = 0.16
TAKE_PROFIT = 1.90
STOP_LOSS = 1.70
OFFER_RISK = 0.03

SAFETY_FACTOR = 1.15

# Реальные спецификации Мосбиржи для BRU6
REAL_MARGIN = 18200.0
REAL_COMMISSION = 20.37
REAL_MULT = 777.0

# Файлы исторических данных
TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH = "brent_m1_4months.csv"
TEST_OPTIMIZE_CSV_PATH_1MONTH_PATH = "brent_m1_1months.csv"
TEST_OPTIMIZE_CSV_PATH_STRESS_PERIOD = "brent_m1_stress_period.csv"

QUIK_PORT = 34150

VOLATILITY_PRECISION_NUM = ".2f"
PRECISION_NUM = 2
PRECISION_NUM_DEPO_RUB = 2

# Сетки параметров оптимизации
TRIGGER_RANGE = [
    round(x, 2)
    for x in np.arange(0.10, 0.41, 0.01)
]
TP_RANGE = [
    round(x, 2)
    for x in np.arange(0.40, 3.05, 0.10)
]
SL_RANGE = [
    round(x, 2)
    for x in np.arange(0.60, 4.05, 0.20)
]

DYNAMIC_TRAIL_STEPS = [
    (0.35, 0.05),
    (0.75, 0.15),
    (0.95, 0.65),
]

# Пустые состояния оптимизации принадлежат конкретному инструменту.
DYNAMIC_TRAIL_STEPS_STANDART_OPTIMIZATION = []
OPTIMIZED_TRAIL_STEPS = []
