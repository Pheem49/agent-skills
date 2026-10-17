# Agent Skills

A collection of reusable AI agent skills. Each skill has its own source directory, instructions, and supporting resources.

## Available skills

### [Bottleneck Lens](bottleneck-lens/SKILL.md)

**Find the bottleneck. Prove the improvement.**

An AI agent skill for finding performance bottlenecks in Python applications using runtime measurements, then comparing results before and after an optimization.

Bottleneck Lens gives an agent a repeatable workflow and a local measurement script. The agent inspects the project, agrees on a representative workload, collects evidence, explains likely causes, and verifies authorized changes. If measurements are unavailable, it labels its findings as hypotheses.

## What Bottleneck Lens does

- Guides project inspection and selection of a bounded, reproducible workload.
- Collects uninstrumented wall time and process CPU time across repeated runs.
- Records function call counts, callers, and self/cumulative times with `cProfile`.
- Measures Python allocation peaks and live allocation locations with `tracemalloc`.
- Records process lifetime peak RSS on Linux and macOS.
- Compares compatible before/after artifacts, preserving raw samples and variability.
- Guides correctness checks and regression checks before calling an improvement verified.

The collector produces JSON. The agent interprets the evidence and writes the Markdown report; the script itself does not automatically diagnose or rewrite application code.

## Requirements for Bottleneck Lens

- An agent with skill support and permission to read files and run local commands.
- Python 3.10+ in the target project's existing environment.
- A finite Python script or module that exercises the operation to investigate.

The collector uses only the Python standard library. Application dependencies must already be available in the selected environment. No additional profiling package, hosted dashboard, or Bottleneck Lens API key is required.

## Install

### Install with npx

With Node.js and npm available, run the [Skills CLI](https://github.com/vercel-labs/skills) in your terminal. It downloads the skill directly from this GitHub repository.

Run this command from the project where you want to use the skill:

```bash
npx skills add Pheem49/agent-skills --skill bottleneck-lens
```

Follow the interactive prompts to choose your agent and installation options. The CLI supports agents including Codex, Claude Code, and Cursor. Host behavior still needs validation for the agent you use. Node.js/npm are needed for this installation method; the bundled collector runs in Python.

To inspect available skills before installing:

```bash
npx skills add Pheem49/agent-skills --list
```

### Codex: install from GitHub

Ask Codex to install the skill from this repository:

```text
Use $skill-installer to install the skill from
https://github.com/Pheem49/agent-skills/tree/main/bottleneck-lens
```

The installation URL points to the `main` branch. Codex supports installing skills from other repositories through the skill installer. See the [official skill documentation](https://learn.chatgpt.com/docs/build-skills) for discovery and installation details.

### Install from a downloaded or cloned checkout

Copy the complete `bottleneck-lens/` directory, including its `scripts/`, `references/`, and `agents/` directories, into the target project's `.agents/skills/` directory:

```text
your-project/
└── .agents/
    └── skills/
        └── bottleneck-lens/
            ├── SKILL.md
            ├── LICENSE
            ├── agents/openai.yaml
            ├── references/measurement.md
            └── scripts/measure.py
```

Copy the skill folder, rather than the entire repository. Codex discovers project skills in `.agents/skills`; if the skill does not appear, restart Codex. Other agents may use a different installation location and invocation syntax. The core instructions are vendor-neutral, but installation and behavior have not been verified across all agent products.

## Use with an agent

After installation, try:

```text
Use $bottleneck-lens to investigate this Python project.
First inspect the code and propose a representative workload, the exact
commands, run counts, timeouts, and output paths. Ask before executing
unfamiliar repository code. Report measured bottlenecks with evidence.
Propose optimizations, but ask before changing application code.
```

Once the workload is approved, the workflow is:

1. Save an uninstrumented baseline benchmark.
2. Collect CPU and memory profiles in separate runs.
3. Explain observed bottlenecks, hypotheses, proposed changes, and tradeoffs.
4. Apply an optimization only within the user's authorization.
5. Run correctness checks, repeat equivalent measurements, and compare results.

The default benchmark invokes the workload eight times: one discarded warmup and seven recorded samples, each in a new process. CPU and memory profiles each add one invocation. A warmup in a separate process does not warm the next process's application state.

## Run the collector directly

From this repository's root, using the target project's interpreter, collect a baseline for a reviewed workload:

```bash
python bottleneck-lens/scripts/measure.py measure \
  --cwd /path/to/project \
  --script workload.py \
  --workload fixture-v1-seed42 \
  --revision before \
  --mode benchmark \
  --runs 7 \
  --warmups 1 \
  --timeout 30 \
  --output /path/to/existing-results/before.json
```

Replace the example paths and workload identity. Output parent directories must already exist; existing files are never overwritten. Use `--module package.module` instead of `--script` for module entrypoints. Put workload arguments after `--`. Select `--mode cpu` or `--mode memory` for a separate diagnostic run and give each artifact a fresh name.

After an authorized optimization, repeat the benchmark with the same workload and collection settings, `--revision after`, and a fresh `after.json` path. Then compare:

```bash
python bottleneck-lens/scripts/measure.py compare \
  /path/to/existing-results/before.json \
  /path/to/existing-results/after.json \
  --output /path/to/existing-results/comparison.json
```

Comparison does not execute the workload. It rejects mismatched workload identities, targets, arguments, environments, and collection protocols. It reports observed changes; it does not certify correctness or statistical significance. Negative percentage changes mean less time or memory. Memory profiles can be compared separately; CPU profile timing is diagnostic and is not accepted as a benchmark comparison.

See the [measurement guide](bottleneck-lens/references/measurement.md) for metric definitions, measurement boundaries, and additional tool selection.

## Understanding the result

A useful agent report includes the workload and environment, evidence for each finding, before/after sample statistics, correctness check results, resource tradeoffs, and untested scenarios. JSON artifacts retain the underlying measurements.

An observed reduction in runtime is not automatically a verified optimization. Equivalent workloads, repeat measurements, and passed correctness checks are required. Regression coverage extends only to the scenarios actually tested. No performance improvement percentage is promised in advance.

## Scope and limitations

This is a Python-first MVP for scripts and modules that finish. APIs and background workers need a bounded driver that exercises the relevant operation.

- The bundled collector does not provide HTTP load testing, live process attachment, I/O byte counters, database query counts, or process-tree resource accounting.
- `tracemalloc` measures Python-tracked allocations, which can exclude native allocations. A final snapshot shows live allocations, not every transient allocation or proof of a leak.
- Peak RSS covers the worker process lifetime, including interpreter/collector startup. It excludes child processes and is available only on Linux/macOS.
- Timed execution excludes interpreter startup and shutdown hooks. Collector-preloaded modules affect import costs, so this is not a faithful application startup benchmark.
- `cProfile` and allocation tracing add overhead. Their timings are kept separate from uninstrumented benchmarks. CPU profiles cover the executing thread, not every application thread.
- Matching metadata cannot establish identical datasets, installed dependencies, cache state, or background load. Record those conditions separately.

## Execution and privacy

The skill instructs the agent to obtain authorization before executing unfamiliar repository code and before modifying application code unless that scope is already authorized. These are agent instructions, not an interactive permission system inside the script. Running the collector directly executes the selected workload.

Collection and comparison run locally. The scripts do not upload source or measurement artifacts. The host AI agent's own data handling is separate; local collection does not imply that the AI model itself runs locally.

The workload inherits the environment and runs with the user's permissions. Process separation and timeouts are not a sandbox. Reports contain paths and arguments: keep credentials out of command-line arguments and workload IDs. Target stdout is suppressed, while stderr is inherited.

## Repository layout

This repository keeps each skill in its own directory at the repository root. The current source layout is:

```text
.
├── README.md
├── LICENSE
├── .gitignore
├── bottleneck-lens/
│   ├── SKILL.md
│   ├── LICENSE
│   ├── agents/openai.yaml
│   ├── scripts/measure.py
│   └── references/measurement.md
└── tests/
    └── test_measure.py
```

### Add another skill

Create a new directory beside `bottleneck-lens/`, named after the new skill. For example, a skill named `new-skill` would have its own `new-skill/SKILL.md`. This is an example name, not a bundled skill.

Each skill needs a `SKILL.md` with its own unique `name`, a precise `description`, and instructions for its workflow. Add `scripts/`, `references/`, or `assets/` only when that skill needs them. Include the applicable license with any standalone bundle. Put behavioral tests in `tests/` and document the new skill's purpose and usage in this README.

Skills for different workflows can live side by side in this collection. Keep each skill focused on its own task and keep the existing `bottleneck-lens/` path stable so published installation links continue to work.

The `.agents/skills/` directory holds local installed copies used to try skills in this checkout. Edit the source directories at the repository root when developing skills. Local installed copies and the root `skills-lock.json` are ignored by Git here to keep this distribution repository focused on skill sources.

## Development

Run the behavioral checks from the repository root:

```bash
python3 -B -m unittest discover -s tests -v
```

Tests use generated local synthetic workloads, including successful collection, before/after comparisons, malformed artifacts, failures, timeouts, and POSIX process-group cleanup. They do not establish an improvement in any real application or validate behavior across all agent hosts and operating systems.

The reusable workflow lives in [SKILL.md](bottleneck-lens/SKILL.md), the collector in [measure.py](bottleneck-lens/scripts/measure.py), and the test suite in [tests/test_measure.py](tests/test_measure.py).

## License

Licensed under the [MIT License](LICENSE). A copy is included in the standalone [skill bundle](bottleneck-lens/LICENSE).
