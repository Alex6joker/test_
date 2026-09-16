python -m unittest discover -s tests -p "test_*.py"
python benchmarks/stage8a_backtrader/benchmark.py
python benchmarks/stage8a_backtrader/benchmark.py --warmup 2 --runs 10 --modes NONE
python benchmarks/stage8a_backtrader/profiler.py
python benchmarks/stage8a_backtrader/observer_benchmark.py --warmup 2 --runs 10
python benchmarks\stage8a_backtrader\native_benchmark.py --warmup 2 --runs 10
python benchmarks/stage8a_backtrader/parity_test.py
python benchmarks\stage8a_backtrader\native_profiler.py --warmup 1 --top 100

for /L %i in (1,1,14) do @(echo ===== RUN %i ===== & python -c "import subprocess,time; t=time.perf_counter(); subprocess.run(['python','main.py','--work-mode','1','--selected-forts','03_BRENT'],input='NONE\n',text=True,stdout=subprocess.DEVNULL); print(f'{time.perf_counter()-t:.3f} sec')")
python benchmarks\production_profiler.py

python -c "import importlib.util; spec=importlib.util.spec_from_file_location('cfg', r'03_BRENT\config.py'); cfg=importlib.util.module_from_spec(spec); spec.loader.exec_module(cfg); print('FUT_SEC_CODE =', cfg.FUT_SEC_CODE); print('CSV =', r'03_BRENT\\' + cfg.TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH)"
python -c "import importlib.util; from core.backtest_data import load_and_prepare_backtest_bars; from core.backtest_validation import validate_backtest_bars; from core.backtest_logger import BacktestLogger; spec=importlib.util.spec_from_file_location('cfg', r'03_BRENT\config.py'); cfg=importlib.util.module_from_spec(spec); spec.loader.exec_module(cfg); p=r'03_BRENT\\' + cfg.TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH; bars=load_and_prepare_backtest_bars(p); print('BARS =', len(bars)); print('FIRST =', bars[0]); print('LAST =', bars[-1]); l=BacktestLogger(r'logs\_stage833_validation.log', reset=True, mode='NONE'); validate_backtest_bars(bars, l); l.close(); print('VALIDATION PASS')"
python -c "import importlib.util; from core.backtest_data import load_and_prepare_backtest_bars; from core.backtest_native import run_native_backtest; from core.backtest_logger import BacktestLogger; spec=importlib.util.spec_from_file_location('cfg','03_BRENT/config.py'); cfg=importlib.util.module_from_spec(spec); spec.loader.exec_module(cfg); bars=load_and_prepare_backtest_bars('03_BRENT/'+cfg.TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH); l=BacktestLogger('logs/_stage833_native_none.log',reset=True,mode='NONE'); r=run_native_backtest(bars,cfg,l,cfg.PRECISION_NUM_DEPO_RUB); l.close(); print(r)"
