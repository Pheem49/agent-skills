# Measurement guide (v0.2)

The CLI requires Python 3.10+ as a supervisor. The Python adapter needs Python 3.10+ in the selected project environment; the Node adapter needs Node.js 22+. No third-party profiling/statistics packages are required. Resolve `SKILL_DIR` to the installed skill directory. Execute only an approved, finite workload.

## Collect and preserve evidence

Choose an existing output directory outside the source tree, for example `/path/to/results`. Every invocation creates a JSON file, a Markdown report, and an adjacent `<output-stem>.artifacts/` directory. They are never overwritten. Failed runs leave diagnostic files in that directory without writing a successful measurement JSON; choose a fresh output name to retry. Keep JSON and its artifact directory together so correctness evidence can be verified after copying results. Stderr logs and optional stdout evidence can contain application data.

```bash
python "$SKILL_DIR/scripts/measure.py" measure \
  --cwd /path/to/project --script workload.py --runtime python \
  --workload fixture-v1-seed42 --dataset fixture-v1 --seed 42 \
  --input fixtures/input.json --source package/operation.py \
  --cache-policy 'Fresh application process; OS caches uncontrolled' \
  --expected-stdout fixtures/expected.txt \
  --revision before --mode benchmark --runs 7 --warmups 1 --timeout 30 \
  --output /path/to/results/before.json -- --size 1000
```

Use `--python /path/to/venv/bin/python` to select a project interpreter. Python modules use `--module package.module` instead of `--script`. `.js`, `.mjs`, and `.cjs` scripts select Node automatically; explicit selection is clearer when sharing commands:

```bash
python "$SKILL_DIR/scripts/measure.py" measure \
  --cwd /path/to/project --script workload.mjs --runtime node --node /usr/bin/node \
  --workload api-fixture-v1 --dataset fixture-v1 --input fixtures/input.json \
  --expected-stdout fixtures/expected.txt \
  --revision before --mode benchmark --output /path/to/results/node-before.json
```

Arguments after `--` go to the workload without a shell. Default benchmark and Python allocation collection each execute one discarded warmup and seven recorded samples, each in a fresh process. `--runs` and `--warmups` apply to both modes, so allocation comparisons can have enough samples for statistical evidence. CPU profiles execute once without a warmup. The recorded `--seed` is an identity label; the workload must actually apply that seed. `--input` fingerprints explicit dataset files; it cannot discover all files or external state the application reads. Use `--source` for relevant transitive source; the script itself is fingerprinted automatically. Module transitive source is not automatically fingerprinted. Root-level manifests/lockfiles and selected environment settings are recorded; installed packages, all environment variables, CPU governor, thermal state, and load are not fully captured.

`--expected-stdout` compares **exact stdout bytes** with an independently reviewed known-answer file on every warmup and sample. Matching outputs produce a `passed` result scoped to that oracle; mismatches preserve measurements/evidence but exit 3 and cannot produce a verified improvement. Omitting the oracle leaves correctness `not_assessed`, even when the process exits successfully or the workload contains its own assertions. A narrow output oracle is not comprehensive correctness testing: still run the project's relevant correctness tests and record their results.

### Diagnostic profiles

Repeat the same request with `--mode cpu` and a fresh output path. Python retains a `.pstats` file plus function/caller statistics; Node retains `.cpuprofile` files plus sampled main-isolate self-time rows and `perf_hooks` event-loop diagnostics. Node uses separate per-run profile directories and default pid/thread-id filenames to preserve worker-isolate files without collisions; only the identified main thread (tid 0) is summarized. All diagnostics are local and add overhead. Do not use diagnostic CPU-profile timings as a before/after benchmark. V8 sample hits are not function call counts, and its profile includes the entire Node main-isolate lifetime, including collector startup. Neither adapter profiles native child stacks.

For Python allocations, use `--mode memory`; retain the default repeated samples for before/after comparisons, or explicitly use `--runs 1 --warmups 0` for a single diagnostic snapshot that cannot establish statistical support. Node allocation profiling is not implemented; requesting it fails before executing the target. Benchmark RSS is available for both runtimes where supported. Node executes the target as its real ESM/CommonJS entrypoint, using a collector preload so `require.main === module` retains its normal behavior. Node module-name entrypoints and long-running servers are not supported; use a bounded driver for APIs and workers.

Python allocation snapshots describe live allocations after the target returns. Peak bytes can come from allocations already freed by that point, so final snapshot locations cannot be assigned as the source of the peak or proof of a leak.

## Common JSON contract

All collectors write **schema version 2**, described by [measurement.schema.json](measurement.schema.json). `scripts/contract.py` additionally enforces cross-field invariants and evidence hashes. Version-1 and ad-hoc JSON from earlier experiments are rejected with a recollection message: missing measurement boundaries cannot be safely guessed during migration.

The contract contains:

- `collector`: adapter/version and bundled collector file fingerprints.
- `workload`, `revision`, `source_fingerprints`: workload identity, code revision label, and explicitly tracked source hashes. Revision labels are supplied by the caller; they are not automatically inferred Git commits.
- `environment`: actual target runtime/executable/version, host, selected settings, and dependency-file hashes.
- `inputs`: dataset identity, seed label, explicit file fingerprints, and declared cache policy.
- `target`, `cwd`, `protocol`: exact arguments, repetition count, fresh-process policy, and timeout.
- `metrics`: each metric's unit, favorable direction, comparability, process/thread/child scope, timing interval, startup/shutdown inclusion, and instrumentation.
- `samples`: raw numeric observations; `summary` caches count/median/mean/min/max/sample standard deviation. The reader recomputes summaries from samples.
- `correctness`: oracle identity and per-invocation stdout hashes with retained evidence files; status is checked against those observations.
- `artifacts`, `details`, `executed_commands`: paths/fingerprints and runtime-specific diagnostic evidence.
- `request`, `rerun_command`: settings and a concrete command for repeating the experiment. A report includes that command, the evidence links, and the next step.

### Metric boundaries

- `wall_seconds`: Python measures the `runpy` dispatch through return; Node measures completion of collector-preload setup through the start of the process `exit` event, including native entrypoint loading and pending asynchronous tasks that keep the event loop alive. Both exclude interpreter startup, initial collector imports, and later shutdown hooks. Preloaded modules change import costs. These two runtime intervals are explicitly different and are **not interchangeable**. Neither is a faithful application startup benchmark or HTTP request latency.
- `cpu_seconds`: CPU time across threads in the worker process during that runtime-specific interval; excludes child-process CPU. It is not CPU utilization. CPU less than wall time is not proof of disk/network causality.
- `process_peak_rss_bytes`: process-lifetime high-water mark, including interpreter/collector startup and any profiler overhead, excluding child processes. Python reports this on Linux/macOS; Node converts `process.resourceUsage().maxRSS` KiB to bytes. This is neither current RSS nor summed process-tree RAM.
- `python_peak_bytes`, `python_current_bytes`: Python-tracked allocations in memory mode; native allocations may be absent. Tracing has overhead. A single run does not establish stable memory behavior.
- Node `event_loop_utilization_ratio` and `event_loop_delay_*_seconds`: main-event-loop diagnostics in CPU mode only, with a 10 ms sampling resolution. No artificial zero delay is reported if the histogram has no samples. Utilization is not classified as inherently good/bad. These values are not benchmark-comparable and do not cover worker threads or native child processes.

For child-heavy applications such as FFmpeg launchers, add a separate, authorized process-tree measurement plan. The bundled adapters do not claim process-tree RAM, native bottlenecks, I/O byte counts, query counts, per-request percentiles, or throughput.

## Repeat and compare

After an authorized edit, reuse the recorded request with a fresh output:

```bash
python "$SKILL_DIR/scripts/measure.py" rerun /path/to/results/before.json \
  --revision after --output /path/to/results/after.json
python "$SKILL_DIR/scripts/measure.py" compare \
  /path/to/results/before.json /path/to/results/after.json \
  --output /path/to/results/comparison.json
```

`rerun` **executes the current target code**, not a checkout of the historical source. It checks recorded inputs, the expected-output file, host/settings, and dependency-file identities before running; the worker also checks runtime identity before importing the target. Preserve source/revisions separately if you need to reproduce the old implementation. It never edits the application, installs dependencies, restores files, or authorizes new effects. `compare` executes no application code.

Compatibility checks require the same runtime/environment, collector implementation, workload/target/arguments, declared input/cache conditions, protocol, metric definitions (including scope/units), and output oracle. Source fingerprints and revision labels may change. A different interpreter/dependency/runtime or timing boundary is a separate experiment. There is no force flag to bypass this check. Matching metadata still cannot prove identical external data or system conditions.

For memory profiles, select an allocation metric explicitly:

```bash
python "$SKILL_DIR/scripts/measure.py" compare \
  /path/to/results/memory-before.json /path/to/results/memory-after.json \
  --primary-metric python_peak_bytes --output /path/to/results/memory-comparison.json
```

### Separate observed change, practical impact, and statistical evidence

Each comparable metric reports:

1. **Observed change:** before/after median and mean, min/max, sample count, standard deviation, and descriptive median percentage. A zero baseline yields `null` percentage, never infinity.
2. **Practical impact:** mean change against a configurable relative threshold (default 5%) and optional per-metric absolute threshold. Both must be met. Five percent is a configurable default, not a universal definition of meaningful improvement.
3. **Statistical evidence:** a two-sided independent-sample permutation test of the **difference in means**, followed by Holm correction across the comparable metrics. Defaults are alpha 0.05, at least five samples per group, and up to 9,999 assignments. Enumerate all assignments when feasible; otherwise use deterministic seeded Monte Carlo with the conservative `(extreme + 1) / (draws + 1)` estimate. Exact tests use `extreme / assignments`. No outliers are silently discarded. Min/max overlap is retained only as a descriptive warning.

This deliberately does not claim to implement `pyperf`'s t-test or reuse pyperf file formats. We evaluated its separation of stability checks and significance, then selected a dependency-free permutation method for the shared Python/Node collector. The test assumes independent, exchangeable observations under the null. Sequential batches, thermal drift, cache evolution, and autocorrelation can invalidate that assumption. A p-value is not the probability that an optimization is real or correct. Repeat or interleave controlled A/B experiments when those concerns matter; an inconclusive result is not evidence of equivalence.

Example policy file:

```json
{
  "primary_metric": "wall_seconds",
  "alpha": 0.05,
  "min_samples": 7,
  "min_change_percent": 5,
  "thresholds": {
    "wall_seconds": {"relative_percent": 5, "absolute": 0.001},
    "process_peak_rss_bytes": {"relative_percent": 10, "absolute": 1048576}
  }
}
```

Pass it with `compare --policy /path/to/policy.json`. Absolute thresholds use each metric's declared unit. Unknown metrics/fields and invalid thresholds are rejected. Policy values override the corresponding CLI defaults/options.

Statuses: `verified_improvement` requires a statistically supported, practically meaningful primary-metric improvement, matching passed output oracles on both sides, and no supported material regression among the compared metrics. Without an oracle it is only `observed_improvement`. Any failed oracle overrides speed gains with `correctness_failed`. Other statuses are `regression`, `below_practical_threshold`, and `inconclusive`. Verification is restricted to the measured workload/output; untested paths and metrics are not covered.

### Exit codes and CI

- `0`: artifacts were produced and no requested failure condition occurred. This includes inconclusive comparisons and is not a certification of improvement.
- `1`: `compare --fail-on-regression` found a statistically supported, practically meaningful regression in at least one comparable metric.
- `2`: invalid input, incompatible artifacts, a failed/timeout target, or missing/tampered evidence.
- `3`: output correctness failed; measurement/comparison reports are still preserved.

Choose practical thresholds before inspecting results and review results across repeated CI runs before using them as release gates. Collector output is not cryptographic proof of provenance: someone who edits the entire JSON and its evidence can fabricate a result.

## Known-answer demonstration

This creates and executes only bundled, generated local examples, preserving reports, profiles, datasets, known answers, and historical source copies in a new directory:

```bash
python "$SKILL_DIR/scripts/demo.py" --runtime node --output-dir /path/to/results/node-demo
python "$SKILL_DIR/scripts/demo.py" --runtime python --output-dir /path/to/results/python-demo
```

The example replaces repeated list/array membership with set membership, repeats the unchanged implementation as a control, and measures an intentionally wrong fast implementation. It never edits an existing application. The unchanged control's actual result is preserved; an inconclusive outcome cannot be guaranteed on a noisy machine. Unit tests separately use explicitly synthetic overlapping distributions to verify the inconclusive branch deterministically. Demo performance numbers are not claims about real applications. The demo workspace ends with the intentionally wrong implementation; restore a preserved source copy before manually repeating an earlier implementation.

## Execution boundaries

Subprocesses inherit the environment and the user's permissions; this is not a sandbox. Stdin is disabled. Stderr is saved locally; stdout is discarded unless an output oracle is requested. Nonzero exits, exceptions, missing metrics, and timeouts abort collection. On POSIX, remaining descendants in the same process group are terminated after each invocation, including after normal exit. Detached descendants can escape; other platforms guarantee only direct-child cleanup. Do not launch persistent services with this tool.

External Python tracing/profiling and Node profiling/inspection flags are rejected. Project-internal instrumentation and arbitrary preload hooks are not comprehensively detectable; inspect the environment and workload. Do not place credentials in arguments, workload IDs, selected runtime settings, logs, or reports intended for sharing.

## Primary references

- [Python profiling](https://docs.python.org/3/library/profile.html) and [tracemalloc](https://docs.python.org/3/library/tracemalloc.html).
- [Node CPU profiling](https://nodejs.org/api/cli.html#--cpu-prof), [performance hooks](https://nodejs.org/api/perf_hooks.html), and [resource usage](https://nodejs.org/api/process.html#processresourceusage).
- [pyperf analysis and comparison](https://pyperf.readthedocs.io/en/latest/analyze.html).
- [Independent permutation test assumptions and conservative randomized p-values](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.permutation_test.html). SciPy is a methodological reference, not a dependency.
