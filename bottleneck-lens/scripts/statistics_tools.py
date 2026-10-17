"""Independent-sample permutation evidence; practical impact is a separate decision."""

from itertools import combinations
import math
import random
import statistics


def permutation_test(before, after, resamples=9999, seed=0, min_samples=5):
    if min(len(before), len(after)) < min_samples:
        return {"method": "independent_permutation_difference_in_means", "p_value": None,
                "reason": "insufficient_samples", "min_samples": min_samples}
    values = [*before, *after]
    n, m = len(before), len(after)
    total = math.fsum(values)
    observed = statistics.mean(after) - statistics.mean(before)
    possibilities = math.comb(n + m, n)
    exact = possibilities <= resamples
    rng = random.Random(seed)
    assignments = combinations(range(n + m), n) if exact else (
        rng.sample(range(n + m), n) for _ in range(resamples))
    tolerance = 1e-12 * max(abs(observed), max(values), 1e-300)
    extreme = count = 0
    for indices in assignments:
        left = math.fsum(values[i] for i in indices)
        difference = (total - left) / m - left / n
        extreme += abs(difference) >= abs(observed) - tolerance
        count += 1
    p_value = extreme / count if exact else (extreme + 1) / (count + 1)
    return {"method": "independent_permutation_difference_in_means", "p_value": p_value,
            "exact": exact, "assignments": count, "seed": None if exact else seed,
            "alternative": "two_sided_absolute_difference", "reason": "computed",
            "assumption": "Independent exchangeable observations under the null; uncontrolled drift can invalidate inference."}


def holm_adjust(evidence):
    ordered = sorted((row["p_value"], key) for key, row in evidence.items() if row["p_value"] is not None)
    previous = 0
    for index, (p_value, key) in enumerate(ordered):
        previous = min(1, max(previous, p_value * (len(ordered) - index)))
        evidence[key]["adjusted_p_value"] = previous
    for row in evidence.values():
        row.setdefault("adjusted_p_value", None)


def compare_metrics(before, after, policy):
    result, tests = {}, {}
    for key, descriptor in before["metrics"].items():
        if not descriptor["comparable"] or descriptor["direction"] == "neutral":
            continue
        first, last = before["summary"][key], after["summary"][key]
        b, a = first["median"], last["median"]
        mean_delta = last["mean"] - first["mean"]
        mean_percent = mean_delta / first["mean"] * 100 if first["mean"] else None
        threshold = policy["thresholds"].get(key, {})
        relative = threshold.get("relative_percent", policy["min_change_percent"])
        absolute = threshold.get("absolute", 0)
        material = mean_percent is not None and abs(mean_percent) >= relative and abs(mean_delta) >= absolute
        tests[key] = permutation_test([s[key] for s in before["samples"]], [s[key] for s in after["samples"]],
                                      policy["permutations"], policy["seed"], policy["min_samples"])
        result[key] = {
            "unit": descriptor["unit"], "scope": descriptor["scope"],
            "before": first, "after": last,
            "change_percent": (a - b) / b * 100 if b else None,
            "mean_change_percent": mean_percent, "mean_absolute_change": mean_delta,
            "direction": "lower" if a < b else "higher" if a > b else "unchanged",
            "variability": "insufficient_samples" if min(first["count"], last["count"]) < policy["min_samples"] else (
                "ranges_overlap" if max(first["min"], last["min"]) <= min(first["max"], last["max"]) else "ranges_separated"),
            "practical": {"meaningful": material, "relative_threshold_percent": relative,
                          "absolute_threshold": absolute, "unit": descriptor["unit"], "statistic": "mean"},
        }
    holm_adjust(tests)
    for key, row in result.items():
        evidence = tests[key]
        supported = evidence["adjusted_p_value"] is not None and evidence["adjusted_p_value"] <= policy["alpha"]
        evidence.update(alpha=policy["alpha"], correction="Holm across comparable metrics", supports_difference=supported)
        row["evidence"] = evidence
        favorable = row["mean_absolute_change"] * (1 if before["metrics"][key]["direction"] == "lower" else -1) < 0
        if not supported:
            row["assessment"] = "inconclusive"
        elif not row["practical"]["meaningful"]:
            row["assessment"] = "below_practical_threshold"
        else:
            row["assessment"] = "improvement" if favorable else "regression"
    return result
