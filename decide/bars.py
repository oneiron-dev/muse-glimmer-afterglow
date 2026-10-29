#!/usr/bin/env python3
"""The lead's three bars for a decision arm, from its readout file (decide/readout.py output).

1. Route, w4: forward score >= always-look-up's (0.656) while asking on all 30 never-told questions. The look-up
   threshold is the one with the best score on the w1-w3 calibration rows (decide/score_decisions.py fallback).
2. Supersede: links at p >= 0.9 on the clean w4 day-2 corrections and on the day-3 corrections, with no wrong link.
3. Duplicate: no false "same fact" at p >= 0.9 on the real day-3 pairs; accuracy on the w4 duplicate test.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "decide"))
import notes_agreement_gate as gate  # noqa: E402
import score_decisions as S  # noqa: E402

TEST_WORLD = "w4-grad-student"
ALWAYS_LOOK_UP_FORWARD = 0.6556


def route_bar(results, rows_by_id):
    route = [r for r in results if rows_by_id[r["id"]]["kind"] == "route"]
    ids = {r["id"] for r in route}
    runs = gate.runs_root(None)
    decision = [x for w in sorted({rows_by_id[i]["world"] for i in ids})
                for x in gate.world_rows(w, runs, "decision", "strict", picks(route, rows_by_id))
                if f"route:{x['world']}:{x['id']}" in ids]
    tau = float(S.fallback(decision, route, rows_by_id)["chosen_tau"])
    p = {r["id"]: r["p"] for r in route}
    test = [{**x, "route": "notes"} if p[f"route:{x['world']}:{x['id']}"] < tau else x for x in decision
            if rows_by_id[f"route:{x['world']}:{x['id']}"]["split"] == "test"]
    out = {"tau": tau}
    for name, sel in (("forward", lambda x: x["kind"] == "forward"), ("never_told", lambda x: x["kind"] == "never_told"),
                      ("all", lambda x: True)):
        s = gate.score_rows([x for x in test if sel(x)])["lookup_scored_by_d"]
        out[name] = {k: s[k] for k in ("right", "wrong", "asked", "score")}
    out["passed"] = out["forward"]["score"] >= ALWAYS_LOOK_UP_FORWARD and out["never_told"]["asked"] == 30
    return out


def picks(results, rows_by_id):
    return {r["id"]: (None if r["pick"] == "0" else rows_by_id[r["id"]]["note_ids"][int(r["pick"]) - 1]) for r in results}


def pair_bar(results, rows_by_id, kind, split, world=None):
    rows = [r for r in results if rows_by_id[r["id"]]["kind"] == kind and rows_by_id[r["id"]]["split"] == split
            and (world is None or rows_by_id[r["id"]]["world"] == world)]
    if not rows:
        return None
    links = S.links_at(rows)
    return {"n": len(rows), "accuracy": round(sum(r["right"] for r in rows) / len(rows), 4), **links}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="append", required=True, help="name=readout.jsonl")
    ap.add_argument("--out")
    args = ap.parse_args()
    rows_by_id = {r["id"]: r for p in sorted(S.DATA.glob("*-test.jsonl")) + sorted(S.DATA.glob("*-calibration.jsonl"))
                  + sorted(S.DATA.glob("*-check.jsonl")) for r in S.read_jsonl(p)}
    report = {}
    for spec in args.arm:
        name, path = spec.split("=", 1)
        res = [r for r in S.read_jsonl(path) if r["id"] in rows_by_id]
        sup_w4 = pair_bar(res, rows_by_id, "supersede", "day2_check", TEST_WORLD)
        sup_d3 = pair_bar(res, rows_by_id, "supersede", "day3_check")
        dup_d3 = pair_bar(res, rows_by_id, "duplicate", "day3_check")
        report[name] = {
            "route": route_bar(res, rows_by_id),
            "supersede": {"day2_w4": sup_w4, "day3": sup_d3,
                          "passed": bool(sup_w4 and sup_d3 and sup_w4["wrong_links"] == 0 and sup_d3["wrong_links"] == 0
                                         and sup_w4["links"] + sup_d3["links"] > 0)},
            "duplicate": {"w4_test": pair_bar(res, rows_by_id, "duplicate", "test"), "day3": dup_d3,
                          "passed": bool(dup_d3 and dup_d3["wrong_links"] == 0)},
        }
        r = report[name]
        print(f"{name}: route tau {r['route']['tau']} fwd {r['route']['forward']['score']} nt asked {r['route']['never_told']['asked']}/30 "
              f"all {r['route']['all']['score']} -> {'PASS' if r['route']['passed'] else 'miss'} | supersede w4-day2 "
              f"{sup_w4 and (sup_w4['links'], sup_w4['true_pairs'], sup_w4['wrong_links'])} day3 "
              f"{sup_d3 and (sup_d3['links'], sup_d3['true_pairs'], sup_d3['wrong_links'])} -> "
              f"{'PASS' if r['supersede']['passed'] else 'miss'} | duplicate day3 false-same "
              f"{dup_d3 and dup_d3['wrong_links']} w4 test acc {r['duplicate']['w4_test'] and r['duplicate']['w4_test']['accuracy']} -> "
              f"{'PASS' if r['duplicate']['passed'] else 'miss'}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1) + "\n")


if __name__ == "__main__":
    main()
