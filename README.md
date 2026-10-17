# Agent Skills

A collection of reusable AI agent skills. Each skill has its own source directory, instructions, and supporting resources.

## Available skills

### [Bottleneck Lens](bottleneck-lens/SKILL.md)

**Find the bottleneck. Prove the improvement.**

Profile Python and Node.js workloads using runtime evidence, compare equivalent before/after measurements, and check whether an optimization preserves the expected output. The agent interprets findings and proposes authorized changes; bundled tools provide repeatable collection, comparison, and local reports.

Bottleneck Lens v0.2 includes:

- A [shared JSON measurement contract](bottleneck-lens/references/measurement.schema.json) with raw samples, runtime/input identities, units, and explicit process/thread/startup boundaries.
- Python wall/CPU benchmarks, `cProfile` function evidence, and `tracemalloc` allocation profiles.
- Node ESM/CommonJS benchmarks, V8 CPU profiles, and event-loop diagnostics with `perf_hooks`.
- Exact stdout checks against an independently supplied known answer, with retained per-run evidence.
- Separate observed changes, practical thresholds, and statistical evidence; optional CI failure on supported practical regressions.
- Markdown reports, persistent artifact directories, and recorded requests for rerunning experiments.
- A generated known-answer demonstration covering a real optimization, an unchanged-code control, and an intentionally wrong fast implementation.

Collection/comparison are local and use no external service. The host AI agent's own data handling is separate; this does not imply the model itself runs locally.

## Requirements

- An agent with skill support and permission to read files/run approved local commands.
- Python 3.10+ for the collector CLI and Python workloads.
- Node.js 22+ for Node workloads; Node.js/npm for the `npx` installation method.
- Existing application dependencies and a bounded script/module exercising the operation of interest.

The bundled collector/statistics code uses standard-library and built-in runtime APIs. No additional profiling package, hosted dashboard, or Bottleneck Lens API key is required. The Python supervisor can use `--python` or `--node` to select the target project's interpreter.

## Install

### Install with npx

Run the [Skills CLI](https://github.com/vercel-labs/skills) from the project where you want to use the skill:

```bash
npx skills add Pheem49/agent-skills --skill bottleneck-lens
```

Follow the interactive prompts to choose your agent and installation options. The CLI supports agents including Codex, Claude Code, and Cursor; host behavior still needs validation for the agent you use.

To inspect available skills before installing:

```bash
npx skills add Pheem49/agent-skills --list
```

### Codex: install from GitHub

Ask Codex:

```text
Use $skill-installer to install the skill from
https://github.com/Pheem49/agent-skills/tree/main/bottleneck-lens
```

See the [official skill documentation](https://learn.chatgpt.com/docs/build-skills) for discovery/installation details.

### Install from a checkout

Copy the complete `bottleneck-lens/` directory into your target project's `.agents/skills/` directory, preserving all bundled files. Codex discovers project skills there; restart it if the skill does not appear. Other hosts may use different locations/invocation syntax. The instructions are vendor-neutral; behavior has not been verified across all hosts.

## Use with an agent

```text
Use $bottleneck-lens to investigate this project.
First inspect the code and propose a representative workload, exact commands,
run counts, timeouts, and a durable results directory. Ask before executing
unfamiliar repository code. Report measured bottlenecks with evidence.
Propose optimizations, but ask before changing application code.
```

The workflow saves an uninstrumented baseline, collects separate diagnostic profiles, explains observed facts/hypotheses, applies only authorized changes, checks correctness, and repeats comparable measurements. Default benchmark and Python allocation collections each execute one discarded warmup and seven recorded runs, each in a fresh process; CPU profiles execute once without warmup. Warmup does not preserve application state for later processes.

## Collect, rerun, and compare

Resolve `SKILL_DIR` to the installed skill directory. These commands execute the selected workload: review and authorize it first. Replace example paths/fixtures and create the output parent directory in advance.

```bash
python "$SKILL_DIR/scripts/measure.py" measure \
  --cwd /path/to/project --script workload.mjs --runtime node \
  --workload fixture-v1 --dataset fixture-v1 --input fixtures/input.json \
  --expected-stdout fixtures/expected.txt --revision before \
  --output /path/to/results/before.json
```

For Python use `--script workload.py --runtime python`, or `--module package.module`. An oracle is optional; without `--expected-stdout`, correctness stays `not_assessed` and the automated comparison cannot certify improvement. A supplied oracle checks exact stdout bytes on every invocation and covers only the behavior those bytes establish.

For a diagnostic profile, repeat the collection command with `--mode cpu` and a fresh output name. Python additionally supports `--mode memory`, with repeated fresh-process samples controlled by `--runs` and `--warmups` for allocation comparisons. Diagnostic timings cannot replace uninstrumented benchmark timings. Node uses a collector preload and runs scripts as real entrypoints, preserving CommonJS main guards.

After an authorized change:

```bash
python "$SKILL_DIR/scripts/measure.py" rerun /path/to/results/before.json \
  --revision after --output /path/to/results/after.json
python "$SKILL_DIR/scripts/measure.py" compare \
  /path/to/results/before.json /path/to/results/after.json \
  --output /path/to/results/comparison.json
```

`rerun` executes the current target code using the recorded request and rechecks inputs, recorded environment settings, dependency files, and runtime identity; it does not restore historical source. `compare` executes no workload and rejects incompatible runtimes, inputs, metric scopes/units, collector versions, protocols, or output oracles. Version-1/ad-hoc measurements must be recollected because their missing boundaries cannot safely be inferred.

Each run produces JSON, a Markdown report, and an adjacent `.artifacts/` directory containing evidence. Nothing is overwritten. Keep JSON and evidence together, including when moving results. A failed workload leaves diagnostic files but no successful measurement JSON. Reports lead with findings/evidence/next steps and include exact rerun commands. The agent adds project-specific interpretation in the user's language.

## Interpreting comparisons

Observed median/mean changes, practical impact, statistical support, and correctness are separate fields. Defaults use a 5% practical threshold, five samples per group, alpha 0.05, and a two-sided permutation test of mean differences with Holm correction across compared metrics. These are configurable experiment policies, not universal definitions of a useful improvement. No outliers are silently removed. Range overlap is descriptive, not the statistical test.

- `verified_improvement`: a supported practical primary-metric gain, matching passed output oracles, and no supported practical regression among the compared metrics. Coverage is limited to this workload/output.
- `observed_improvement`: a supported practical primary-metric gain with correctness unassessed.
- `correctness_failed`: an oracle failed; speed gains cannot count as a verified optimization.
- `regression`, `below_practical_threshold`, or `inconclusive`: inspect the evidence, resource tradeoffs, and experiment conditions before acting. Inconclusive does not establish equivalence.

Use `compare --fail-on-regression` to exit 1 for a supported practical regression. Correctness failures exit 3; invalid/incompatible inputs and failed workloads exit 2. Exit 0 includes inconclusive results and does not certify improvement. Select thresholds before viewing results; use `--policy` for per-metric relative/absolute criteria.

Permutation evidence assumes independent, exchangeable observations under the null. System drift, cache evolution, and background load may violate this assumption. Repeat or interleave controlled A/B runs when needed. The method is documented alongside the [measurement contract and statistical policy](bottleneck-lens/references/measurement.md); it does not implement pyperf's t-test.

## Try the full known-answer experiment

With an existing results parent directory:

```bash
python "$SKILL_DIR/scripts/demo.py" --runtime node --output-dir /path/to/results/node-demo
python "$SKILL_DIR/scripts/demo.py" --runtime python --output-dir /path/to/results/python-demo
```

The demo creates its own local workspace and replaces repeated list/array lookups with set lookups. It preserves the baseline, profile, optimized result, unchanged-code control, wrong-answer result, correctness evidence, and source snapshots. The unchanged control's actual result is retained rather than forced to appear inconclusive; deterministic synthetic noisy controls are tested separately. Results describe generated examples, not a performance promise for real applications. The demo workspace ends with the intentionally wrong implementation; historical source copies are retained beside the reports.

## Measurement and execution boundaries

- Time excludes interpreter startup and later shutdown hooks. Preloaded modules alter import costs. Python runpy-to-return and Node preload-to-exit are distinct measurement intervals, not faithful application-startup or HTTP-latency benchmarks.
- CPU and RSS exclude child processes. RSS is a process-lifetime high-water mark, not total process-tree RAM. Python reports RSS on Linux/macOS; Node uses `process.resourceUsage()`.
- Python allocation tracing can miss native allocations. Final live allocations do not explain every transient peak or prove a leak. Node allocation profiling is not implemented.
- Node CPU profiles are main-isolate sampled stacks, include collector startup, and do not show native child stacks. Event-loop diagnostics are separate from benchmarks. Neither adapter directly provides I/O byte counts, query counts, request percentiles, throughput, or native-engine bottlenecks.
- Matching metadata cannot establish all dataset/dependency/cache/load conditions. Explicitly fingerprint relevant inputs/source and report remaining unknowns.
- Subprocesses inherit the environment and user's permissions. This is not a sandbox. POSIX cleanup terminates remaining children in the same process group; detached descendants can escape. Other platforms guarantee only direct-child cleanup.
- The script executes the selected workload without an interactive approval flow. Skill instructions require applicable authorization. Stderr logs, optional stdout, selected environment settings, paths, and arguments may expose application data; inspect results before sharing.

## Repository layout

Each skill lives in its own source directory at the repository root:

```text
.
├── README.md
├── LICENSE
├── .gitignore
├── bottleneck-lens/
│   ├── SKILL.md
│   ├── LICENSE
│   ├── agents/openai.yaml
│   ├── scripts/
│   │   ├── measure.py
│   │   ├── contract.py
│   │   ├── python_worker.py
│   │   ├── node_worker.mjs
│   │   ├── statistics_tools.py
│   │   ├── reporting.py
│   │   └── demo.py
│   └── references/
│       ├── measurement.md
│       └── measurement.schema.json
└── tests/
    ├── test_measure.py
    └── test_v2.py
```

Create additional skill directories beside `bottleneck-lens/`, each with a unique `SKILL.md` name/description and its own focused instructions. Add scripts/references/assets only when needed, include the applicable standalone license, and document purpose/usage here. Keep published source paths stable.

`.agents/skills/` contains local installed copies used for experiments in this checkout. Edit the root source directories when developing skills. Local installations, the root `skills-lock.json`, and Python bytecode are ignored here.

## Development

```bash
python3 -B -m unittest discover -s tests -v
```

Tests cover both adapters, contract compatibility, evidence integrity, correctness gating, timeouts, statistical controls, practical/CI policies, and measured before/after experiments. Node tests require Node.js 22+ and skip when Node is unavailable. These checks do not establish performance gains in a real application or coverage of every host/OS.

## License

Licensed under the [MIT License](LICENSE). A copy is included in the standalone [skill bundle](bottleneck-lens/LICENSE).
