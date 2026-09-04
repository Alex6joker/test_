"""Конфигурация инструмента NATURAL GAS."""

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
FUT_SEC_CODE = "NGN6"

# Ориентировочные параметры для старта до получения данных оптимизации
TRIGGER_SPREAD = 0.025
TAKE_PROFIT = 0.040
STOP_LOSS = 0.050
OFFER_RISK = 0.02

SAFETY_FACTOR = 1.1

# Реальные спецификации Мосбиржи для NG
REAL_MARGIN = 7454.0
REAL_COMMISSION = 8.99
REAL_MULT = 7775.39

# Файлы исторических данных
TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH = "natgas_m1_4months.csv"
TEST_OPTIMIZE_CSV_PATH_1MONTH_PATH = "natgas_m1_1months.csv"
TEST_OPTIMIZE_CSV_PATH_STRESS_PERIOD = "natgas_m1_stress_period.csv"

QUIK_PORT = 34140

VOLATILITY_PRECISION_NUM = ".3f"
PRECISION_NUM = 3
PRECISION_NUM_DEPO_RUB = 2

# Сетки параметров оптимизации
TRIGGER_RANGE = [
    round(x, 3)
    for x in np.arange(0.010, 0.050 + 0.001, 0.001)
]
TP_RANGE = [
    round(x, 3)
    for x in np.arange(0.040, 0.200 + 0.005, 0.005)
]
SL_RANGE = [
    round(x, 3)
    for x in np.arange(0.060, 0.250 + 0.010, 0.010)
]

DYNAMIC_TRAIL_STEPS = [
    (0.15, 0.03),
    (0.30, 0.05),
    (0.60, 0.30),
    (0.85, 0.60),
]

# Пустые состояния оптимизации принадлежат конкретному инструменту.
DYNAMIC_TRAIL_STEPS_STANDART_OPTIMIZATION = []
OPTIMIZED_TRAIL_STEPS = []
