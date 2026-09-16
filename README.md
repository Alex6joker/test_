python -m unittest discover -s tests -p "test_*.py"
python benchmarks/stage8a_backtrader/benchmark.py
python benchmarks/stage8a_backtrader/benchmark.py --warmup 2 --runs 10 --modes NONE
python benchmarks/stage8a_backtrader/profiler.py
python benchmarks/stage8a_backtrader/observer_benchmark.py --warmup 2 --runs 10
python benchmarks\stage8a_backtrader\native_benchmark.py --warmup 2 --runs 10
python benchmarks/stage8a_backtrader/parity_test.py
python benchmarks\stage8a_backtrader\native_profiler.py --warmup 1 --top 100

for /L %i in (1,1,7) do @(echo ===== RUN %i ===== & python -c "import subprocess,time; t=time.perf_counter(); subprocess.run(['python','main.py','--work-mode','1','--selected-forts','03_BRENT'],input='NONE\n',text=True,stdout=subprocess.DEVNULL); print(f'{time.perf_counter()-t:.3f} sec')")
python benchmarks\production_profiler.py