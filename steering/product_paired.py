#!/usr/bin/env python3
"""Paired differences for the product-form abstention test (answers from product_answers.py), scored with the night's
rules (eval/product_abstention.abstained):

  --answers W=FILE   adapter minus base on the same questions under the same notes (an answers-<arm>.jsonl file),
                     per set, with a 95% paired bootstrap interval; general also on its first 100 questions (the
                     night-2 direction screen) when more were asked.
  --swing L=A:B      the base against itself: base rows of two raw.jsonl files on their shared questions (a rerun with
                     the same notes, or the same questions under another day's notes): counts, flips, byte-equal
                     answers and the same interval.

Differences are in percentage points of the questions in the set (never-told: of 30). Bootstrap: 10,000 resamples of
the question ids, seed 20261014.

  product_paired.py --answers w4-grad-student=.../answers-night2b.jsonl --swing w2-rerun=raw-run1.jsonl:raw.jsonl
"""

import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "eval"))
import simpleqa_gate  # noqa: E402
from product_abstention import (abstained, refuse_mixed_decide, refuse_mixed_server, refuse_mixed_tools,  # noqa: E402
                                same_server)

SEED = 20261014
RESAMPLES = 10000


def paired(pairs):
    """pairs: [(x, y)] of 0/1 per question; the difference is mean(y - x) in points with its 95% bootstrap interval."""
    n = len(pairs)
    d = [y - x for x, y in pairs]
    rng = random.Random(SEED)
    boot = sorted(sum(d[rng.randrange(n)] for _ in range(n)) / n for _ in range(RESAMPLES))
    return {"n": n, "x": sum(x for x, _ in pairs), "y": sum(y for _, y in pairs),
            "diff_points": round(100 * sum(d) / n, 1),
            "ci95_points": [round(100 * boot[int(0.025 * RESAMPLES)], 1), round(100 * boot[int(0.975 * RESAMPLES) - 1], 1)]}


def load(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def answers_report(path, first):
    rows = load(path)
    full = {}
    for r in rows:
        full.setdefault((r["set"], r["id"]), {})[r["arm"]] = r
    pairs = [(v["base"], v["adapter"]) for v in full.values() if {"base", "adapter"} <= set(v)]
    refuse_mixed_tools(pairs, path)
    refuse_mixed_server(pairs, path)
    refuse_mixed_decide(pairs, path)
    by = {k: {arm: int(abstained(r)) for arm, r in v.items()} for k, v in full.items()}
    out = {}
    order = [q["id"] for q in simpleqa_gate.gate_items()]
    for s in ("never_told", "never_told_rewrites", "general"):
        keys = [k for k in by if k[0] == s and {"base", "adapter"} <= set(by[k])]
        if s == "general":
            keys.sort(key=lambda k: order.index(k[1]))
        if keys:
            out[s] = paired([(by[k]["base"], by[k]["adapter"]) for k in keys])
        if s == "general" and len(keys) > first:
            out[f"general_first{first}"] = paired([(by[k]["base"], by[k]["adapter"]) for k in keys[:first]])
    return out


def swing_report(path_a, path_b):
    def base(p):
        return {(r["set"], r["id"]): r for r in load(p) if r["arm_name"] == "base"}
    a, b = base(path_a), base(path_b)
    refuse_mixed_tools([(a[k], b[k]) for k in a.keys() & b.keys()], f"{path_a} vs {path_b}")
    out = {}
    for s in ("never_told", "never_told_rewrites", "general"):
        keys = sorted(k for k in a.keys() & b.keys() if k[0] == s)
        if not keys:
            continue
        pairs = [(int(abstained(dict(a[k], arm="base"))), int(abstained(dict(b[k], arm="base")))) for k in keys]
        out[s] = dict(paired(pairs), flips=sum(x != y for x, y in pairs),
                      byte_equal=sum(a[k]["answer"] == b[k]["answer"] for k in keys),
                      same_server_run=sum(same_server(a[k], b[k]) for k in keys))  # a swing may cross runs
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", action="append", default=[], help="LABEL=answers-<arm>.jsonl")
    ap.add_argument("--swing", action="append", default=[], help="LABEL=RAW_A:RAW_B (base rows)")
    ap.add_argument("--first", type=int, default=100)
    ap.add_argument("--out")
    args = ap.parse_args()
    rec = {"rule": "abstention by the night's rules; difference = second minus first (adapter minus base), points",
           "bootstrap": {"resamples": RESAMPLES, "seed": SEED}, "paired": {}, "swing": {}}
    for item in args.answers:
        label, path = item.split("=", 1)
        rec["paired"][label] = dict(answers_report(path, args.first), file=path)
    for item in args.swing:
        label, paths = item.split("=", 1)
        pa, pb = paths.split(":")
        rec["swing"][label] = dict(swing_report(pa, pb), files=[pa, pb])
    if args.out:
        Path(args.out).write_text(json.dumps(rec, indent=1) + "\n")
    print("| label | set | n | first / base | second / adapter | difference (points) | 95% interval | flips | byte-equal |")
    print("|---|---|---|---|---|---|---|---|---|")
    for kind in ("paired", "swing"):
        for label, sets in rec[kind].items():
            for s, v in sets.items():
                if s == "files" or s == "file":
                    continue
                lo, hi = v["ci95_points"]
                print(f"| {label} | {s} | {v['n']} | {v['x']} | {v['y']} | {v['diff_points']:+.1f} | {lo:+.1f} to {hi:+.1f} "
                      f"| {v.get('flips', '')} | {v.get('byte_equal', '')} |")


if __name__ == "__main__":
    main()
