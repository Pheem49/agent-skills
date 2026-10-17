---
name: bottleneck-lens
license: MIT
description: Profile Python workloads to find CPU, memory, and latency bottlenecks using runtime evidence, then compare before/after measurements and check regressions. Use when investigating slow Python applications, excessive resource use, or verifying an optimization; source inspection alone is not a measured finding.
---

# Bottleneck Lens

Find the bottleneck. Prove the improvement.

Python-first MVP. Keep collection local; do not upload source, traces, or reports by default. The portable instructions and standard-library scripts do not require a particular agent vendor. Resolve bundled paths relative to this SKILL.md. Scripts require Python 3.10+ in the project's existing environment.

## Discover and agree on the workload

Read project instructions, manifests, lockfiles, entrypoints, and existing performance/correctness tests without importing project modules or executing setup hooks. Identify language, framework, interpreter, dependencies, and the actual slow operation. For another language, explain the Python collector's boundary and propose a suitable profiler rather than pretending these scripts support it.

Choose a representative, bounded workload with a stable dataset, seed, concurrency, cache policy, and expected output. Prefer a trusted existing benchmark or a small reviewed driver that asserts the result. Test collection and application imports also execute code. If no runnable workload is available, deliver hypotheses and a collection plan; never invent measurements.

Before executing an unfamiliar repository, show the exact interpreter, working directory, command and arguments, number of invocations, timeout, output paths, and possible database/network/file effects. Obtain authorization for that concrete workload if not already covered by the conversation. Permission to inspect or profile is not permission to modify application code. Ask before changes unless the user has already authorized optimization. Do not ask again for an unchanged, already approved scope. Installing dependencies, attaching to processes, and accessing production require their own applicable authorization.

The collector runs code with the user's permissions: subprocess separation and timeouts are not a security sandbox. Use fixtures or a test database when needed. Never pass credentials in workload IDs or command-line arguments; arguments and paths are recorded locally. Reports can reveal internal paths. Do not run unknown commands merely to discover what they do.

## Collect evidence

Read [references/measurement.md](references/measurement.md) for commands, metric definitions, and tool selection. Execute the bundled collector only for the authorized workload.

- First collect an **uninstrumented benchmark**. Save all samples, environment metadata, workload identity, and code revision. Every sample uses a new process. One discarded warmup is also a new process; it does not warm application state in later processes.
- Collect CPU and Python allocation profiles in **separate invocations**. Their timings are diagnostic and cannot be substituted for benchmark timings. Default benchmark collection executes the workload eight times (one warmup, seven recorded runs); each profile adds one invocation.
- Preserve the baseline before editing. Use fresh output names: the scripts refuse to overwrite artifacts. Record input hashes, seed, dependency versions, load and cache conditions in the report; automatic metadata cannot establish these for you.
- Prefer a finite driver for a Python API or worker. The bundled tool is not an HTTP load generator, live process sampler, thread-wide CPU profiler, or process-tree monitor. It does not directly measure I/O bytes, query counts, request percentiles, or throughput. When needed, choose additional instrumentation with the user and record its scope/overhead.

## Diagnose and propose

For each finding, cite the artifact, function/file/line, call count or allocation size, and relevant self/cumulative time. Distinguish **observed fact**, **hypothesis**, and **proposed change**. Cumulative times overlap across the call tree; never add them into a percentage of total time. High cumulative time identifies an expensive path, not necessarily the function to rewrite.

CPU time below wall time can suggest waiting, but does not prove disk, database, or network causality. A repeated function call is not proof of N+1 queries: obtain query counts/spans and inspect the loop. Allocation snapshots describe live allocations at snapshot time, not all transient allocations or a leak. Confirm suspected leaks with repeated workloads and retention evidence. State unknowns explicitly.

Prioritize changes by measured contribution, likely benefit, implementation cost, correctness risk, and resource tradeoffs. Describe cache invalidation, ordering, transaction, concurrency, and memory effects where relevant. Do not promise a percentage improvement before measuring it.

## Optimize and verify within authorization

Make the smallest authorized change and preserve unrelated work. Run relevant correctness tests and compare outputs/invariants on the same fixture. A successful process exit alone is not correctness verification.

Repeat the **same benchmark and affected profiles** with the same environment, workload, seed, cache policy, run count, and timeout. Compare uninstrumented timing to uninstrumented timing, and like-for-like memory metrics. If a dependency/interpreter/workload changes, report it as a separate experiment rather than bypassing incompatibility checks.

Use the bundled comparison command for observed deltas; it never declares correctness or statistical significance. Inspect variability, rerun or interleave A/B measurements when noise or system drift could explain the change. Run representative neighboring workloads if they exist and are authorized. Claim regression coverage only for tested scenarios. Stop expanding experiments when the agreed question is answered, a run fails, authorization boundaries are reached, or comparable measurements cannot be obtained.

## Deliver the result

Use the user's language. Provide a concise Markdown report plus the measured JSON artifacts. Include:

- Scope, command, environment/revisions, inputs, repetition count, and measurement boundaries.
- Findings with evidence and confidence; separate unmeasured suggestions.
- Changes made or proposed, tradeoffs, and correctness checks with commands/results.
- Before/after medians, min/max, sample count, and change percentages for each comparable metric. Positive change in the comparison JSON means more time/memory; negative means less. Mark zero baselines as undefined.
- Status: **measured baseline**, **inconclusive**, **observed improvement / regression**, or **verified improvement**. Reserve verified improvement for comparable repeat measurements plus passed correctness checks. A gain in speed with increased memory is a tradeoff, not an unqualified win.
- Untested paths, unavailable metrics, noise, and next action. Clearly label synthetic demonstrations and do not reuse their numbers as application results.

Markdown/JSON files are the portable baseline; an agent may add a chart using its available presentation tools. Do not depend on a hosted dashboard, external AI endpoint, proprietary connector, or optional chart tool to complete this workflow.
