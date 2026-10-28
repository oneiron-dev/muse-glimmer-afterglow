#!/usr/bin/env python3
"""A real-world check of the supersede decision from a simulated later day (day 2 by default; never training rows).

The day agent writes day-2 notes under its own keys, so a correction often lands under a new key and the day-1 value
stays active. For every day-2 note that holds a world fact which supersedes a day-1 fact (world.json superseded_by),
the pair (the day-1 note holding the old value, the day-2 note) is "replaces" (1). The same day-2 note against the
other day-1 notes whose keys share a word is "both stay true" (2). Labels come from world.json, never from a model.
Each row records whether the day agent itself linked the correction (supersedes set on the note).
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "night"))
sys.path.insert(0, str(ROOT / "decide"))
import scoring  # noqa: E402
from paths import runs_root  # noqa: E402
from prompts import SUP_OPTIONS, pair_prompt  # noqa: E402

WORLDS = ["w2-solo-developer", "w3-shop-owner", "w4-grad-student"]
SEED = 20261014


def holds(text, fact):
    return scoring.score_fact(text, scoring.usable_accept(fact["accept"], fact["value"]))


def words(key):
    return set(re.findall(r"[a-z]+", key.lower())) - {"name", "the", "of", "my"}


def line(n):
    return f"{n['key']}: {n['value']}"


def world_rows(world, runs, rng, negatives_per_note, day=2):
    facts = {f["id"]: f for f in json.loads((ROOT / "worlds" / world / "world.json").read_text())["facts"]}
    old_of = {f["superseded_by"]: f for f in facts.values() if f.get("superseded_by")}
    notes = json.loads((runs / world / str(day) / "notes.json").read_text())["notes"]
    day1 = [n for n in notes if (n.get("day") or 0) < day and n.get("status", "active") == "active"
            or (n.get("day") or 0) < day and n.get("superseded_by") and
            next((m for m in notes if m["id"] == n["superseded_by"]), {}).get("day") == day]
    rows = []
    for n in (n for n in notes if n.get("day") == day):
        new = next((f for fid, f in old_of.items() if holds(n["value"], facts[fid])), None)
        if new is None:
            continue
        new_fact = next(f for fid, f in facts.items() if fid in old_of and old_of[fid] is new)
        olds = [o for o in day1 if holds(o["value"], new)]
        for o in olds:
            rows.append({"id": f"sup-day{day}:{world}:{o['id']}-{n['id']}", "kind": "supersede", "world": world,
                         "split": f"day{day}_check", "source": f"day{day}_correction", "prompt": pair_prompt("supersede", line(o), line(n)),
                         "options": SUP_OPTIONS, "label": "1", "accept": ["1"], "a": line(o), "b": line(n),
                         "fact_ids": [new["id"], new_fact["id"]], "agent_linked": n.get("supersedes") == o["id"]})
        others = [o for o in day1 if o not in olds and words(o["key"]) & words(n["key"])]
        rng.shuffle(others)
        for o in others[:negatives_per_note]:
            rows.append({"id": f"sup-day{day}:{world}:{o['id']}-{n['id']}", "kind": "supersede", "world": world,
                         "split": f"day{day}_check", "source": f"day{day}_other_note", "prompt": pair_prompt("supersede", line(o), line(n)),
                         "options": SUP_OPTIONS, "label": "2", "accept": ["2"], "a": line(o), "b": line(n),
                         "fact_ids": [None, new_fact["id"]], "agent_linked": n.get("supersedes") == o["id"]})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs")
    ap.add_argument("--negatives-per-note", type=int, default=2)
    ap.add_argument("--day", type=int, default=2)
    ap.add_argument("--worlds", nargs="+", default=WORLDS)
    ap.add_argument("--out")
    args = ap.parse_args()
    args.out = args.out or str(ROOT / "decide" / "data" / f"supersede-day{args.day}-check.jsonl")
    runs = runs_root(args.runs)
    rng = random.Random(SEED)
    rows = [r for w in args.worlds if (runs / w / str(args.day) / "notes.json").exists()
            for r in world_rows(w, runs, rng, args.negatives_per_note, args.day)]
    Path(args.out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    pos = [r for r in rows if r["label"] == "1"]
    print(json.dumps({"rows": len(rows), "replaces": len(pos), "agent_linked": sum(r["agent_linked"] for r in pos),
                      "both_true": len(rows) - len(pos), "by_world": {w: sum(r["world"] == w for r in pos) for w in args.worlds}}))


if __name__ == "__main__":
    main()
