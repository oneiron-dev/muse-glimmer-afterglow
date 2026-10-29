#!/usr/bin/env python3
"""Paired bootstrap intervals for AA-Omniscience-Public (own harness) arms graded by eval/aa_omniscience.py.

Per arm: hallucination rate = incorrect / (incorrect + partial + not attempted), omniscience index, attempt rate,
each with a 95% percentile interval; per pair (arm minus reference): the difference, resampling the same questions
for both arms. Label every number "AA-Omniscience-Public, own harness".

  eval/aa_omniscience_ci.py --dir DIR [--dir DIR2 ...] --arms base w1n3 w1idk [--pairs w1idk:w1n3 w1n3:base]
"""

import argparse
import json
import random
from pathlib import Path


def rates(grades, ids):
    c = i = p = a = 0
    for q in ids:
        g = grades[q]
        c += g == "correct"; i += g == "incorrect"; p += g == "partial"; a += g == "not_attempted"
    n = c + i + p + a
    return {"hallucination_rate": i / (i + p + a) if i + p + a else float("nan"),
            "omniscience_index": 100 * (c - i) / n, "attempt_rate": (c + i + p) / n}


def interval(xs):
    xs = sorted(xs)
    return [round(xs[int(0.025 * len(xs))], 4), round(xs[int(0.975 * len(xs)) - 1], 4)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", action="append", required=True, help="AA output dirs holding grades-<arm>.jsonl (first match wins)")
    ap.add_argument("--arms", nargs="+", required=True)
    ap.add_argument("--pairs", nargs="*", default=[], help="arm:reference")
    ap.add_argument("--resamples", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--out")
    args = ap.parse_args()
    grades = {}
    for arm in args.arms:
        path = next((Path(d) / f"grades-{arm}.jsonl" for d in args.dir if (Path(d) / f"grades-{arm}.jsonl").exists()), None)
        if path is None:
            raise SystemExit(f"no grades for {arm}")
        grades[arm] = {r["question_id"]: r["grade"] for r in (json.loads(line) for line in path.read_text().splitlines() if line.strip())}
    ids = sorted(set.intersection(*(set(g) for g in grades.values())))
    rng = random.Random(args.seed)
    boots = [[rng.choice(ids) for _ in ids] for _ in range(args.resamples)]
    res = {"label": "AA-Omniscience-Public, own harness", "n_questions": len(ids), "resamples": args.resamples, "arms": {}, "pairs": {}}
    for arm in args.arms:
        point = rates(grades[arm], ids)
        bs = [rates(grades[arm], b) for b in boots]
        res["arms"][arm] = {k: {"value": round(v, 4), "ci95": interval([x[k] for x in bs])} for k, v in point.items()}
    for spec in args.pairs:
        arm, ref = spec.split(":")
        point = {k: rates(grades[arm], ids)[k] - rates(grades[ref], ids)[k] for k in ("hallucination_rate", "omniscience_index", "attempt_rate")}
        bs = []
        for b in boots:
            ra, rr = rates(grades[arm], b), rates(grades[ref], b)
            bs.append({k: ra[k] - rr[k] for k in point})
        res["pairs"][f"{arm} - {ref}"] = {k: {"value": round(v, 4), "ci95": interval([x[k] for x in bs])} for k, v in point.items()}
    text = json.dumps(res, indent=1) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
