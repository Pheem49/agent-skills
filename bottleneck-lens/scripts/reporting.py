"""Plain Markdown reports with source artifacts and explicit measurement boundaries."""

import json
import copy
import os
from pathlib import Path
import shlex


def _text(value):
    # Keep target-controlled strings from becoming Markdown structure.
    return str(value).replace("\n", " ").replace("\r", " ").replace("`", "'")


def measurement_report(data):
    checked = data["correctness"]["status"]
    lines = ["# Bottleneck Lens measurement", "", f"Status: **measured baseline**; correctness: **{checked}**.", "",
             "## Findings, evidence, and next step", "",
             "This artifact records a workload measurement; it does not establish a bottleneck or an improvement by itself.",
             f"Evidence: {len(data['samples'])} recorded sample(s), raw metrics, runtime identity, and linked run artifacts.",
             "Next: inspect diagnostic profiles, select a targeted change, then repeat this workload and compare equivalent measurements.", "",
             "## Workload", "", f"- Identity: {_text(data['workload'])}",
             f"- Revision: {_text(data['revision'])}", f"- Runtime: {_text(data['environment']['runtime'])}",
             f"- Working directory: `{_text(data['cwd'])}`", f"- Target: `{_text(data['target'])}`",
             f"- Inputs/cache: `{_text(data['inputs'])}`", f"- Protocol: `{_text(data['protocol'])}`", "",
             "## Measurements", ""]
    for key, row in data["summary"].items():
        descriptor = data["metrics"][key]
        lines += [f"- **{key}** ({descriptor['unit']}): median {row['median']:.6g}; mean {row['mean']:.6g}; "
                  f"range {row['min']:.6g}–{row['max']:.6g}; n={row['count']}.",
                  f"  Scope: `{_text(descriptor['scope'])}`"]
    lines += ["", "## Diagnostic evidence", ""]
    for details in data["details"]:
        for row in details.get("functions", [])[:10]:
            lines.append(f"- `{_text(row['file'])}:{row['line']} {_text(row['function'])}`: "
                         f"{row['calls']} calls, self {row['self_seconds']:.6g}s; cumulative {row['cumulative_seconds']:.6g}s (overlaps callees).")
        for row in details.get("sampled_functions", [])[:10]:
            lines.append(f"- `{_text(row['file'])}:{row['line']} {_text(row['function'])}`: "
                         f"{row['sample_hits']} sample hits, sampled self {row['sampled_self_seconds']:.6g}s; hits are not call counts.")
        for row in details.get("live_allocations", [])[:10]:
            lines.append(f"- Live Python allocations at `{_text(row['file'])}:{row['line']}`: {row['bytes']} bytes, {row['blocks']} blocks.")
    if data["mode"] == "benchmark":
        lines.append("No diagnostic instrumentation was enabled by the collector in these samples.")
    lines += ["", "## Artifacts", ""]
    for item in data["artifacts"]:
        lines.append(f"- [{_text(item['role'])}](<{item['path']}>) — SHA256 `{item['sha256']}`")
    lines += ["", "## Repeat", "", "Use the same interpreter and environment, after reviewing the workload:", "", "```bash",
              shlex.join(data["rerun_command"]),
              "```", "", "The JSON request records exact target arguments and input identities. Rerun executes the current target code and rechecks recorded input hashes; it does not restore a historical revision.",
              "", "## Limitations", "",
              "Timings exclude interpreter startup and shutdown hooks. Collector-preloaded modules affect import costs. "
              "RSS is a process-lifetime high-water mark, not summed process-tree RAM. CPU excludes child processes. "
              "Python runpy ends on return; Node starts at the collector preload and waits until its event loop drains. These scopes are not interchangeable.",
              "Diagnostic instrumentation adds overhead. Native child stacks, I/O causality, request percentiles, "
              "unmeasured paths, uncontrolled cache/thermal/load drift, and equivalence of unspecified inputs remain unverified.",
              "Correctness covers only the specified output oracle. A passing oracle is not proof of all application behavior."]
    return "\n".join(lines) + "\n"


def comparison_report(data):
    lines = ["# Bottleneck Lens comparison", "", f"Status: **{data['status']}**; correctness: **{data['correctness']}**.", "",
             "## Findings, evidence, and next step", "", data["conclusion"],
             "Evidence: raw before/after samples, per-metric practical thresholds, and independent-sample permutation tests with Holm correction.",
             data["next_step"], "", "## Sources", "",
             f"- Before: `{_text(data['before_artifact'])}` ({_text(data['before_revision'])})",
             f"- After: `{_text(data['after_artifact'])}` ({_text(data['after_revision'])})", "",
             "## Observed changes and evidence", ""]
    for key, row in data["metrics"].items():
        pct = row["change_percent"]
        change = "undefined (zero baseline)" if pct is None else f"{pct:+.3f}%"
        mean_pct = row["mean_change_percent"]
        mean_change = "undefined" if mean_pct is None else f"{mean_pct:+.3f}%"
        lines += [f"### {key} ({row['unit']})", "",
                  f"- Median: {row['before']['median']:.6g} → {row['after']['median']:.6g} ({change}).",
                  f"- Mean change: {mean_change}; assessment: **{row['assessment']}**.",
                  f"- Before range: {row['before']['min']:.6g}–{row['before']['max']:.6g}; n={row['before']['count']}.",
                  f"- After range: {row['after']['min']:.6g}–{row['after']['max']:.6g}; n={row['after']['count']}.",
                  f"- Practical criterion (mean): `{_text(row['practical'])}`",
                  f"- Statistical evidence: `{_text(row['evidence'])}`",
                  f"- Scope: `{_text(row['scope'])}`", ""]
    lines += ["## Interpretation limits", "", data["note"],
              "Statistical support does not establish causality or eliminate system drift. "
              "An inconclusive result does not establish equivalence. Correctness and regression coverage apply only to this workload and its specified output oracle.",
              "", "## Recorded policy", "", "```json", json.dumps(data["policy"], indent=2), "```", ""]
    return "\n".join(lines)


def write_report(path, data, artifact_base=None):
    rendered = copy.deepcopy(data)
    if artifact_base and data["kind"] == "measurement":
        for item in rendered["artifacts"]:
            item["path"] = os.path.relpath(Path(artifact_base) / item["path"], Path(path).parent)
    text = measurement_report(rendered) if data["kind"] == "measurement" else comparison_report(rendered)
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(text)
