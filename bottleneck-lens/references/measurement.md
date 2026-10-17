# Measurement guide

## Bundled collector

Use the existing project interpreter; no third-party packages are required. Replace `SKILL_DIR` with this skill's absolute directory. These examples execute `workload.py`; review and obtain authorization first. All output parent directories must already exist. Keep artifacts outside the source tree when possible.

```bash
python "$SKILL_DIR/scripts/measure.py" measure --cwd /path/to/project --script workload.py --workload fixture-v1-seed42 --revision before --mode benchmark --runs 7 --warmups 1 --timeout 30 --output /tmp/bottleneck-lens-before.json -- --size 1000
python "$SKILL_DIR/scripts/measure.py" measure --cwd /path/to/project --script workload.py --workload fixture-v1-seed42 --revision before --mode cpu --timeout 30 --output /tmp/bottleneck-lens-cpu-before.json -- --size 1000
python "$SKILL_DIR/scripts/measure.py" measure --cwd /path/to/project --script workload.py --workload fixture-v1-seed42 --revision before --mode memory --timeout 30 --output /tmp/bottleneck-lens-memory-before.json -- --size 1000
```

After an authorized change, repeat with `--revision after` and fresh output names. For a module, substitute `--module package.module` for `--script workload.py`. Arguments after `--` go to the workload without shell expansion. The driver should assert meaningful outputs; the collector suppresses workload stdout, inherits stderr, and does not validate application output. The subprocess inherits the environment and has stdin disabled. Normal exceptions, nonzero exits, missing metrics, and timeouts abort collection; no success report is written. Successful `sys.exit(0)` is supported. On POSIX, cleanup kills remaining descendants in the same process group after each invocation; detached descendants can escape. Other platforms only guarantee direct-child cleanup. Do not use this collector to launch persistent services.

```bash
python "$SKILL_DIR/scripts/measure.py" compare /tmp/bottleneck-lens-before.json /tmp/bottleneck-lens-after.json --output /tmp/bottleneck-lens-comparison.json
python "$SKILL_DIR/scripts/measure.py" compare /tmp/bottleneck-lens-memory-before.json /tmp/bottleneck-lens-memory-after.json --output /tmp/bottleneck-lens-memory-comparison.json
```

Comparison is read-only with respect to the project and does not run the workload. The same target path, arguments, working directory, workload ID, interpreter/host metadata, selected environment settings, root-level dependency file hashes, collection mode, run count, and timeout must match. Revision labels and script hashes may differ. These checks do not cover transitive source, all environment variables, dataset contents, installed packages, CPU governor, or background load. Use a workload ID encoding fixture/seed/scenario identity and record missing context separately. JSON contains paths and arguments but never a full environment dump or captured target output. Inspect artifacts before sharing.

## Metric contract

- `wall_seconds`: `perf_counter()` duration around execution of the script/module through `runpy`. Includes wrapper dispatch, target imports not already loaded by the collector, and driver assertions. Excludes interpreter startup, initial collector imports, shutdown/atexit hooks, and report generation. Collector-preloaded modules alter import costs: this is not a faithful application startup benchmark, end-to-end process time, or per-request latency. Use an external benchmark for those questions.
- `cpu_seconds`: `process_time()` for the worker's target execution, summed across its threads; excludes sleep and child-process CPU. This is CPU time, not a sampled CPU utilization percentage. The benchmark has no cProfile/tracemalloc enabled by the collector. If instrumentation is already enabled externally, the benchmark refuses to run.
- `process_peak_rss_bytes`: process lifetime maximum resident set size from `resource.getrusage(RUSAGE_SELF)` on Linux/macOS only. Includes interpreter/collector startup and any profiler overhead; excludes children. It is not a live or target-only RAM measurement. Compare benchmark RSS only with benchmark RSS. Unsupported platforms omit it rather than report zero.
- `python_peak_bytes`: peak Python-tracked allocation bytes during the memory run. `python_current_bytes` and allocation locations are measured immediately after the target returns. Native allocations can be missing. Peak cannot be attributed to the final snapshot; freed allocations are absent. Memory runs add tracing overhead and run once, so repeated evidence is needed before asserting stability or leaks.
- CPU JSON retains all function rows with primitive/total call counts, self/cumulative seconds, and caller identities. Wall-clock cProfile time can include waits; its default timer is not a CPU sampling timer. The current thread is profiled, not every application thread. Benchmark CPU time covers all worker threads, so the two scopes differ.
- Benchmark summaries retain raw samples, count, median, min, max, and sample standard deviation. Compare medians using `(after - before) / before * 100`; a zero baseline yields `null`. Overlapping sample ranges are a conservative noise warning, not a statistical test. Single-sample profile differences are marked insufficient for stability assessment. The comparison does not mark a result verified or enforce CI regression thresholds.

## Choosing additional tools

Use existing project benchmarks where they provide a better workload. `timeit` suits isolated pure operations; beware its different GC behavior and setup scope. Existing pytest benchmarks may be useful without introducing another test harness. Before adding or installing optional tools, confirm the need and scope. `py-spy` can help with live processes or sampled stacks; it is optional and not implemented by the bundled collector. Database tracing/query counters, OS I/O tools, and HTTP load drivers need separate measurement plans. No automatic production attachment or dependency installation.

## Primary references

- [Python profiling and pstats](https://docs.python.org/3.12/library/profile.html): profiler overhead, self/cumulative time, call counts.
- [tracemalloc](https://docs.python.org/3.12/library/tracemalloc.html): Python allocation scope and snapshot/peak behavior.
- [Python clocks](https://docs.python.org/3.12/library/time.html): wall-clock and process CPU time semantics.
- [resource usage](https://docs.python.org/3.12/library/resource.html): process resource counters.
- [timeit](https://docs.python.org/3.12/library/timeit.html): microbenchmark repetitions and garbage collection behavior.
