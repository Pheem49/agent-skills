#!/usr/bin/env python3
"""Local, finite Python workload measurement. Does not sandbox executed code."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import runpy
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone


SCHEMA = 1
LOCKFILES = ("pyproject.toml", "uv.lock", "poetry.lock", "Pipfile.lock",
             "requirements.txt", "requirements.lock", "pdm.lock")
SETTINGS = ("PYTHONHASHSEED", "PYTHONOPTIMIZE", "PYTHONMALLOC",
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    payload = json.dumps(data, indent=2, allow_nan=False) + "\n"
    # Exclusive creation protects existing baseline artifacts.
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(payload)


def output_path(value):
    path = Path(value).absolute()
    if path.exists() or path.is_symlink():
        raise ValueError(f"Output already exists: {path}")
    if not path.parent.is_dir():
        raise ValueError(f"Output parent must exist: {path.parent}")
    return path


def summary(samples):
    keys = set.intersection(*(set(sample) for sample in samples))
    return {
        key: {"count": len(values), "median": statistics.median(values),
              "min": min(values), "max": max(values),
              "stdev": statistics.stdev(values) if len(values) > 1 else None}
        for key in sorted(keys)
        for values in [[sample[key] for sample in samples]]
    }


def run_target(config):
    target = config["target"]
    sys.argv = [target["value"], *target["args"]]
    sys.path.insert(0, str(Path(target["value"]).parent)
                    if target["kind"] == "script" else os.getcwd())
    try:
        if target["kind"] == "script":
            runpy.run_path(target["value"], run_name="__main__")
        else:
            runpy.run_module(target["value"], run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        if exc.code is not None and exc.code != 0:
            raise


def worker(config_path, result_path):
    import tracemalloc

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    mode = config["mode"]
    if tracemalloc.is_tracing() or sys.getprofile() or sys.gettrace():
        raise ValueError("Disable externally enabled tracing/profiling before collection")
    profiler = None
    if mode == "cpu":
        import cProfile
        import pstats

        profiler = cProfile.Profile()
        profiler.enable()
    elif mode == "memory":
        tracemalloc.start(1)
    cpu_start, wall_start = time.process_time(), time.perf_counter()
    run_target(config)
    wall, cpu = time.perf_counter() - wall_start, time.process_time() - cpu_start
    if profiler is not None:
        profiler.disable()
    metrics = {"wall_seconds": wall, "cpu_seconds": cpu}
    detail = {}
    if mode == "memory":
        current, peak = tracemalloc.get_traced_memory()
        snapshot = tracemalloc.take_snapshot()
        tracemalloc.stop()
        metrics.update(python_current_bytes=current, python_peak_bytes=peak)
        detail["live_allocations"] = [
            {"file": item.traceback[0].filename, "line": item.traceback[0].lineno,
             "bytes": item.size, "blocks": item.count}
            for item in snapshot.statistics("lineno")[:30]
        ]
    # Capture lifetime RSS before allocating the report's function rows.
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        metrics["process_peak_rss_bytes"] = rss * (1024 if sys.platform.startswith("linux") else 1)
    if profiler is not None:
        rows = []
        for (file, line, function), (primitive, calls, own, cumulative, callers) in pstats.Stats(profiler).stats.items():
            rows.append({"file": file, "line": line, "function": function,
                         "primitive_calls": primitive, "calls": calls,
                         "self_seconds": own, "cumulative_seconds": cumulative,
                         "callers": [{"file": f, "line": n, "function": fn,
                                      "stats": list(values)}
                                     for (f, n, fn), values in sorted(callers.items())]})
        detail["functions"] = sorted(rows, key=lambda row: row["cumulative_seconds"], reverse=True)
    write_json(result_path, {"metrics": metrics, "detail": detail})


def collect_one(config, timeout):
    with tempfile.TemporaryDirectory(prefix="bottleneck-lens-profile-") as directory:
        config_path = Path(directory) / "config.json"
        result_path = Path(directory) / "result.json"
        write_json(config_path, config)
        command = [sys.executable, str(Path(__file__).resolve()), "__worker__",
                   str(config_path), str(result_path)]
        process = subprocess.Popen(
            command, cwd=config["cwd"], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, start_new_session=(os.name == "posix"))
        try:
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                raise ValueError(f"Workload exceeded {timeout}s; collection aborted") from exc
            if code:
                raise ValueError(f"Workload exited with code {code}; collection aborted")
        finally:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
            process.wait()
        if not result_path.is_file():
            raise ValueError("Workload produced no metrics (possibly os._exit); collection aborted")
        return json.loads(result_path.read_text(encoding="utf-8"))


def measure(args):
    output = output_path(args.output)
    cwd = Path(args.cwd).resolve(strict=True)
    if not cwd.is_dir():
        raise ValueError("--cwd must be a directory")
    if args.runs < 3 or args.warmups < 0:
        raise ValueError("Benchmark requires --runs >= 3 and --warmups >= 0")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("--timeout must be finite and positive")
    if not args.workload.strip():
        raise ValueError("--workload cannot be blank")
    is_script = args.script is not None
    target = {"kind": "script" if is_script else "module",
              "value": args.script if is_script else args.module,
              "args": args.target_args[1:] if args.target_args[:1] == ["--"] else args.target_args}
    source_hash = None
    if not target["value"].strip():
        raise ValueError("Target cannot be blank")
    if is_script:
        path = (cwd / args.script).resolve(strict=True)
        if not path.is_file():
            raise ValueError("--script must name a file")
        target["value"] = str(path)
        source_hash = digest(path)
    elif target["value"].startswith("-"):
        raise ValueError("--module must name a Python module")
    environment = {
        "python": sys.version, "executable": sys.executable,
        "implementation": platform.python_implementation(),
        "platform": platform.platform(), "machine": platform.machine(),
        "host": platform.node(),
        "settings": {key: os.environ.get(key) for key in SETTINGS},
        "dependency_files": {name: digest(cwd / name) for name in LOCKFILES if (cwd / name).is_file()},
    }
    config = {"cwd": str(cwd), "target": target, "mode": args.mode}
    runs = args.runs if args.mode == "benchmark" else 1
    warmups = args.warmups if args.mode == "benchmark" else 0
    for _ in range(warmups):
        collect_one(config, args.timeout)
    collected = [collect_one(config, args.timeout) for _ in range(runs)]
    if source_hash is not None and digest(target["value"]) != source_hash:
        raise ValueError("Target script changed during collection; results discarded")
    samples = [item["metrics"] for item in collected]
    result = {
        "schema_version": SCHEMA, "kind": "measurement",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "workload": args.workload, "revision": args.revision,
        "script_sha256": source_hash, "environment": environment,
        **config, "protocol": {"runs": runs, "warmups": warmups,
                               "timeout_seconds": args.timeout,
                               "runner_sha256": digest(__file__), "fresh_process": True},
        "samples": samples, "summary": summary(samples),
        "details": [item["detail"] for item in collected] if args.mode != "benchmark" else [],
        "correctness": "not_assessed",
    }
    write_json(output, result)
    print(f"Saved {args.mode} measurement ({runs} samples): {output}")


def read_measurement(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA or data.get("kind") != "measurement":
        raise ValueError(f"Unsupported measurement format: {path}")
    for field in ("workload", "environment", "cwd", "target", "mode", "protocol"):
        if field not in data:
            raise ValueError(f"Missing {field}: {path}")
    if data["mode"] not in ("benchmark", "cpu", "memory"):
        raise ValueError(f"Unknown measurement mode: {path}")
    samples = data.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError(f"No samples: {path}")
    if not isinstance(data["protocol"], dict) or data["protocol"].get("runs") != len(samples):
        raise ValueError(f"Sample count does not match protocol: {path}")
    if not all(isinstance(sample, dict) for sample in samples):
        raise ValueError(f"Samples must be metric objects: {path}")
    keys = set(samples[0])
    required = {"wall_seconds", "cpu_seconds"}
    if data["mode"] == "memory":
        required |= {"python_current_bytes", "python_peak_bytes"}
    if not required <= keys:
        raise ValueError(f"Missing required metrics: {path}")
    for sample in samples:
        if set(sample) != keys or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
                                     for v in sample.values()):
            raise ValueError(f"Invalid or inconsistent samples: {path}")
    # Recompute from raw samples rather than trusting cached summary data.
    data["summary"] = summary(samples)
    return data


def compare(args):
    output = output_path(args.output)
    before, after = read_measurement(args.before), read_measurement(args.after)
    for field in ("workload", "environment", "cwd", "target", "mode", "protocol"):
        if before[field] != after[field]:
            raise ValueError(f"Incomparable measurements: {field} differs")
    if before["mode"] == "cpu":
        raise ValueError("CPU profiles diagnose call paths; compare benchmark timing instead")
    keys = set(before["summary"])
    if keys != set(after["summary"]):
        raise ValueError("Incomparable measurements: available metrics differ")
    if before["mode"] == "memory":
        keys &= {"python_current_bytes", "python_peak_bytes"}
    changes = {}
    for key in sorted(keys):
        first, last = before["summary"][key], after["summary"][key]
        b, a = first["median"], last["median"]
        variability = "insufficient_samples" if min(first["count"], last["count"]) < 3 else (
            "ranges_overlap" if max(first["min"], last["min"]) <= min(first["max"], last["max"])
            else "ranges_separated")
        changes[key] = {
            "before": first, "after": last,
            "change_percent": (a - b) / b * 100 if b else None,
            "direction": "lower" if a < b else "higher" if a > b else "unchanged",
            "variability": variability,
        }
    write_json(output, {
        "schema_version": SCHEMA, "kind": "comparison", "status": "observed_only",
        "before_artifact": str(Path(args.before).resolve()),
        "after_artifact": str(Path(args.after).resolve()),
        "workload": before["workload"], "mode": before["mode"],
        "before_revision": before.get("revision"), "after_revision": after.get("revision"),
        "metrics": changes, "correctness": "not_assessed",
        "note": "Negative change means less time/memory. Range overlap is not a significance test. "
                "Matching metadata does not prove equivalent inputs, dependencies, or system load.",
    })
    print(f"Saved observed comparison: {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("measure", help="Execute an approved finite workload")
    collect.add_argument("--cwd", required=True)
    target = collect.add_mutually_exclusive_group(required=True)
    target.add_argument("--script")
    target.add_argument("--module")
    collect.add_argument("--workload", required=True, help="Stable fixture/seed/scenario identity")
    collect.add_argument("--revision", default="unspecified", help="Code revision label")
    collect.add_argument("--mode", choices=("benchmark", "cpu", "memory"), default="benchmark")
    collect.add_argument("--runs", type=int, default=7)
    collect.add_argument("--warmups", type=int, default=1)
    collect.add_argument("--timeout", type=float, default=30)
    collect.add_argument("--output", required=True)
    collect.add_argument("target_args", nargs=argparse.REMAINDER)
    diff = commands.add_parser("compare", help="Compare compatible artifacts; executes no workload")
    diff.add_argument("before")
    diff.add_argument("after")
    diff.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        (measure if args.command == "measure" else compare)(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "__worker__":
        worker(sys.argv[2], sys.argv[3])
    else:
        main()
