"""Конфигурация инструмента SBER."""

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
FUT_SEC_CODE = "SRU6"

# Ориентировочные параметры для старта до получения данных оптимизации
TRIGGER_SPREAD = 20
TAKE_PROFIT = 275
STOP_LOSS = 230
OFFER_RISK = 0.02

SAFETY_FACTOR = 1.15

# Реальные спецификации Мосбиржи для SBER
REAL_MARGIN = 4646.0
REAL_COMMISSION = 9.57
REAL_MULT = 1.0

# Файлы исторических данных
TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH = "sber_m1_4months.csv"
TEST_OPTIMIZE_CSV_PATH_1MONTH_PATH = "sber_m1_1months.csv"
TEST_OPTIMIZE_CSV_PATH_STRESS_PERIOD = "sber_m1_stress_period.csv"

QUIK_PORT = 34160

VOLATILITY_PRECISION_NUM = ".0f"
PRECISION_NUM = 0
PRECISION_NUM_DEPO_RUB = 2

# Сетки параметров оптимизации
TRIGGER_RANGE = [int(x) for x in range(20, 201, 20)]
TP_RANGE = [int(x) for x in range(50, 401, 25)]
SL_RANGE = [int(x) for x in range(30, 251, 25)]

DYNAMIC_TRAIL_STEPS = [
    (0.15, 0.03),
    (0.30, 0.05),
    (0.60, 0.30),
    (0.85, 0.60),
]

# Пустые состояния оптимизации принадлежат конкретному инструменту.
DYNAMIC_TRAIL_STEPS_STANDART_OPTIMIZATION = []
OPTIMIZED_TRAIL_STEPS = []
