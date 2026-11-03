#!/usr/bin/env python3
"""Summarise the live eval (run_live.sh): per world and arm, mean acceptance length, decode tok/s, speedup over the
target alone, and losslessness (generated ids equal the reference drafter arm's, round by round).

Acceptance length = 1 + accepted / verify steps, from llama-server's /metrics deltas; checked against the per-request
identity tokens = 1 + steps + accepted. Decode tok/s = generated tokens / decode seconds, summed over rounds.

usage: analyze_live.py OUTDIR [--ref stock-ours] [--baseline none]
"""
import argparse
import glob
import json
import os
from collections import defaultdict

ap = argparse.ArgumentParser()
ap.add_argument("outdir")
ap.add_argument("--ref", default="stock-ours", help="the arm every drafter arm's outputs are compared with")
ap.add_argument("--baseline", default="none", help="the target-alone arm for the speedup")
a = ap.parse_args()

ACC, DRAFTS, DRAFTED = ("spec_decode_num_accepted_tokens_total", "spec_decode_num_drafts_total",
                        "spec_decode_num_draft_tokens_total")
runs = defaultdict(dict)
for f in sorted(glob.glob(os.path.join(a.outdir, "*.jsonl"))):
    rows = [json.loads(line) for line in open(f)]
    if rows:
        runs[rows[0]["world"]][rows[0]["arm"]] = rows


def stats(rows):
    tok = sum(r["timings"]["predicted_n"] for r in rows)
    ms = sum(r["timings"]["predicted_ms"] for r in rows)
    acc = sum(r["spec"].get(ACC, 0) for r in rows)
    drafts = sum(r["spec"].get(DRAFTS, 0) for r in rows)
    drafted = sum(r["spec"].get(DRAFTED, 0) for r in rows)
    t_acc = sum(r["timings"].get("draft_n_accepted", 0) or 0 for r in rows)
    return {"rounds": len(rows), "tokens": tok, "decode_tok_s": round(tok / (ms / 1000), 2) if ms else None,
            "acc_len": round(1 + acc / drafts, 3) if drafts else None, "accepted": acc, "verify_steps": drafts,
            "drafted": drafted, "draft_accept_rate": round(acc / drafted, 4) if drafted else None,
            "timings_accepted": t_acc}


out = {"ref": a.ref, "baseline": a.baseline, "worlds": {}}
pooled = defaultdict(list)
for world, arms in sorted(runs.items()):
    base = stats(arms[a.baseline]) if a.baseline in arms else None
    ref = {(r["turn"], r["round"]): r["gen_ids"] for r in arms.get(a.ref, [])}
    tgt = {(r["turn"], r["round"]): r["gen_ids"] for r in arms.get(a.baseline, [])}
    res = {}
    for arm, rows in sorted(arms.items()):
        s = stats(rows)
        if base and s["decode_tok_s"]:
            s["speedup"] = round(s["decode_tok_s"] / base["decode_tok_s"], 3)
        if ref:
            same = [ref.get((r["turn"], r["round"])) == r["gen_ids"] for r in rows]
            s["same_as_ref"] = f"{sum(same)}/{len(same)}"
            diff = []
            for r in rows:
                g = ref.get((r["turn"], r["round"]))
                if g is not None and g != r["gen_ids"]:
                    k = next((i for i, (x, y) in enumerate(zip(g, r["gen_ids"])) if x != y), min(len(g), len(r["gen_ids"])))
                    diff.append({"turn": r["turn"], "round": r["round"], "first_split": k})
            s["splits_vs_ref"] = diff
        if tgt:
            s["same_as_target_alone"] = f"{sum(tgt.get((r['turn'], r['round'])) == r['gen_ids'] for r in rows)}/{len(rows)}"
        res[arm] = s
        pooled[arm] += rows
    out["worlds"][world] = res
allbase = stats(pooled[a.baseline]) if a.baseline in pooled else None
out["all"] = {}
for arm, rows in sorted(pooled.items()):
    s = stats(rows)
    if allbase and s["decode_tok_s"]:
        s["speedup"] = round(s["decode_tok_s"] / allbase["decode_tok_s"], 3)
    out["all"][arm] = s
json.dump(out, open(os.path.join(a.outdir, "summary.json"), "w"), indent=1)
arms_order = sorted(pooled)
print("world".ljust(20) + "".join(f"{x:>22}" for x in arms_order))
for world, res in list(out["worlds"].items()) + [("ALL", out["all"])]:
    cells = []
    for arm in arms_order:
        s = res.get(arm)
        cells.append(f"{(s or {}).get('acc_len') or '-'} / {(s or {}).get('decode_tok_s') or '-'} / {(s or {}).get('speedup') or '-'}x"
                     if s else "-")
    print(world.ljust(20) + "".join(f"{c:>22}" for c in cells))
print("cells: acceptance length / decode tok/s / speedup over target alone")
