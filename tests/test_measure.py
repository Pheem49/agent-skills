"""Behavioral tests using only generated, local synthetic workloads."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


RUNNER = Path(__file__).resolve().parents[1] / "bottleneck-lens/scripts/measure.py"
spec = importlib.util.spec_from_file_location("bottleneck_lens_measure", RUNNER)
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="bottleneck-lens-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.script = self.root / "workload.py"
        self.script.write_text("assert sum(range(1000)) == 499500\n", encoding="utf-8")

    def cli(self, *arguments, ok=True, env=None):
        result = subprocess.run([sys.executable, str(RUNNER), *map(str, arguments)],
                                text=True, capture_output=True, env=env, timeout=15)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def collect(self, name="result.json", mode="benchmark", extra=(), ok=True, env=None):
        output = self.root / name
        self.cli("measure", "--cwd", self.root, "--script", self.script,
                 "--workload", "synthetic-fixture-v1", "--mode", mode,
                 "--runs", 3, "--warmups", 0, "--output", output, *extra, ok=ok, env=env)
        return json.loads(output.read_text()) if ok else output

    def test_benchmark_records_raw_samples_and_separate_cpu_wall_time(self):
        self.script.write_text("import time\ntime.sleep(0.04)\nassert 2 + 2 == 4\n")
        data = self.collect()
        self.assertEqual(len(data["samples"]), 3)
        self.assertTrue(all(row["wall_seconds"] > row["cpu_seconds"] for row in data["samples"]))
        self.assertGreaterEqual(data["summary"]["wall_seconds"]["min"], 0.04)
        self.assertEqual(data["correctness"]["status"], "not_assessed")
        self.assertEqual(data["script_sha256"], measure.digest(self.script))

    def test_warmups_and_samples_each_execute_the_workload(self):
        marker = self.root / "count.txt"
        self.script.write_text(f"from pathlib import Path\np = Path({str(marker)!r})\n"
                               "with p.open('a') as f: f.write('run\\n')\n")
        data = self.collect(extra=("--warmups", 2))
        self.assertEqual(len(marker.read_text().splitlines()), 5)
        self.assertEqual(len(data["samples"]), 3)

    def test_cpu_evidence_includes_function_calls_and_callers(self):
        self.script.write_text("def hot():\n    return sum(range(1000))\n"
                               "assert [hot() for _ in range(4)] == [499500] * 4\n")
        data = self.collect(mode="cpu")
        row = next(row for row in data["details"][0]["functions"] if row["function"] == "hot")
        self.assertEqual(row["calls"], 4)
        self.assertTrue(row["callers"])
        self.assertGreater(row["cumulative_seconds"], 0)
        self.assertEqual(data["protocol"]["runs"], 1)

    def test_memory_peak_includes_freed_transient_allocation(self):
        self.script.write_text("value = bytearray(2000000)\nassert len(value) == 2000000\ndel value\n")
        data = self.collect(mode="memory")
        sample = data["samples"][0]
        self.assertGreaterEqual(sample["python_peak_bytes"], 2000000)
        self.assertGreater(sample["python_peak_bytes"], sample["python_current_bytes"])
        self.assertTrue(data["details"][0]["live_allocations"])

    def test_script_imports_arguments_and_stdout_do_not_corrupt_metrics(self):
        (self.root / "helper.py").write_text("VALUE = 42\n")
        self.script.write_text("import sys\nfrom helper import VALUE\n"
                               "assert VALUE == 42\nassert sys.argv[1:] == ['--size', '3']\n"
                               "print('not json and not report content')\n")
        data = self.collect(extra=("--", "--size", "3"))
        self.assertEqual(data["target"]["args"], ["--size", "3"])
        self.assertNotIn("not json", json.dumps(data))

    def test_module_entrypoint_and_successful_system_exit(self):
        package = self.root / "example"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "__main__.py").write_text("import sys\nassert sys.argv[1:] == ['hello']\nsys.exit(0)\n")
        output = self.root / "module.json"
        self.cli("measure", "--cwd", self.root, "--module", "example", "--workload", "module-fixture",
                 "--runs", 3, "--warmups", 0, "--output", output, "--", "hello")
        self.assertEqual(json.loads(output.read_text())["target"]["kind"], "module")

    def test_failures_and_missing_metrics_do_not_create_a_success_artifact(self):
        for index, source in enumerate(("raise RuntimeError('failure')\n", "raise SystemExit(3)\n",
                                       "assert False\n", "import os\nos._exit(0)\n")):
            with self.subTest(source=source):
                self.script.write_text(source)
                output = self.collect(name=f"failed-{index}.json", ok=False)
                self.assertFalse(output.exists())

    def test_timeout_aborts_without_success_artifact(self):
        self.script.write_text("import time\ntime.sleep(5)\n")
        output = self.collect(extra=("--timeout", "0.15"), ok=False)
        self.assertFalse(output.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup")
    def test_timeout_also_terminates_descendants_in_the_group(self):
        marker = self.root / "child-finished"
        child_code = f"import time; from pathlib import Path; time.sleep(0.8); Path({str(marker)!r}).touch()"
        self.script.write_text(f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', {child_code!r}])\ntime.sleep(5)\n")
        self.collect(extra=("--timeout", "0.3"), ok=False)
        time.sleep(0.9)
        self.assertFalse(marker.exists())

    def test_existing_report_prevents_workload_execution(self):
        output = self.root / "result.json"
        output.write_text("baseline")
        marker = self.root / "should-not-run"
        self.script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        self.collect(ok=False)
        self.assertEqual(output.read_text(), "baseline")
        self.assertFalse(marker.exists())

    def test_external_tracemalloc_is_not_accepted_as_uninstrumented(self):
        env = {**os.environ, "PYTHONTRACEMALLOC": "1"}
        self.assertFalse(self.collect(ok=False, env=env).exists())

    def test_invalid_protocol_is_rejected_before_execution(self):
        for extra in (("--runs", 0), ("--warmups", -1), ("--timeout", "nan"), ("--timeout", 0),
                      ("--script", "")):
            with self.subTest(extra=extra):
                self.assertFalse(self.collect(extra=extra, ok=False).exists())

    def test_real_before_after_comparison_uses_same_target(self):
        self.script.write_text("import time\ntime.sleep(0.05)\nassert 1 + 1 == 2\n")
        self.collect("before.json")
        self.script.write_text("import time\ntime.sleep(0.002)\nassert 1 + 1 == 2\n")
        self.collect("after.json")
        output = self.root / "comparison.json"
        self.cli("compare", self.root / "before.json", self.root / "after.json", "--output", output)
        data = json.loads(output.read_text())
        self.assertLess(data["metrics"]["wall_seconds"]["change_percent"], 0)
        self.assertEqual(data["correctness"], "not_assessed")
        self.assertEqual(data["status"], "inconclusive")

    def test_compare_rejects_different_workloads_environments_modes_and_protocols(self):
        before = self.collect("before.json")
        for field, value in (("workload", "different"), ("environment", {}),
                             ("mode", "cpu"), ("protocol", {**before["protocol"], "timeout_seconds": 100})):
            with self.subTest(field=field):
                after = {**before, field: value}
                path = self.root / "after.json"
                path.write_text(json.dumps(after))
                output = self.root / "comparison.json"
                self.cli("compare", self.root / "before.json", path, "--output", output, ok=False)
                self.assertFalse(output.exists())

    def test_comparison_recomputes_summary_handles_zero_and_exposes_overlap(self):
        before = self.collect("original.json")
        before["samples"] = [{"wall_seconds": v, "cpu_seconds": 0} for v in (1, 2, 3)]
        before["metrics"] = {key: before["metrics"][key] for key in before["samples"][0]}
        after = copy.deepcopy(before)
        after["samples"] = [{"wall_seconds": v, "cpu_seconds": 0} for v in (2, 3, 4)]
        for name, value in (("before.json", before), ("after.json", after)):
            (self.root / name).write_text(json.dumps(value))
        output = self.root / "comparison.json"
        self.cli("compare", self.root / "before.json", self.root / "after.json", "--output", output,
                 "--min-samples", 3)
        metrics = json.loads(output.read_text())["metrics"]
        self.assertEqual(metrics["wall_seconds"]["change_percent"], 50)
        self.assertEqual(metrics["wall_seconds"]["variability"], "ranges_overlap")
        self.assertIsNone(metrics["cpu_seconds"]["change_percent"])

    def test_profile_memory_comparison_only_compares_allocation_metrics(self):
        self.collect("before.json", mode="memory")
        self.collect("after.json", mode="memory")
        output = self.root / "comparison.json"
        self.cli("compare", self.root / "before.json", self.root / "after.json", "--output", output,
                 "--primary-metric", "python_peak_bytes")
        metrics = json.loads(output.read_text())["metrics"]
        self.assertEqual(set(metrics), {"python_peak_bytes", "python_current_bytes"})
        self.assertTrue(all(row["variability"] == "insufficient_samples" for row in metrics.values()))

    def test_malformed_measurement_is_rejected(self):
        original = self.collect("before.json")
        for values in ([], [1, 2, 3], [{"wall_seconds": -1, "cpu_seconds": 0}] * 3,
                       [{"wall_seconds": float("nan"), "cpu_seconds": 0}] * 3,
                       [{"wall_seconds": True, "cpu_seconds": 0}] * 3):
            (self.root / "bad.json").write_text(json.dumps({**original, "samples": values}))
            output = self.root / "comparison.json"
            self.cli("compare", self.root / "before.json", self.root / "bad.json", "--output", output, ok=False)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
