#!/usr/bin/env python3
"""Run a local known-answer experiment with generated workloads and preserved evidence."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

RUNNER = Path(__file__).resolve().with_name("measure.py")

PYTHON = {
    "before": "import json, sys\nn = json.load(open(sys.argv[1]))['size']\nvalues = list(range(n))\nprint(sum(q in values for q in range(n)))\n",
    "after": "import json, sys\nn = json.load(open(sys.argv[1]))['size']\nvalues = set(range(n))\nprint(sum(q in values for q in range(n)))\n",
    "wrong": "print(0)\n",
}
NODE = {
    "before": "import {readFileSync} from 'node:fs';\nconst {size:n}=JSON.parse(readFileSync(process.argv[2], 'utf8'));\nconst values=Array.from({length:n},(_,i)=>i);\nlet count=0; for(let q=0;q<n;q++) count+=values.includes(q); console.log(count);\n",
    "after": "import {readFileSync} from 'node:fs';\nconst {size:n}=JSON.parse(readFileSync(process.argv[2], 'utf8'));\nconst values=new Set(Array.from({length:n},(_,i)=>i));\nlet count=0; for(let q=0;q<n;q++) count+=values.has(q); console.log(count);\n",
    "wrong": "console.log(0);\n",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, help="New persistent directory; parent must exist")
    parser.add_argument("--runtime", choices=("python", "node"), default="node")
    parser.add_argument("--node", default="node")
    parser.add_argument("--runs", type=int, default=7)
    args = parser.parse_args()
    if args.runs < 5:
        parser.error("Use at least five recorded samples")
    output = Path(args.output_dir).absolute()
    output.mkdir()
    workspace = output / "workspace"
    workspace.mkdir()
    extension = ".py" if args.runtime == "python" else ".mjs"
    variants = PYTHON if args.runtime == "python" else NODE
    script = workspace / ("membership" + extension)
    size = 6000 if args.runtime == "python" else 12000
    dataset = workspace / "dataset.json"
    dataset.write_text(json.dumps({"size": size}) + "\n", encoding="utf-8")
    oracle = workspace / "expected.txt"
    oracle.write_text(f"{size}\n", encoding="utf-8")

    def run(*command, expected=0):
        result = subprocess.run([sys.executable, str(RUNNER), *map(str, command)])
        if result.returncode != expected:
            raise SystemExit(f"Unexpected collector status {result.returncode}; evidence remains at {output}")

    def collect(name, variant, mode="benchmark", expected=0):
        script.write_text(variants[variant], encoding="utf-8")
        (output / (name + "-source" + extension)).write_text(variants[variant], encoding="utf-8")
        path = output / f"{name}.json"
        run("measure", "--cwd", workspace, "--script", script, "--runtime", args.runtime,
            "--node", args.node, "--workload", "membership-known-answer-v1", "--dataset", f"integers-0-to-{size - 1}",
            "--input", dataset, "--expected-stdout", oracle, "--runs", args.runs, "--warmups", 1,
            "--revision", variant, "--mode", mode, "--output", path, "--", dataset, expected=expected)
        return path

    before = collect("before", "before")
    collect("profile", "before", mode="cpu")
    after = collect("after", "after")
    run("compare", before, after, "--output", output / "improvement.json")
    unchanged = collect("unchanged", "after")
    run("compare", after, unchanged, "--output", output / "control.json")
    wrong = collect("wrong", "wrong", expected=3)
    run("compare", before, wrong, "--output", output / "invalid-speedup.json", expected=3)
    results = {name: json.loads((output / f"{name}.json").read_text())["status"]
               for name in ("improvement", "control", "invalid-speedup")}
    with (output / "demo-summary.json").open("x", encoding="utf-8") as stream:
        json.dump({"label": "Generated known-answer demonstration; not a real application performance claim",
                   "runtime": args.runtime, "results": results,
                   "control_note": "The unchanged implementation is measured again; its actual outcome is preserved. "
                                   "Load/noise may prevent an inconclusive result. Deterministic noisy controls are covered in unit tests.",
                   "source_note": "Historical source copies are preserved beside reports. The workspace currently contains the intentionally wrong implementation."}, stream, indent=2)
    print(f"\nDemonstration evidence: {output}\n{json.dumps(results, indent=2)}")


if __name__ == "__main__":
    main()
