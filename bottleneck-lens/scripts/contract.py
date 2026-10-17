"""Versioned measurement contract shared by runtime adapters (standard library only)."""

import hashlib
import json
import math
from pathlib import Path
import statistics
import re

SCHEMA = 2
VERSION = "0.2.0"


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def output_path(value):
    path = Path(value).absolute()
    if path.exists() or path.is_symlink():
        raise ValueError(f"Output already exists: {path}")
    if not path.parent.is_dir():
        raise ValueError(f"Output parent must exist: {path.parent}")
    return path


def write_json(path, data):
    payload = json.dumps(data, indent=2, allow_nan=False) + "\n"
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(payload)


def summary(samples):
    return {
        key: {"count": len(values), "median": statistics.median(values),
              "mean": statistics.mean(values), "min": min(values), "max": max(values),
              "stdev": statistics.stdev(values) if len(values) > 1 else None}
        for key in sorted(samples[0])
        for values in [[sample[key] for sample in samples]]
    }


def metric_contract(runtime, mode, keys):
    instrumentation = {"benchmark": "none", "cpu": "cProfile" if runtime == "python" else "v8_cpu_prof",
                       "memory": "tracemalloc"}[mode]
    interval = "runpy_dispatch" if runtime == "python" else "preload_until_exit_event"
    result = {}
    for key in keys:
        allocation = key.startswith("python_")
        rss = key == "process_peak_rss_bytes"
        event_loop = key.startswith("event_loop_")
        result[key] = {
            "unit": "seconds" if key.endswith("seconds") else "ratio" if key.endswith("ratio") else "bytes",
            "direction": "neutral" if event_loop else "lower",
            "comparable": mode == "benchmark" or (mode == "memory" and allocation),
            "scope": {
                "process": "python_tracked_allocations" if allocation else "worker_process",
                "threads": "main_event_loop" if event_loop else "all",
                "children": "excluded", "interval": "process_lifetime" if rss else interval,
                "interpreter_startup_included": rss,
                "shutdown_hooks_included": False,
                "instrumentation": instrumentation,
            },
        }
    return result


def correctness_status(checks):
    if checks["method"] == "none":
        return "not_assessed"
    expected = checks["oracle_sha256"]
    return "passed" if checks["observations"] and all(
        row["stdout_sha256"] == expected for row in checks["observations"]) else "failed"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_measurement(data):
    _require(isinstance(data, dict), "Expected a measurement object")
    _require(data.get("schema_version") == SCHEMA,
             "Unsupported measurement schema; recollect version-1/ad-hoc results with the v2 collector")
    _require(data.get("kind") == "measurement", "Expected a measurement object")
    for key in ("workload", "revision", "cwd", "created_utc"):
        _require(isinstance(data.get(key), str) and bool(data[key].strip()), f"Invalid {key}")
    for key in ("collector", "environment", "target", "protocol", "inputs", "metrics", "correctness", "request"):
        _require(isinstance(data.get(key), dict), f"Missing or invalid {key}")
    _require(data.get("mode") in ("benchmark", "cpu", "memory"), "Unknown measurement mode")
    runtime = data["environment"].get("runtime", {})
    _require(isinstance(runtime, dict) and runtime.get("name") in ("python", "node"), "Invalid runtime")
    for key in ("version", "executable"):
        _require(isinstance(runtime.get(key), str) and bool(runtime[key]), f"Missing runtime {key}")
    for key in ("host", "settings", "dependency_files"):
        _require(isinstance(data["environment"].get(key), dict), f"Missing environment {key}")
    for key in ("name", "version", "adapter"):
        _require(isinstance(data["collector"].get(key), str) and bool(data["collector"][key]), f"Invalid collector {key}")
    _require(isinstance(data["collector"].get("files"), dict) and bool(data["collector"]["files"]), "Missing collector fingerprints")
    target = data["target"]
    _require(target.get("kind") in ("script", "module") and isinstance(target.get("value"), str)
             and bool(target["value"]), "Invalid target")
    _require(isinstance(target.get("args"), list) and all(isinstance(v, str) for v in target["args"]), "Invalid target arguments")
    request = data["request"]
    for key in ("cwd", "target", "mode", "workload", "revision"):
        _require(request.get(key) == data[key], f"Rerun request disagrees with measurement {key}")
    _require(request.get("runtime") == runtime["name"] and isinstance(request.get("executable"), str), "Invalid rerun runtime")
    _require(Path(request["executable"]).resolve() == Path(runtime["executable"]).resolve(), "Rerun executable disagrees with measured runtime")
    protocol = data["protocol"]
    for key in ("runs", "warmups"):
        _require(type(protocol.get(key)) is int and protocol[key] >= (1 if key == "runs" else 0), f"Invalid protocol {key}")
    _require(type(protocol.get("timeout_seconds")) in (int, float) and math.isfinite(protocol["timeout_seconds"])
             and protocol["timeout_seconds"] > 0 and protocol.get("fresh_process") is True, "Invalid collection protocol")
    for request_key, protocol_key in (("runs", "runs"), ("warmups", "warmups"), ("timeout", "timeout_seconds")):
        _require(request.get(request_key) == protocol[protocol_key], f"Rerun request disagrees with protocol {protocol_key}")
    _require(isinstance(data["inputs"].get("files"), dict)
             and isinstance(data["inputs"].get("dataset"), str)
             and isinstance(data["inputs"].get("cache_policy"), str), "Invalid input identity")
    for field in ("dataset", "seed", "cache_policy"):
        _require(request.get(field) == data["inputs"].get(field), f"Rerun request disagrees with input {field}")
    _require(isinstance(request.get("input_files"), list) and set(request["input_files"]) == set(data["inputs"]["files"]), "Rerun input files differ")
    sources = data.get("source_fingerprints")
    _require(isinstance(sources, dict) and isinstance(request.get("source_files"), list)
             and set(request["source_files"]) == set(sources), "Invalid source fingerprints")
    for fingerprints in (data["collector"]["files"], data["environment"]["dependency_files"], sources, data["inputs"]["files"]):
        _require(all(isinstance(key, str) and isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value)
                     for key, value in fingerprints.items()), "Invalid file fingerprint")
    _require(data["inputs"].get("seed") is None or isinstance(data["inputs"]["seed"], str), "Invalid seed identity")
    _require(isinstance(data.get("artifacts"), list) and isinstance(data.get("details"), list), "Missing artifacts or details")
    _require(isinstance(data.get("rerun_command"), list) and all(isinstance(v, str) for v in data["rerun_command"]), "Missing rerun command")
    _require(isinstance(data.get("executed_commands"), list) and all(isinstance(cmd, list) and all(isinstance(v, str) for v in cmd)
             for cmd in data["executed_commands"]), "Missing executed commands")
    samples = data.get("samples")
    _require(isinstance(samples, list) and bool(samples), "No samples")
    _require(len(samples) == protocol["runs"], "Sample count does not match protocol")
    _require(all(isinstance(s, dict) for s in samples), "Samples must be metric objects")
    keys = set(samples[0])
    required = {"wall_seconds", "cpu_seconds"}
    if data["mode"] == "memory":
        required |= {"python_current_bytes", "python_peak_bytes"}
        _require(runtime["name"] == "python", "Node allocation profiling is not implemented")
    _require(required <= keys and keys == set(data["metrics"]), "Missing metrics or metric definitions")
    for sample in samples:
        _require(set(sample) == keys and all(type(v) in (int, float) and math.isfinite(v) and v >= 0
                                           for v in sample.values()), "Invalid or inconsistent metric samples")
    for key, descriptor in data["metrics"].items():
        _require(isinstance(descriptor, dict) and descriptor.get("unit") in ("seconds", "bytes", "ratio")
                 and descriptor.get("direction") in ("lower", "higher", "neutral")
                 and type(descriptor.get("comparable")) is bool, f"Invalid definition for {key}")
        scope = descriptor.get("scope", {})
        _require(isinstance(scope, dict), f"Invalid scope for {key}")
        for field in ("process", "threads", "children", "interval", "instrumentation"):
            _require(isinstance(scope.get(field), str) and bool(scope[field]), f"Missing {key} scope {field}")
        for field in ("interpreter_startup_included", "shutdown_hooks_included"):
            _require(type(scope.get(field)) is bool, f"Missing {key} scope {field}")
    checks = data["correctness"]
    _require(checks.get("method") in ("none", "expected_stdout_sha256"), "Unknown correctness method")
    _require(isinstance(checks.get("observations"), list), "Missing correctness observations")
    if checks["method"] != "none":
        _require(isinstance(checks.get("oracle_sha256"), str) and len(checks["oracle_sha256"]) == 64, "Invalid correctness oracle")
        _require(len(checks["observations"]) == protocol["runs"] + protocol["warmups"], "Incomplete correctness coverage")
        for row in checks["observations"]:
            _require(isinstance(row, dict) and isinstance(row.get("stdout_sha256"), str)
                     and isinstance(row.get("path"), str) and isinstance(row.get("run"), str), "Invalid correctness evidence")
        labels = {f"warmup-{index + 1}" for index in range(protocol["warmups"])} | {
            f"run-{index + 1}" for index in range(protocol["runs"])}
        _require({row["run"] for row in checks["observations"]} == labels
                 and len({row["path"] for row in checks["observations"]}) == len(labels), "Duplicate or incomplete correctness observations")
    else:
        _require(not checks["observations"], "Unassessed correctness cannot contain observations")
    _require(checks.get("status") == correctness_status(checks), "Correctness status disagrees with raw observations")
    data["summary"] = summary(samples)
    return data


def read_measurement(path):
    path = Path(path).resolve()
    data = validate_measurement(json.loads(path.read_text(encoding="utf-8")))
    checks = data["correctness"]
    if checks["method"] != "none":
        for row in checks["observations"]:
            evidence = (path.parent / row["path"]).resolve()
            _require(evidence.is_relative_to(path.parent) and evidence.is_file(), "Correctness evidence missing or outside artifact directory")
            _require(digest(evidence) == row["stdout_sha256"], "Correctness evidence hash mismatch")
    return data


def ensure_compatible(before, after):
    for field in ("workload", "environment", "cwd", "target", "mode", "protocol", "collector", "inputs", "metrics"):
        _require(before[field] == after[field], f"Incomparable measurements: {field} differs")
    for field in ("method", "oracle_sha256"):
        _require(before["correctness"].get(field) == after["correctness"].get(field),
                 "Incomparable measurements: correctness oracle differs")
