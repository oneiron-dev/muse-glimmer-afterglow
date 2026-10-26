#!/usr/bin/env python3
"""Summarize sweep score files into one table: facts learned, probe pass rates, and paired deltas vs the base.

Delta B-A (and C-A) over facts uses a paired cluster bootstrap (cluster = fact, 10,000 resamples,
seed 20261014) on the per-fact forward pass rate. The general slice delta reports wins/losses.
"""

import argparse
import json
import random
from pathlib import Path

BOOTSTRAP = 10_000
SEED = 20261014


def load(path):
    return json.loads(Path(path).read_text())


def per_fact_rate(record):
    rates = {}
    for r in record["results"]:
        if r["kind"] == "forward":
            rates.setdefault(r["fact_id"], []).append(r["pass"])
    return {k: sum(v) / len(v) for k, v in rates.items()}


def bootstrap_delta(a, b):
    keys = sorted(set(a) & set(b))
    diffs = [b[k] - a[k] for k in keys]
    rng = random.Random(SEED)
    means = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(BOOTSTRAP))
    return {"delta": round(sum(diffs) / len(diffs), 4), "ci95": [round(means[int(0.025 * BOOTSTRAP)], 4), round(means[int(0.975 * BOOTSTRAP) - 1], 4)]}


def general_paired(a, b):
    pa = {r["id"]: r["pass"] for r in a["results"] if r["kind"] == "general"}
    pb = {r["id"]: r["pass"] for r in b["results"] if r["kind"] == "general"}
    return {"wins": sum(1 for k in pa if not pa[k] and pb[k]), "losses": sum(1 for k in pa if pa[k] and not pb[k]),
            "lost_ids": sorted(k for k in pa if pa[k] and not pb[k])}


def row(label, rec, base):
    s = rec["summary"]["by_kind"]
    g = lambda k: f"{s.get(k, {}).get('pass', 0)}/{s.get(k, {}).get('n', 0)}"  # noqa: E731
    out = {"label": label, "arm": rec["arm"], "facts_learned": f"{rec['summary']['facts_learned']}/{rec['summary']['facts']}",
           "forward": g("forward"), "reverse": g("reverse"), "preference": g("preference"), "procedure": g("procedure"),
           "general": g("general"), "truncated": sum(v["truncated"] for v in s.values())}
    if base is not None and rec is not base:
        out["delta_vs_A"] = bootstrap_delta(per_fact_rate(base), per_fact_rate(rec))
        out["general_vs_A"] = general_paired(base, rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="score-base-A.json")
    ap.add_argument("--scores", nargs="+", required=True, help="label=path pairs")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    base = load(args.base)
    rows = [row("base", base, base)]
    for pair in args.scores:
        label, path = pair.split("=", 1)
        rows.append(row(label, load(path), base))
    Path(args.out).write_text(json.dumps(rows, indent=1) + "\n")
    cols = ["label", "arm", "facts_learned", "forward", "reverse", "preference", "procedure", "general", "truncated"]
    print("| " + " | ".join(cols + ["B-A per-fact pass (95% CI)", "general lost"]) + " |")
    print("|" + "---|" * (len(cols) + 2))
    for r in rows:
        d = r.get("delta_vs_A")
        dtxt = f"{d['delta']:+.3f} [{d['ci95'][0]:+.3f}, {d['ci95'][1]:+.3f}]" if d else ""
        gl = r.get("general_vs_A", {})
        print("| " + " | ".join(str(r[c]) for c in cols) + f" | {dtxt} | {gl.get('losses', '')} {gl.get('lost_ids', '')} |")


if __name__ == "__main__":
    main()
