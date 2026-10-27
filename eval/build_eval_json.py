#!/usr/bin/env python3
"""Turn the four arm score files of one night into runs/<w>/<d>/night/eval.json (the morning report contract).

learned: per-fact forward pass-rate deltas (B-A, C-A, B-C, D-C), paired cluster bootstrap over facts.
stale: rate at which a superseded value comes back, per arm.
forgot: arm A vs arm B on the 200-item forgetting slice (180 MMLU-Pro + 20 tool), Newcombe interval and
exact McNemar; the update passes when the interval's lower bound is above -5 points and no tool item that
passed before fails after. facts[]: per fact, passes of 3, the raw answers, the per-probe pass flags and
(llama.cpp runs) the per-probe token confidence in every arm. The confidence field names are stable:
conf.<arm>[i] = {first_logprob, min_logprob, mean_logprob, n_tokens, first_top}.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omniscience  # noqa: E402
import stats  # noqa: E402
from summarize_sweep import passes, per_fact_rate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FORGET_KINDS = ("forget_mmlu", "forget_tool")
FORGET_LOWER_BOUND = -0.05


def build(world, day, recs, adapter, gate=None):
    arms = {}
    for arm, rec in recs.items():
        arms[arm] = {**rec["summary"], "truncated": sum(1 for r in rec["results"] if r["truncated"]),
                     "lora_scale": rec.get("lora_scale"), "notes": rec.get("notes")}
    rate = {arm: per_fact_rate(rec) for arm, rec in recs.items()}
    learned = {"facts_learned": {arm: recs[arm]["summary"]["facts_learned"] for arm in recs}}
    for name, (x, y) in {"B_minus_A": ("A", "B"), "C_minus_A": ("A", "C"), "B_minus_C": ("C", "B"), "D_minus_C": ("C", "D")}.items():
        if x in rate and y in rate:
            b = stats.paired_cluster_bootstrap(rate[x], rate[y])
            learned[name] = {"est": b["delta"], "ci95": b["ci95"], "clusters": b["clusters"]}
    stale = {}
    for arm, rec in recs.items():
        st = [r for r in rec["results"] if r["kind"] == "stale"]
        stale[arm] = {"stale": sum(r["pass"] for r in st), "n": len(st)}
    forgot = None
    if "A" in recs and "B" in recs:
        before, after = passes(recs["A"], FORGET_KINDS), passes(recs["B"], FORGET_KINDS)
        if before:
            pp = stats.paired_proportions(before, after)
            tb, ta = passes(recs["A"], ("forget_tool",)), passes(recs["B"], ("forget_tool",))
            critical = sorted(k for k in tb if tb[k] and not ta.get(k, False))
            forgot = {"before": {"pass": sum(before.values()), "n": len(before)},
                      "after": {"pass": sum(after.values()), "n": len(after)},
                      "delta": pp["after_minus_before"], "ci95": pp["newcombe95"], "mcnemar_p": pp["mcnemar_p"],
                      "lost": pp["lost"], "gained": pp["gained"], "critical_tool_failures": critical,
                      "rule": "keep if ci95 lower bound > -0.05 and no new critical tool failure",
                      "passed": pp["newcombe95"][0] > FORGET_LOWER_BOUND and not critical}
    facts = []
    for f in world["facts"]:
        row = {"id": f["id"], "key": f["key"], "value": f["value"], "valid_from": f.get("valid_from", 1), "answers": {}, "pass": {}}
        for arm, rec in recs.items():
            rs = [r for r in rec["results"] if r["kind"] == "forward" and r["fact_id"] == f["id"]]
            if not rs:
                continue
            row[arm] = sum(r["pass"] for r in rs)
            row["answers"][arm] = [r["answer"] for r in rs]
            row["pass"][arm] = [bool(r["pass"]) for r in rs]
            if any(r.get("conf") for r in rs):
                row.setdefault("conf", {})[arm] = [r.get("conf") for r in rs]
            row["probes"] = [r["id"] for r in rs]
        if row["answers"]:
            facts.append(row)
    any_rec = next(iter(recs.values()))
    return {"world": world["world"], "day": day, "eval_day": day, "adapter": adapter,
            "runtime": any_rec.get("runtime", "transformers+peft"),
            "decoding": {"greedy": True, "target_only": True, "answer_channel": "forced to=user",
                         "max_new_tokens": {"forward": 64, "reverse": 64, "general": 64, "preference": 256, "procedure": 256}},
            "arms": arms, "learned": learned, "stale": stale, "forgot": forgot,
            "omniscience": omniscience.per_arm(recs),
            "abstention_gate": {k: v for k, v in gate.items() if k != "answers"} if gate else None, "facts": facts}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--scores", nargs="+", required=True, help="ARM=path, ARM in A B C D")
    ap.add_argument("--adapter-path", required=True)
    ap.add_argument("--adapter-sha256", required=True)
    ap.add_argument("--gate", help="gate-<label>.json from llama_eval --gate-slice (SimpleQA abstention gate)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    world = json.loads((ROOT / "worlds" / args.world / "world.json").read_text())
    recs = {}
    for pair in args.scores:
        arm, path = pair.split("=", 1)
        recs[arm] = json.loads(Path(path).read_text())
    gate = json.loads(Path(args.gate).read_text()) if args.gate else None
    doc = build(world, args.day, recs, {"path": args.adapter_path, "sha256": args.adapter_sha256}, gate)
    Path(args.out).write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({"learned": doc["learned"], "forgot": {k: doc["forgot"][k] for k in ("delta", "ci95", "passed")} if doc["forgot"] else None}))


if __name__ == "__main__":
    main()
