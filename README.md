python -m unittest discover -s tests -p "test_*.py"
python benchmarks/stage8a_backtrader/benchmark.py
python benchmarks/stage8a_backtrader/benchmark.py --warmup 2 --runs 10 --modes NONE
python benchmarks/stage8a_backtrader/profiler.py
python benchmarks/stage8a_backtrader/observer_benchmark.py --warmup 2 --runs 10
python benchmarks\stage8a_backtrader\native_benchmark.py --warmup 2 --runs 10
python benchmarks/stage8a_backtrader/parity_test.py