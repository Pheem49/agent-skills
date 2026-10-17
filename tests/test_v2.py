"""Common contract, Node adapter, statistical decisions, and full measured workflows."""

import copy
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "bottleneck-lens/scripts"
sys.path.insert(0, str(SCRIPTS))
from contract import read_measurement, validate_measurement, metric_contract, summary
from statistics_tools import permutation_test, holm_adjust


class StatisticsTests(unittest.TestCase):
    def test_exact_permutation_has_known_two_sided_probability(self):
        result = permutation_test([0] * 5, [1] * 5)
        self.assertTrue(result["exact"])
        self.assertEqual(result["assignments"], 252)
        self.assertAlmostEqual(result["p_value"], 2 / 252)

    def test_identical_noisy_distributions_are_inconclusive(self):
        values = [1, 2, 10, 20, 30, 50, 100]
        self.assertEqual(permutation_test(values, values[::-1])["p_value"], 1)

    def test_small_sample_is_not_reported_as_statistical_support(self):
        self.assertIsNone(permutation_test([1] * 3, [100] * 3)["p_value"])

    def test_seeded_monte_carlo_is_reproducible_and_never_returns_zero(self):
        first = permutation_test(list(range(12)), list(range(100, 112)), resamples=999)
        second = permutation_test(list(range(12)), list(range(100, 112)), resamples=999)
        self.assertEqual(first, second)
        self.assertFalse(first["exact"])
        self.assertGreater(first["p_value"], 0)

    def test_holm_correction_handles_multiple_metrics(self):
        evidence = {"a": {"p_value": .01}, "b": {"p_value": .04}, "c": {"p_value": .2},
                    "missing": {"p_value": None}}
        holm_adjust(evidence)
        self.assertAlmostEqual(evidence["a"]["adjusted_p_value"], .03)
        self.assertAlmostEqual(evidence["b"]["adjusted_p_value"], .08)
        self.assertAlmostEqual(evidence["c"]["adjusted_p_value"], .2)
        self.assertIsNone(evidence["missing"]["adjusted_p_value"])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="bottleneck-lens-v2-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.script = self.root / "workload.py"
        self.script.write_text("print(42)\n")
        self.oracle = self.root / "expected.txt"
        self.oracle.write_text("42\n")

    def cli(self, *args, code=0, env=None):
        result = subprocess.run([sys.executable, str(SCRIPTS / "measure.py"), *map(str, args)],
                                capture_output=True, text=True, timeout=40, env=env)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def collect(self, name, mode="benchmark", extra=(), oracle=False, code=0):
        path = self.root / f"{name}.json"
        self.cli("measure", "--cwd", self.root, "--script", self.script, "--workload", "known-answer-v1",
                 "--mode", mode, "--runs", 7, "--warmups", 0, "--output", path,
                 *( ["--expected-stdout", self.oracle] if oracle else []), *extra, code=code)
        return path, json.loads(path.read_text())

    def compare(self, before, after, name="comparison", extra=(), code=0):
        path = self.root / f"{name}.json"
        self.cli("compare", before, after, "--output", path, *extra, code=code)
        return json.loads(path.read_text())

    def test_contract_records_units_scopes_inputs_and_persistent_reports(self):
        fixture = self.root / "data.txt"
        fixture.write_text("fixture")
        path, data = self.collect("baseline", extra=("--input", fixture, "--seed", "42", "--dataset", "data-v1"))
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["metrics"]["wall_seconds"]["unit"], "seconds")
        self.assertFalse(data["metrics"]["wall_seconds"]["scope"]["interpreter_startup_included"])
        self.assertEqual(data["metrics"]["cpu_seconds"]["scope"]["children"], "excluded")
        self.assertEqual(data["inputs"]["seed"], "42")
        self.assertIn(str(fixture), data["inputs"]["files"])
        self.assertTrue(path.with_suffix(".md").is_file())
        self.assertTrue(all((self.root / item["path"]).is_file() for item in data["artifacts"]))

    def test_full_python_before_profile_optimize_check_and_after(self):
        self.oracle.write_text("6000\n")
        self.script.write_text("values = list(range(6000))\nprint(sum(q in values for q in range(6000)))\n")
        before, first = self.collect("before", oracle=True, extra=("--revision", "before"))
        _, profile = self.collect("profile", mode="cpu", oracle=True)
        self.assertTrue(any(row["role"].endswith(".pstats") for row in profile["artifacts"]))
        self.script.write_text("values = set(range(6000))\nprint(sum(q in values for q in range(6000)))\n")
        after = self.root / "after.json"
        self.cli("rerun", before, "--output", after, "--revision", "after")
        result = self.compare(before, after)
        self.assertEqual(first["correctness"]["status"], "passed")
        self.assertEqual(result["status"], "verified_improvement")
        self.assertEqual(result["correctness"], "passed")

    def test_faster_wrong_result_is_never_verified_and_returns_failure(self):
        self.script.write_text("import time\ntime.sleep(.03)\nprint(42)\n")
        before, _ = self.collect("before", oracle=True)
        self.script.write_text("print(0)\n")
        after, data = self.collect("wrong", oracle=True, code=3)
        self.assertEqual(data["correctness"]["status"], "failed")
        result = self.compare(before, after, code=3)
        self.assertEqual(result["status"], "correctness_failed")

    def test_tampered_stdout_evidence_blocks_comparison(self):
        before, data = self.collect("before", oracle=True)
        after, _ = self.collect("after", oracle=True)
        (self.root / data["correctness"]["observations"][0]["path"]).write_text("tampered")
        self.cli("compare", before, after, "--output", self.root / "comparison.json", code=2)

    def test_duplicate_correctness_evidence_is_rejected(self):
        _, data = self.collect("baseline", oracle=True)
        data["correctness"]["observations"][-1] = data["correctness"]["observations"][0]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_measurement(data)

    def test_mismatched_oracles_cannot_verify_an_improvement(self):
        before, _ = self.collect("before", oracle=True)
        self.script.write_text("print(43)\n")
        self.oracle.write_text("43\n")
        after, _ = self.collect("after", oracle=True)
        result = self.cli("compare", before, after, "--output", self.root / "comparison.json", code=2)
        self.assertIn("oracle differs", result.stderr)

    def test_rerun_request_cannot_redirect_to_a_different_target(self):
        before, data = self.collect("before")
        marker = self.root / "unexpected"
        alternate = self.root / "alternate.py"
        alternate.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        data["request"]["target"] = {**data["target"], "value": str(alternate)}
        before.write_text(json.dumps(data))
        self.cli("rerun", before, "--output", self.root / "after.json", code=2)
        self.assertFalse(marker.exists())

    def test_rerun_environment_change_is_rejected_before_execution(self):
        before, _ = self.collect("before")
        env = {**os.environ, "UV_THREADPOOL_SIZE": "31"}
        self.cli("rerun", before, "--output", self.root / "after.json", code=2, env=env)
        self.assertFalse((self.root / "after.artifacts").exists())

    def test_rerun_new_dependency_metadata_is_rejected_before_execution(self):
        before, _ = self.collect("before")
        (self.root / "package.json").write_text('{"name":"changed"}\n')
        self.cli("rerun", before, "--output", self.root / "after.json", code=2)
        self.assertFalse((self.root / "after.artifacts").exists())

    def test_custom_report_directory_keeps_artifact_links_resolvable(self):
        report_dir = self.root / "reports"
        report_dir.mkdir()
        report = report_dir / "baseline.md"
        _, data = self.collect("baseline", oracle=True, extra=("--report", report))
        links = re.findall(r'\]\(<([^>]+)>\)', report.read_text())
        self.assertTrue(links)
        self.assertTrue(all((report.parent / link).is_file() for link in links))
        self.assertIn(str(SCRIPTS / "measure.py"), report.read_text())
        self.assertEqual(data["rerun_command"][2], "rerun")

    def test_scope_and_unit_mismatches_are_rejected(self):
        before, data = self.collect("before")
        for field, value in (("unit", "bytes"), ("scope", {**data["metrics"]["cpu_seconds"]["scope"], "children": "included"})):
            changed = copy.deepcopy(data)
            changed["metrics"]["cpu_seconds"][field] = value
            after = self.root / "after.json"
            after.write_text(json.dumps(changed))
            result = self.cli("compare", before, after, "--output", self.root / "comparison.json", code=2)
            self.assertIn("metrics differs", result.stderr)

    def test_missing_scope_and_legacy_format_are_rejected(self):
        _, data = self.collect("baseline")
        del data["metrics"]["wall_seconds"]["scope"]["children"]
        with self.assertRaisesRegex(ValueError, "scope children"):
            validate_measurement(data)
        with self.assertRaisesRegex(ValueError, "recollect"):
            validate_measurement({"kind": "measurement", "schema_version": 1})
        with self.assertRaisesRegex(ValueError, "recollect"):
            validate_measurement({"status": "measured baseline", "samples": []})

    def test_rerun_refuses_a_changed_dataset_before_executing(self):
        fixture = self.root / "data.txt"
        fixture.write_text("original")
        before, _ = self.collect("before", extra=("--input", fixture))
        fixture.write_text("changed")
        self.cli("rerun", before, "--output", self.root / "after.json", code=2)
        self.assertFalse((self.root / "after.artifacts").exists())

    def test_overlapping_synthetic_control_is_inconclusive(self):
        # Synthetic statistics control, not application benchmark evidence.
        before, data = self.collect("before")
        first = copy.deepcopy(data)
        last = copy.deepcopy(data)
        for artifact, times in ((first, [1, 2, 10, 20, 30, 50, 100]), (last, [100, 50, 30, 20, 10, 2, 1])):
            artifact["samples"] = [{key: value if key == "wall_seconds" else 10 for key in data["metrics"]} for value in times]
        before.write_text(json.dumps(first))
        after = self.root / "after.json"
        after.write_text(json.dumps(last))
        result = self.compare(before, after)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["metrics"]["wall_seconds"]["evidence"]["p_value"], 1)

    def test_ci_regression_and_practical_threshold_are_separate(self):
        before, data = self.collect("before")
        first, last = copy.deepcopy(data), copy.deepcopy(data)
        for artifact, wall in ((first, 1), (last, 1.02)):
            artifact["samples"] = [{key: wall if key == "wall_seconds" else 10 for key in data["metrics"]} for _ in range(7)]
        before.write_text(json.dumps(first))
        after = self.root / "after.json"
        after.write_text(json.dumps(last))
        result = self.compare(before, after)
        self.assertEqual(result["status"], "below_practical_threshold")
        self.assertTrue(result["metrics"]["wall_seconds"]["evidence"]["supports_difference"])
        result = self.compare(before, after, name="strict", extra=("--min-change-percent", 1, "--fail-on-regression"), code=1)
        self.assertEqual(result["status"], "regression")

    def test_unassessed_correctness_never_receives_verified_status(self):
        # Synthetic statistical control; no application speed claim.
        before, data = self.collect("before")
        first, last = copy.deepcopy(data), copy.deepcopy(data)
        for artifact, time_value in ((first, 10), (last, 5)):
            artifact["samples"] = [{key: time_value if key.endswith("seconds") else 10
                                    for key in data["metrics"]} for _ in range(7)]
        before.write_text(json.dumps(first))
        after = self.root / "after.json"
        after.write_text(json.dumps(last))
        result = self.compare(before, after)
        self.assertEqual(result["status"], "observed_improvement")

    def test_absolute_policy_threshold_is_applied_in_metric_units(self):
        # Synthetic statistical control: detectable but practically small.
        before, data = self.collect("before")
        first, last = copy.deepcopy(data), copy.deepcopy(data)
        for artifact, time_value in ((first, 1), (last, 1.02)):
            artifact["samples"] = [{key: time_value if key == "wall_seconds" else 10
                                    for key in data["metrics"]} for _ in range(7)]
        before.write_text(json.dumps(first))
        after = self.root / "after.json"
        after.write_text(json.dumps(last))
        policy = self.root / "policy.json"
        policy.write_text(json.dumps({"thresholds": {"wall_seconds": {"relative_percent": 1, "absolute": .1}}}))
        result = self.compare(before, after, extra=("--policy", policy))
        self.assertEqual(result["status"], "below_practical_threshold")
        self.assertEqual(result["metrics"]["wall_seconds"]["practical"]["unit"], "seconds")

    def test_invalid_policy_does_not_write_a_comparison(self):
        before, _ = self.collect("before")
        after, _ = self.collect("after")
        for flag, value in (("--alpha", "nan"), ("--min-change-percent", -1), ("--min-samples", 1)):
            self.cli("compare", before, after, "--output", self.root / "comparison.json", flag, value, code=2)
            self.assertFalse((self.root / "comparison.json").exists())

    @unittest.skipUnless(shutil.which("node"), "Node.js required")
    def test_node_benchmark_waits_for_async_work_and_supports_cjs(self):
        self.script = self.root / "workload.cjs"
        self.script.write_text("const assert = require('node:assert/strict');\n"
                               "assert.deepEqual(process.argv.slice(2), ['hello']);\nsetTimeout(() => console.log(42), 35);\n")
        _, data = self.collect("node", oracle=True, extra=("--", "hello"))
        self.assertEqual(data["environment"]["runtime"]["name"], "node")
        self.assertEqual(data["correctness"]["status"], "passed")
        self.assertGreater(data["summary"]["wall_seconds"]["min"], .035)
        self.assertEqual(data["metrics"]["wall_seconds"]["scope"]["interval"], "preload_until_exit_event")

    @unittest.skipUnless(shutil.which("node"), "Node.js required")
    def test_commonjs_main_guard_executes_for_cjs_and_js_entrypoints(self):
        for extension in ("cjs", "js"):
            with self.subTest(extension=extension):
                self.script = self.root / f"guard.{extension}"
                marker = self.root / f"ran-{extension}.txt"
                self.script.write_text("const fs = require('node:fs');\n"
                                       "function main() {\n"
                                       f"  fs.appendFileSync({json.dumps(str(marker))}, 'ran\\n');\n"
                                       "  console.log(42);\n}\n"
                                       "if (require.main === module) main();\n")
                _, data = self.collect(f"guard-{extension}", oracle=True, extra=("--runs", 3))
                self.assertEqual(marker.read_text().splitlines(), ["ran"] * 3)
                self.assertEqual(data["correctness"]["status"], "passed")

    @unittest.skipUnless(shutil.which("node"), "Node.js required")
    def test_esm_main_identity_is_preserved_when_supported_by_node(self):
        self.script = self.root / "guard.mjs"
        self.script.write_text("if ('main' in import.meta && !import.meta.main) throw new Error('Not the entrypoint');\n"
                               "console.log(42);\n")
        _, data = self.collect("esm-main", oracle=True, extra=("--runs", 3))
        self.assertEqual(data["correctness"]["status"], "passed")

    def test_repeated_memory_samples_detect_allocation_regression(self):
        self.script.write_text("allocation = bytearray(1000000)\nprint(42)\n")
        before, first = self.collect("memory-before", mode="memory", oracle=True)
        self.script.write_text("allocation = bytearray(16000000)\nprint(42)\n")
        after, last = self.collect("memory-after", mode="memory", oracle=True)
        self.assertEqual(len(first["samples"]), 7)
        self.assertEqual(len(last["samples"]), 7)
        result = self.compare(before, after, extra=("--primary-metric", "python_peak_bytes", "--fail-on-regression"), code=1)
        self.assertEqual(result["status"], "regression")
        self.assertEqual(result["metrics"]["python_peak_bytes"]["assessment"], "regression")
        self.assertTrue(result["metrics"]["python_peak_bytes"]["evidence"]["supports_difference"])

    @unittest.skipUnless(shutil.which("node"), "Node.js required")
    def test_node_cpu_profile_event_loop_and_common_contract(self):
        self.script = self.root / "workload.mjs"
        self.script.write_text("import { setTimeout } from 'node:timers/promises';\n"
                               "let value = 0; for(let i=0;i<500000;i++) value += Math.sqrt(i);\n"
                               "await setTimeout(40); console.log(42);\n")
        _, data = self.collect("node-profile", mode="cpu", oracle=True)
        validate_measurement(data)
        self.assertTrue(data["details"][0]["sampled_functions"])
        self.assertIn("event_loop_utilization_ratio", data["samples"][0])
        self.assertIn("event_loop_delay_p99_seconds", data["samples"][0])
        self.assertTrue(any(item["role"].endswith(".cpuprofile") for item in data["artifacts"]))
        self.assertFalse(data["metrics"]["wall_seconds"]["comparable"])

    @unittest.skipUnless(shutil.which("node"), "Node.js required")
    def test_node_profiles_preserve_worker_isolates_and_identify_main_thread(self):
        self.script = self.root / "workload.mjs"
        self.script.write_text("import {Worker} from 'node:worker_threads';\n"
                               "const worker=new Worker('let total=0;for(let i=0;i<1000000;i++) total+=Math.sqrt(i);', {eval:true});\n"
                               "await new Promise((resolve,reject)=>{worker.once('exit',resolve);worker.once('error',reject);});\nconsole.log(42);\n")
        _, data = self.collect("workers", mode="cpu", oracle=True)
        profiles = [row for row in data["artifacts"] if row["role"].endswith(".cpuprofile")]
        self.assertGreaterEqual(len(profiles), 2)
        self.assertEqual(data["details"][0]["profile_thread_id"], 0)
        self.assertIn(".0.", Path(data["details"][0]["profile_file"]).name)

    @unittest.skipUnless(shutil.which("node"), "Node.js required")
    def test_node_esm_full_before_after_and_wrong_answer(self):
        self.script = self.root / "workload.mjs"
        self.oracle.write_text("12000\n")
        setup = "const values = Array.from({length:12000},(_,i)=>i);\n"
        self.script.write_text(setup + "let count=0; for(let q=0;q<12000;q++) count += values.includes(q); console.log(count);\n")
        before, _ = self.collect("before", oracle=True)
        self.script.write_text(setup + "const lookup=new Set(values); let count=0; for(let q=0;q<12000;q++) count += lookup.has(q); console.log(count);\n")
        after, _ = self.collect("after", oracle=True)
        result = self.compare(before, after)
        self.assertEqual(result["status"], "verified_improvement")
        self.script.write_text("console.log(0);\n")
        wrong, _ = self.collect("wrong", oracle=True, code=3)
        result = self.compare(before, wrong, name="invalid", code=3)
        self.assertEqual(result["status"], "correctness_failed")

    @unittest.skipUnless(shutil.which("node"), "Node.js required")
    def test_node_memory_mode_is_rejected_before_target_execution(self):
        self.script = self.root / "workload.mjs"
        marker = self.root / "unexpected"
        self.script.write_text(f"import {{writeFileSync}} from 'node:fs'; writeFileSync({json.dumps(str(marker))}, 'ran');\n")
        self.cli("measure", "--cwd", self.root, "--script", self.script, "--mode", "memory",
                 "--workload", "test", "--output", self.root / "result.json", code=2)
        self.assertFalse(marker.exists())

    @unittest.skipUnless(shutil.which("node"), "Node.js required")
    def test_node_failure_and_timeout_do_not_write_success_artifacts(self):
        self.script = self.root / "workload.mjs"
        for index, source in enumerate(("throw new Error('failed');\n", "setTimeout(()=>{}, 5000);\n")):
            self.script.write_text(source)
            output = self.root / f"fail-{index}.json"
            self.cli("measure", "--cwd", self.root, "--script", self.script, "--workload", "test",
                     "--timeout", .3, "--output", output, code=2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
