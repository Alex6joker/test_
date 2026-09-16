import os
import sys

# Keep the project root importable for legacy entry points and parallel workers.
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

# Do not import Backtrader or any Backtrader compatibility layer here.
# Production backtester modules import their concrete dependencies directly.
# Legacy Backtrader users can still obtain the historical public exports via
# lazy attribute resolution below, without pulling Backtrader into every
# `import core.*` used by the native production path.
__all__ = [
    "RealisticFuturesStrategy",
    "ContractVolumeAnalyzer",
]


def __getattr__(name):
    """Lazily resolve legacy public exports when explicitly requested."""
    if name == "RealisticFuturesStrategy":
        from core.backtest_engine import RealisticFuturesStrategy
        return RealisticFuturesStrategy
    if name == "ContractVolumeAnalyzer":
        from core.backtest_compat import ContractVolumeAnalyzer
        return ContractVolumeAnalyzer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
