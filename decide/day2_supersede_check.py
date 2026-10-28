#!/usr/bin/env python3
"""A real-world check of the supersede decision from the simulated day 2 (never training rows).

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


def world_rows(world, runs, rng, negatives_per_note):
    facts = {f["id"]: f for f in json.loads((ROOT / "worlds" / world / "world.json").read_text())["facts"]}
    old_of = {f["superseded_by"]: f for f in facts.values() if f.get("superseded_by")}
    notes = json.loads((runs / world / "2" / "notes.json").read_text())["notes"]
    day1 = [n for n in notes if n.get("day") == 1]
    rows = []
    for n in (n for n in notes if n.get("day") == 2):
        new = next((f for fid, f in old_of.items() if holds(n["value"], facts[fid])), None)
        if new is None:
            continue
        new_fact = next(f for fid, f in facts.items() if fid in old_of and old_of[fid] is new)
        olds = [o for o in day1 if holds(o["value"], new)]
        for o in olds:
            rows.append({"id": f"sup-day2:{world}:{o['id']}-{n['id']}", "kind": "supersede", "world": world,
                         "split": "day2_check", "source": "day2_correction", "prompt": pair_prompt("supersede", line(o), line(n)),
                         "options": SUP_OPTIONS, "label": "1", "accept": ["1"], "a": line(o), "b": line(n),
                         "fact_ids": [new["id"], new_fact["id"]], "agent_linked": n.get("supersedes") == o["id"]})
        others = [o for o in day1 if o not in olds and words(o["key"]) & words(n["key"])]
        rng.shuffle(others)
        for o in others[:negatives_per_note]:
            rows.append({"id": f"sup-day2:{world}:{o['id']}-{n['id']}", "kind": "supersede", "world": world,
                         "split": "day2_check", "source": "day2_other_note", "prompt": pair_prompt("supersede", line(o), line(n)),
                         "options": SUP_OPTIONS, "label": "2", "accept": ["2"], "a": line(o), "b": line(n),
                         "fact_ids": [None, new_fact["id"]], "agent_linked": n.get("supersedes") == o["id"]})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs")
    ap.add_argument("--negatives-per-note", type=int, default=2)
    ap.add_argument("--out", default=str(ROOT / "decide" / "data" / "supersede-day2-check.jsonl"))
    args = ap.parse_args()
    runs = runs_root(args.runs)
    rng = random.Random(SEED)
    rows = [r for w in WORLDS if (runs / w / "2" / "notes.json").exists() for r in world_rows(w, runs, rng, args.negatives_per_note)]
    Path(args.out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    pos = [r for r in rows if r["label"] == "1"]
    print(json.dumps({"rows": len(rows), "replaces": len(pos), "agent_linked": sum(r["agent_linked"] for r in pos),
                      "both_true": len(rows) - len(pos), "by_world": {w: sum(r["world"] == w for r in pos) for w in WORLDS}}))


if __name__ == "__main__":
    main()
