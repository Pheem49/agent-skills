#!/usr/bin/env python3
"""Measure approved finite Python/Node workloads, compare evidence, and write local reports."""

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone

# Resolve bundled helpers when imported by tools as well as when executed as a CLI.
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from contract import (SCHEMA, VERSION, digest, output_path, write_json, summary,
                      metric_contract, correctness_status, validate_measurement,
                      read_measurement, ensure_compatible)
from reporting import write_report
from statistics_tools import compare_metrics

LOCKFILES = ("pyproject.toml", "uv.lock", "poetry.lock", "Pipfile.lock",
             "requirements.txt", "requirements.lock", "pdm.lock", "package.json",
             "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock")
SETTINGS = ("PYTHONHASHSEED", "PYTHONOPTIMIZE", "PYTHONMALLOC", "PYTHONPATH",
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
            "NODE_OPTIONS", "NODE_PATH", "NODE_COMPILE_CACHE", "UV_THREADPOOL_SIZE")


def finish_process(process):
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()
    process.wait()


def execute(command, cwd, timeout, stderr, stdout=None, env=None):
    with Path(stderr).open("xb") as error_stream:
        out_stream = Path(stdout).open("xb") if stdout else None
        try:
            process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                                       stdout=out_stream if out_stream else subprocess.DEVNULL,
                                       stderr=error_stream, env=env, start_new_session=(os.name == "posix"))
            try:
                try:
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    raise ValueError(f"Workload exceeded {timeout}s; collection aborted; log: {stderr}") from exc
                if code:
                    raise ValueError(f"Workload exited with code {code}; collection aborted; log: {stderr}")
            finally:
                finish_process(process)
        finally:
            if out_stream:
                out_stream.close()
    return process.pid


def node_profile(path):
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    nodes = {row["id"]: row for row in profile["nodes"]}
    times, hits = Counter(), Counter(profile.get("samples", []))
    for node, delta in zip(profile.get("samples", []), profile.get("timeDeltas", [])):
        times[node] += delta / 1e6
    rows = []
    for node, amount in times.items():
        frame = nodes[node]["callFrame"]
        rows.append({"file": frame["url"], "line": frame["lineNumber"] + 1,
                     "function": frame["functionName"] or "(anonymous)",
                     "sample_hits": hits[node], "sampled_self_seconds": amount})
    return sorted(rows, key=lambda row: row["sampled_self_seconds"], reverse=True)[:30]


def collect_one(config, executable, directory, label, timeout, capture_stdout):
    config_path = directory / f"{label}.config.json"
    result_path = directory / f"{label}.metrics.json"
    stderr = directory / f"{label}.stderr.log"
    stdout = directory / f"{label}.stdout" if capture_stdout else None
    config = dict(config)
    extension = "pstats" if config["runtime"] == "python" else "cpuprofile"
    profile = directory / f"{label}.{extension}"
    if config["runtime"] == "python":
        config["profile_path"] = str(profile)
    profile_directory = directory / f"{label}.profiles"
    if config["runtime"] == "node" and config["mode"] == "cpu":
        profile_directory.mkdir()
        config["profile_directory"] = str(profile_directory)
    write_json(config_path, config)
    child_env = None
    if config["runtime"] == "python":
        command = [executable, str(SCRIPTS / "python_worker.py"), str(config_path), str(result_path)]
    else:
        # Preserve Node's pid/tid filenames: worker isolates must not overwrite
        # each other, and only tid 0 is summarized as the main-isolate profile.
        flags = ["--cpu-prof", f"--cpu-prof-dir={profile_directory}"] if config["mode"] == "cpu" else []
        command = [executable, *flags, "--import", str(SCRIPTS / "node_worker.mjs"),
                   config["target"]["value"], *config["target"]["args"]]
        child_env = {**os.environ, "BOTTLENECK_LENS_CONFIG": str(config_path),
                     "BOTTLENECK_LENS_RESULT": str(result_path)}
    pid = execute(command, config["cwd"], timeout, stderr, stdout, env=child_env)
    if not result_path.is_file():
        raise ValueError(f"Workload produced no metrics (possibly os._exit); log: {stderr}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if config["mode"] == "cpu":
        if config["runtime"] == "node":
            main_profiles = list(profile_directory.glob(f"CPU.*.{pid}.0.*.cpuprofile"))
            if len(main_profiles) != 1:
                raise ValueError("Expected exactly one identifiable Node main-thread CPU profile")
            profile = main_profiles[0]
        if not profile.is_file():
            raise ValueError("CPU profiler did not produce its expected artifact")
        if config["runtime"] == "node":
            result["detail"]["sampled_functions"] = node_profile(profile)
            result["detail"]["profile_thread_id"] = 0
            result["detail"]["profile_file"] = str(profile.relative_to(directory.parent))
            result["detail"]["profile_scope"] = "V8 main-isolate sampled stacks including Node/collector startup and exit; excludes native child stacks."
    result["command"] = command
    return result, stdout


def resolve_file(value, cwd):
    path = (cwd / value).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Expected a file: {path}")
    return path


def request_from_args(args):
    cwd = Path(args.cwd).resolve(strict=True)
    if not cwd.is_dir():
        raise ValueError("--cwd must be a directory")
    if (args.mode == "benchmark" and args.runs < 3) or args.runs < 1 or args.warmups < 0:
        raise ValueError("Benchmark requires --runs >= 3; other modes require --runs >= 1; --warmups must be nonnegative")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("--timeout must be finite and positive")
    if not args.workload.strip() or not args.revision.strip():
        raise ValueError("Workload and revision cannot be blank")
    is_script = args.script is not None
    value = args.script if is_script else args.module
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Target cannot be blank")
    runtime = args.runtime
    if runtime == "auto":
        runtime = "node" if is_script and Path(value).suffix in (".js", ".mjs", ".cjs") else "python"
    if runtime == "node" and (not is_script or args.mode == "memory"):
        raise ValueError("Node supports --script with benchmark/cpu modes; Python allocation mode is not a Node metric")
    target = {"kind": "script" if is_script else "module",
              "value": str(resolve_file(value, cwd)) if is_script else value,
              "args": args.target_args[1:] if args.target_args[:1] == ["--"] else args.target_args}
    if not is_script and value.startswith("-"):
        raise ValueError("--module must name a Python module")
    executable = shutil.which(args.node if runtime == "node" else args.python)
    if executable is None:
        raise ValueError(f"Runtime executable not found for {runtime}")
    files = [str(resolve_file(value, cwd)) for value in args.input]
    sources = [str(resolve_file(value, cwd)) for value in args.source]
    if is_script and target["value"] not in sources:
        sources.append(target["value"])
    expected = str(resolve_file(args.expected_stdout, cwd)) if args.expected_stdout else None
    return {"cwd": str(cwd), "runtime": runtime, "executable": str(Path(executable).absolute()),
            "target": target, "mode": args.mode, "workload": args.workload, "revision": args.revision,
            "runs": args.runs if args.mode in ("benchmark", "memory") else 1,
            "warmups": args.warmups if args.mode in ("benchmark", "memory") else 0,
            "timeout": args.timeout, "dataset": args.dataset or args.workload,
            "seed": args.seed, "cache_policy": args.cache_policy,
            "input_files": files, "source_files": sources, "expected_stdout": expected}


def host_identity():
    return {"platform": platform.platform(), "machine": platform.machine(),
            "hostname": platform.node(), "logical_cpus": os.cpu_count()}


def collect_request(request, output_value, report_value=None, expected_environment=None):
    output = output_path(output_value)
    report = output_path(report_value or output.with_suffix(".md"))
    if output == report:
        raise ValueError("JSON and Markdown output paths must differ")
    directory = output.with_suffix(".artifacts")
    if directory in (output, report):
        raise ValueError("JSON, Markdown, and artifact directory paths must differ")
    if directory.exists():
        raise ValueError(f"Artifacts already exist; choose a fresh output name: {directory}")
    cwd = Path(request["cwd"])
    inputs = {path: digest(path) for path in request["input_files"]}
    sources = {path: digest(path) for path in request["source_files"]}
    dependencies = {name: digest(cwd / name) for name in LOCKFILES if (cwd / name).is_file()}
    host = host_identity()
    settings = {key: os.environ.get(key) for key in SETTINGS}
    if expected_environment:
        for key, current in (("host", host), ("settings", settings), ("dependency_files", dependencies)):
            if current != expected_environment[key]:
                raise ValueError(f"Recorded environment {key} changed; use a separate experiment")
    collector_files = {path.name: digest(path) for path in sorted(SCRIPTS.glob("*.py"))}
    collector_files["node_worker.mjs"] = digest(SCRIPTS / "node_worker.mjs")
    checks = {"method": "expected_stdout_sha256" if request["expected_stdout"] else "none",
              "status": "not_assessed", "oracle_sha256": digest(request["expected_stdout"]) if request["expected_stdout"] else None,
              "observations": []}
    directory.mkdir()
    if request["expected_stdout"]:
        shutil.copyfile(request["expected_stdout"], directory / "oracle.expected")
    config = {key: request[key] for key in ("cwd", "runtime", "target", "mode")}
    if expected_environment:
        config["expected_runtime"] = expected_environment["runtime"]
    collected, commands = [], []
    runtime = None
    for index in range(request["warmups"] + request["runs"]):
        label = f"warmup-{index + 1}" if index < request["warmups"] else f"run-{index - request['warmups'] + 1}"
        result, stdout = collect_one(config, request["executable"], directory, label,
                                     request["timeout"], request["expected_stdout"] is not None)
        if runtime is not None and result["runtime"] != runtime:
            raise ValueError("Runtime identity changed during collection")
        runtime = result["runtime"]
        commands.append(result["command"])
        if stdout:
            checks["observations"].append({"run": label, "path": str(stdout.relative_to(output.parent)),
                                            "stdout_sha256": digest(stdout)})
        if index >= request["warmups"]:
            collected.append(result)
    for path, fingerprint in {**inputs, **sources}.items():
        if digest(path) != fingerprint:
            raise ValueError(f"Input or source changed during collection; results discarded: {path}")
    if {name: digest(cwd / name) for name in dependencies} != dependencies:
        raise ValueError("Dependency metadata changed during collection")
    if request["expected_stdout"] and digest(request["expected_stdout"]) != checks["oracle_sha256"]:
        raise ValueError("Correctness oracle changed during collection")
    checks["status"] = correctness_status(checks)
    samples = [row["metrics"] for row in collected]
    artifacts = [{"role": path.name, "path": str(path.relative_to(output.parent)), "sha256": digest(path)}
                 for path in sorted(directory.rglob("*")) if path.is_file()]
    data = {
        "schema_version": SCHEMA, "kind": "measurement", "created_utc": datetime.now(timezone.utc).isoformat(),
        "collector": {"name": "bottleneck-lens", "version": VERSION,
                      "adapter": f"{request['runtime']}-v1", "files": collector_files},
        "workload": request["workload"], "revision": request["revision"],
        "source_fingerprints": sources, "script_sha256": sources.get(request["target"]["value"]),
        "environment": {"runtime": runtime, "host": host, "settings": settings, "dependency_files": dependencies},
        "cwd": request["cwd"], "target": request["target"], "mode": request["mode"],
        "inputs": {"dataset": request["dataset"], "seed": request["seed"], "files": inputs, "cache_policy": request["cache_policy"]},
        "protocol": {"runs": request["runs"], "warmups": request["warmups"],
                     "timeout_seconds": request["timeout"], "fresh_process": True},
        "metrics": metric_contract(request["runtime"], request["mode"], samples[0]),
        "samples": samples, "summary": summary(samples),
        "details": [row["detail"] for row in collected] if request["mode"] != "benchmark" else [],
        "correctness": checks, "artifacts": artifacts, "request": request, "executed_commands": commands,
        "rerun_command": [sys.executable, str(SCRIPTS / "measure.py"), "rerun", str(output),
                          "--output", str(output.with_name(output.stem + "-rerun.json")), "--revision", "after"],
    }
    validate_measurement(data)
    write_json(output, data)
    write_report(report, data, artifact_base=output.parent)
    print(f"Saved {request['mode']} measurement ({len(samples)} samples): {output}\nReport: {report}\nCorrectness: {checks['status']}")
    return 3 if checks["status"] == "failed" else 0


def measure(args):
    # Check destinations before resolving or executing any target code.
    output_path(args.output)
    return collect_request(request_from_args(args), args.output, args.report)


def policy_from_args(args):
    policy = {"primary_metric": args.primary_metric, "min_change_percent": args.min_change_percent,
              "alpha": args.alpha, "min_samples": args.min_samples, "permutations": args.permutations,
              "seed": args.seed, "thresholds": {}}
    if args.policy:
        custom = json.loads(Path(args.policy).read_text(encoding="utf-8"))
        if not isinstance(custom, dict) or set(custom) - set(policy):
            raise ValueError("Policy contains unknown fields")
        policy.update(custom)
    if not isinstance(policy["primary_metric"], str) or not policy["primary_metric"]:
        raise ValueError("Primary metric must be a nonempty string")
    for key in ("alpha", "min_change_percent"):
        if type(policy[key]) not in (int, float) or not math.isfinite(policy[key]):
            raise ValueError(f"Invalid policy {key}")
    if not 0 < policy["alpha"] < 1 or policy["min_change_percent"] < 0:
        raise ValueError("Alpha must be between 0 and 1; practical threshold must be nonnegative")
    for key in ("min_samples", "permutations", "seed"):
        if type(policy[key]) is not int:
            raise ValueError(f"Invalid policy {key}")
    if policy["min_samples"] < 3 or not 99 <= policy["permutations"] <= 100000:
        raise ValueError("Use min_samples >= 3 and 99 <= permutations <= 100000")
    if not isinstance(policy["thresholds"], dict):
        raise ValueError("Metric thresholds must be an object")
    for threshold in policy["thresholds"].values():
        if not isinstance(threshold, dict) or set(threshold) - {"relative_percent", "absolute"}:
            raise ValueError("Invalid metric threshold")
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in threshold.values()):
            raise ValueError("Thresholds must be finite nonnegative numbers")
    return policy


def compare(args):
    output = output_path(args.output)
    report = output_path(args.report or output.with_suffix(".md"))
    if output == report:
        raise ValueError("JSON and Markdown output paths must differ")
    before, after = read_measurement(args.before), read_measurement(args.after)
    ensure_compatible(before, after)
    if before["mode"] == "cpu":
        raise ValueError("CPU profiles diagnose call paths; compare benchmark timing instead")
    policy = policy_from_args(args)
    metrics = compare_metrics(before, after, policy)
    if not metrics or policy["primary_metric"] not in metrics:
        raise ValueError("Primary metric must be available and comparable (memory mode: use --primary-metric python_peak_bytes)")
    if set(policy["thresholds"]) - set(metrics):
        raise ValueError("Policy thresholds name unavailable or non-comparable metrics")
    checks = [before["correctness"]["status"], after["correctness"]["status"]]
    correctness = "failed" if "failed" in checks else "passed" if checks == ["passed", "passed"] else "not_assessed"
    regressions = [key for key, row in metrics.items() if row["assessment"] == "regression"]
    primary = metrics[policy["primary_metric"]]["assessment"]
    if correctness == "failed":
        status = "correctness_failed"
    elif regressions:
        status = "regression"
    elif primary == "improvement":
        status = "verified_improvement" if correctness == "passed" else "observed_improvement"
    elif primary == "below_practical_threshold":
        status = "below_practical_threshold"
    else:
        status = "inconclusive"
    conclusions = {
        "correctness_failed": "The output oracle failed. Any measured speedup is invalid as a verified optimization.",
        "regression": "At least one measured resource has a statistically supported, practically meaningful regression; inspect tradeoffs.",
        "verified_improvement": "The primary metric improved under the recorded policy and both output-oracle checks passed; verification is limited to this workload.",
        "observed_improvement": "The primary metric improved under the recorded policy, but correctness has not been assessed.",
        "below_practical_threshold": "There is evidence of a difference, but its magnitude is below the configured practical threshold.",
        "inconclusive": "The measurements do not provide sufficient evidence of a meaningful improvement or regression under this policy.",
    }
    data = {"schema_version": SCHEMA, "kind": "comparison", "status": status,
            "before_artifact": str(Path(args.before).resolve()), "after_artifact": str(Path(args.after).resolve()),
            "before_revision": before["revision"], "after_revision": after["revision"],
            "workload": before["workload"], "mode": before["mode"], "metrics": metrics,
            "correctness": correctness, "regressions": regressions, "policy": policy,
            "conclusion": conclusions[status],
            "next_step": "Inspect the metric evidence and correctness outputs; repeat representative workloads or interleave A/B runs if drift is plausible.",
            "note": "Negative change means lower values. Median changes are descriptive; tests and practical decisions use means. "
                    "Range overlap is not a significance test. Permutation tests assume independent exchangeable samples; "
                    "p-values are not probabilities that an optimization is correct. Matching metadata cannot prove identical system load.",
            }
    write_json(output, data)
    write_report(report, data)
    print(f"Saved comparison: {output}\nReport: {report}\nStatus: {status}")
    return 3 if correctness == "failed" else 1 if args.fail_on_regression and regressions else 0


def rerun(args):
    before = read_measurement(args.measurement)
    request = dict(before["request"])
    for path, fingerprint in before["inputs"]["files"].items():
        if digest(path) != fingerprint:
            raise ValueError(f"Recorded dataset changed: {path}")
    if request["expected_stdout"] and digest(request["expected_stdout"]) != before["correctness"]["oracle_sha256"]:
        raise ValueError("Recorded correctness oracle changed")
    if args.revision:
        request["revision"] = args.revision
    return collect_request(request, args.output, args.report, expected_environment=before["environment"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("measure", help="Execute an approved finite workload")
    collect.add_argument("--cwd", required=True)
    target = collect.add_mutually_exclusive_group(required=True)
    target.add_argument("--script")
    target.add_argument("--module")
    collect.add_argument("--runtime", choices=("auto", "python", "node"), default="auto")
    collect.add_argument("--python", default=sys.executable, help="Project Python executable")
    collect.add_argument("--node", default="node", help="Project Node executable (22+)")
    collect.add_argument("--workload", required=True)
    collect.add_argument("--revision", default="unspecified")
    collect.add_argument("--mode", choices=("benchmark", "cpu", "memory"), default="benchmark")
    collect.add_argument("--runs", type=int, default=7)
    collect.add_argument("--warmups", type=int, default=1)
    collect.add_argument("--timeout", type=float, default=30)
    collect.add_argument("--dataset", help="Dataset identity (defaults to workload)")
    collect.add_argument("--seed", help="Recorded identity only; the workload must apply the seed")
    collect.add_argument("--cache-policy", default="Fresh process; OS cache uncontrolled")
    collect.add_argument("--input", action="append", default=[], help="Dataset file to fingerprint; repeatable")
    collect.add_argument("--source", action="append", default=[], help="Additional source file to fingerprint; repeatable")
    collect.add_argument("--expected-stdout", help="Known-answer file; exact stdout bytes checked on every invocation")
    collect.add_argument("--output", required=True)
    collect.add_argument("--report", help="Markdown path (default: output with .md suffix)")
    collect.add_argument("target_args", nargs=argparse.REMAINDER)
    diff = commands.add_parser("compare", help="Compare compatible artifacts; executes no workload")
    diff.add_argument("before")
    diff.add_argument("after")
    diff.add_argument("--output", required=True)
    diff.add_argument("--report")
    diff.add_argument("--primary-metric", default="wall_seconds")
    diff.add_argument("--min-change-percent", type=float, default=5)
    diff.add_argument("--alpha", type=float, default=0.05)
    diff.add_argument("--min-samples", type=int, default=5)
    diff.add_argument("--permutations", type=int, default=9999)
    diff.add_argument("--seed", type=int, default=0)
    diff.add_argument("--policy", help="JSON policy with per-metric relative/absolute thresholds")
    diff.add_argument("--fail-on-regression", action="store_true", help="Exit 1 for a supported, material regression")
    repeat = commands.add_parser("rerun", help="Execute the recorded workload again; review/authorize first")
    repeat.add_argument("measurement")
    repeat.add_argument("--output", required=True)
    repeat.add_argument("--report")
    repeat.add_argument("--revision")
    args = parser.parse_args()
    try:
        return {"measure": measure, "compare": compare, "rerun": rerun}[args.command](args)
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
