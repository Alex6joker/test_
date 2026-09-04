import os
import sys

# 1. Автоматическая защита путей поиска Python.
# Гарантируем, что корневая директория проекта всегда находится в sys.path,
# чтобы локальные зависимости и патчи не ломали многопоточность в оптимизаторе.
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

# 2. Инициализация патчей Backtrader.
# Так как patch_backtrader должен импортироваться самым первым в проекте,
# мы принудительно инициализируем его прямо здесь при любом обращении к core.
try:
    import core.patch_backtrader
except ImportError:
    # Если файл лежит в корне, пробуем импортировать напрямую
    import patch_backtrader

# 3. Чистый экспорт интерфейса ядра наружу.
# Теперь внешние скрипты (main.py, backtester.py, optimize.py) могут импортировать
# компоненты напрямую: "from core import RealisticFuturesStrategy"
from core.backtest_engine import RealisticFuturesStrategy, ContractVolumeAnalyzer

# Явно объявляем публичный интерфейс пакета
__all__ = [
    'RealisticFuturesStrategy',
    'ContractVolumeAnalyzer'
]