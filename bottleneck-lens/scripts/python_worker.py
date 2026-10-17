"""Private worker: executes the approved target; never import it for discovery."""

import json
import os
from pathlib import Path
import platform
import runpy
import sys
import time
import tracemalloc


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


def main(config_path, result_path):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if sys.version_info < (3, 10):
        raise ValueError("Python 3.10+ is required")
    if tracemalloc.is_tracing() or sys.getprofile() or sys.gettrace():
        raise ValueError("Disable externally enabled tracing/profiling before collection")
    runtime = {"name": "python", "version": sys.version, "executable": sys.executable,
               "implementation": platform.python_implementation()}
    if config.get("expected_runtime") is not None and config["expected_runtime"] != runtime:
        raise ValueError("Recorded Python runtime changed; target was not executed")
    profiler = None
    if config["mode"] == "cpu":
        import cProfile
        import pstats

        profiler = cProfile.Profile()
        profiler.enable()
    elif config["mode"] == "memory":
        tracemalloc.start(1)
    cpu_start, wall_start = time.process_time(), time.perf_counter()
    run_target(config)
    wall, cpu = time.perf_counter() - wall_start, time.process_time() - cpu_start
    if profiler is not None:
        profiler.disable()
    metrics = {"wall_seconds": wall, "cpu_seconds": cpu}
    detail = {}
    if config["mode"] == "memory":
        current, peak = tracemalloc.get_traced_memory()
        metrics.update(python_current_bytes=current, python_peak_bytes=peak)
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        import resource

        metrics["process_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
            1024 if sys.platform.startswith("linux") else 1)
    if config["mode"] == "memory":
        snapshot = tracemalloc.take_snapshot()
        tracemalloc.stop()
        detail["live_allocations"] = [
            {"file": item.traceback[0].filename, "line": item.traceback[0].lineno,
             "bytes": item.size, "blocks": item.count}
            for item in snapshot.statistics("lineno")[:30]]
    if profiler is not None:
        profiler.dump_stats(config["profile_path"])
        rows = []
        for (file, line, function), (primitive, calls, own, cumulative, callers) in pstats.Stats(profiler).stats.items():
            rows.append({"file": file, "line": line, "function": function,
                         "primitive_calls": primitive, "calls": calls,
                         "self_seconds": own, "cumulative_seconds": cumulative,
                         "callers": [{"file": f, "line": n, "function": fn, "stats": list(values)}
                                     for (f, n, fn), values in sorted(callers.items())]})
        detail["functions"] = sorted(rows, key=lambda row: row["cumulative_seconds"], reverse=True)
    result = {"metrics": metrics, "detail": detail, "runtime": runtime}
    with Path(result_path).open("x", encoding="utf-8") as stream:
        json.dump(result, stream, allow_nan=False)


if __name__ == "__main__":
    main(*sys.argv[1:])
