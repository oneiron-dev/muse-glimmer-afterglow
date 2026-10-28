#!/usr/bin/env python3
"""Night N beside the world's previous kept night, per world: right / wrong / abstain and the Omniscience score of
arms B and D on the forward probes, split into facts from earlier days that are still true (both nights asked) and
facts new on day N; the stale-value rate; never-told; the gate; forgetting; the decision.

  eval/compare_night2.py --day 2 [--worlds ...] [--out FILE]
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "night"))
from omniscience import outcome  # noqa: E402
from paths import runs_root  # noqa: E402
from score_peft import fact_valid_on  # noqa: E402

WORLDS = ["w1-ines-varga", "w2-solo-developer", "w3-shop-owner", "w4-grad-student"]


def rwa(results, ids):
    c = {"right": 0, "wrong": 0, "abstain": 0}
    for r in results:
        if r["id"] in ids:
            c[outcome(r)] += 1
    n = sum(c.values())
    return {**c, "n": n, "score": round((c["right"] - c["wrong"]) / n, 3) if n else None}


def night_summary(night, earlier_ids, new_ids):
    out = {}
    for arm in "BD":
        res = json.loads((night / "eval" / f"score-night-{arm}.json").read_text())["results"]
        stale = [r for r in res if r["kind"] == "stale"]
        nt = [r for r in res if r["kind"] == "never_told"]
        out[arm] = {"earlier_facts": rwa(res, earlier_ids), "new_facts": rwa(res, new_ids),
                    "stale_returned": sum(r["pass"] for r in stale), "stale_n": len(stale),
                    "never_told": sum(r["pass"] for r in nt) if nt else None}
    e = json.loads((night / "eval.json").read_text())
    d = json.loads((night / "decision.json").read_text())
    out.update({"decision": d["decision"], "checks": d["checks"], "abstention": d.get("abstention"),
                "abstention_with_control_vector": d.get("abstention_with_control_vector"),
                "forgot": {k: e["forgot"][k] for k in ("delta", "ci95")} if e.get("forgot") else None,
                "facts_learned": e["learned"]["facts_learned"]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--worlds", nargs="*", default=WORLDS)
    ap.add_argument("--runs")
    ap.add_argument("--out")
    args = ap.parse_args()
    runs = runs_root(args.runs)
    table = {}
    for w in args.worlds:
        night = runs / w / str(args.day) / "night"
        prev = runs / w / str(args.day - 1) / "night"
        if not (night / "decision.json").exists():
            continue
        facts = {f["id"]: f for f in json.loads((ROOT / "worlds" / w / "world.json").read_text())["facts"]}
        probes = [json.loads(line) for line in (ROOT / "worlds" / w / "heldout" / "probes.jsonl").read_text().splitlines() if line.strip()]
        fwd = [p for p in probes if p["kind"] == "forward"]
        valid = {fid for fid, f in facts.items() if fact_valid_on(f, facts, args.day)}  # the truth on that day, not today's file
        earlier = {p["id"] for p in fwd if p["fact_id"] in valid and (facts[p["fact_id"]].get("valid_from") or 1) < args.day}
        new = {p["id"] for p in fwd if p["fact_id"] in valid and (facts[p["fact_id"]].get("valid_from") or 1) == args.day}
        row = {"night": night_summary(night, earlier, new)}
        if (prev / "eval" / "score-night-B.json").exists():
            prev_b = json.loads((prev / "eval" / "score-night-B.json").read_text())["results"]
            prev_d = json.loads((prev / "eval" / "score-night-D.json").read_text())["results"]
            row["previous"] = {"B": {"earlier_facts": rwa(prev_b, earlier)}, "D": {"earlier_facts": rwa(prev_d, earlier)}}
        table[w] = row
        n, p = row["night"], row.get("previous", {})
        fmt = lambda c: f"{c['right']}/{c['wrong']}/{c['abstain']} ({c['score']:+.3f})" if c and c["n"] else "-"
        g = n["abstention"] or {}
        print(f"{w}: {n['decision']}; B earlier facts {fmt(p.get('B', {}).get('earlier_facts'))} -> {fmt(n['B']['earlier_facts'])}, "
              f"new {fmt(n['B']['new_facts'])}; D earlier {fmt(p.get('D', {}).get('earlier_facts'))} -> {fmt(n['D']['earlier_facts'])}, "
              f"new {fmt(n['D']['new_facts'])}; stale B {n['B']['stale_returned']}/{n['B']['stale_n']}; never-told B {n['B']['never_told']} "
              f"D {n['D']['never_told']}; gate {g.get('base')}->{g.get('after')}; forgot {n['forgot']}")
    if args.out:
        Path(args.out).write_text(json.dumps(table, indent=1) + "\n")


if __name__ == "__main__":
    main()
