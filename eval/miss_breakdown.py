#!/usr/bin/env python3
"""Why facts were missed in a night: data gaps versus learning errors.

For each fact with fewer than 2 of 3 forward probes passing in arm B (adapter, notes off):
  not_in_notes   - no active note of the day holds an accept term (the day agent never saved it)
  not_trained    - in the notes, but no training row's answer holds an accept term (lost in consolidation
                   or rewriting)
  binding        - trained, and a wrong answer names another fact's value from the same world
  other          - trained, wrong in some other way
Also reports C-only facts (B < 2/3, C >= 2/3) and facts neither arm gets. Reads saved files only.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "night"))
import scoring  # noqa: E402
from paths import runs_root  # noqa: E402


def holds(text, fact):
    return scoring.score_fact(text, scoring.usable_accept(fact["accept"], fact["value"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, default=1)
    ap.add_argument("--runs")
    ap.add_argument("--out")
    args = ap.parse_args()
    run_dir = runs_root(args.runs) / args.world / str(args.day)
    night = run_dir / "night"
    world = json.loads((ROOT / "worlds" / args.world / "world.json").read_text())
    facts = {f["id"]: f for f in world["facts"] if f.get("valid_from", 1) <= args.day}
    e = json.loads((night / "eval.json").read_text())
    notes_doc = json.loads((run_dir / "notes.json").read_text())
    notes = [f"{n['key']}: {n['value']}" for n in notes_doc["notes"] if n.get("status", "active") == "active" and not n.get("superseded_by")]
    train_answers = [json.loads(line)["messages"][1]["content"] for line in (night / "train.jsonl").read_text().splitlines() if line.strip()]
    rows, counts = [], {"not_in_notes": 0, "not_trained": 0, "binding": 0, "other": 0}
    c_only, neither = [], []
    for row in e["facts"]:
        f = facts.get(row["id"])
        if f is None or "B" not in row:
            continue
        b_ok, c_ok = row["B"] >= 2, row.get("C", 0) >= 2
        if b_ok:
            continue
        (c_only if c_ok else neither).append(row["id"])
        in_notes = any(holds(n, f) for n in notes)
        trained = any(holds(t, f) for t in train_answers)
        wrong = [a for a, p in zip(row["answers"]["B"], row["pass"]["B"]) if not p]
        others = sorted({o["id"] for a in wrong for o in facts.values() if o["id"] != f["id"] and holds(a, o)})
        cause = "not_in_notes" if not in_notes else "not_trained" if not trained else "binding" if others else "other"
        counts[cause] += 1
        rows.append({"fact_id": f["id"], "key": f["key"], "value": f["value"], "B": row["B"], "C": row.get("C"),
                     "cause": cause, "confused_with": [(o, facts[o]["value"]) for o in others], "B_answers": row["answers"]["B"]})
    report = {"world": args.world, "day": args.day, "facts": len(facts), "missed_in_B": len(rows), "causes": counts,
              "c_only": c_only, "neither": neither, "rows": rows}
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("world", "facts", "missed_in_B", "causes")} | {"c_only": len(c_only), "neither": len(neither)}))


if __name__ == "__main__":
    main()
