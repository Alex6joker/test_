from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
CORE=ROOT/"core"

class BacktestArchitectureTests(unittest.TestCase):
    def test_all_core_backtest_modules_are_backtrader_import_free(self):
        for path in CORE.glob("backtest_*.py"):
            text=path.read_text(encoding="utf-8")
            self.assertNotIn("import backtrader", text, str(path))
            self.assertNotIn("from backtrader", text, str(path))

    def test_backtrader_backtest_modules_removed(self):
        for name in ("backtest_adapter.py","backtest_compat.py","backtest_engine.py","backtest_runner.py"):
            self.assertFalse((CORE/name).exists(),name)

    def test_optimizer_backtrader_infrastructure_exists(self):
        for name in ("backtrader_adapter.py","backtrader_compat.py","backtrader_runner.py"):
            self.assertTrue((ROOT/"optimizer"/name).exists(),name)
