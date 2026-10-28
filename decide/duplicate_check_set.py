#!/usr/bin/env python3
"""A real-world check of the duplicate decision from a simulated later day (never training rows).

The pairs are exactly the ones the night's duplicate check reads (decide.decider.duplicate_candidate_pairs over the
notes active at the end of the day), kept when at least one note was written on that day (later-day notes never reach
training). Each note maps to the world fact it holds that was true on the note's day; a note that holds two such facts
is matched by key words, and a note with no clear fact is left out. Label: 1 when both notes map to the same fact,
2 when they map to different facts. Labels come from world.json, never from a model.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "night"))
sys.path.insert(0, str(ROOT / "decide"))
import scoring  # noqa: E402
from decider import duplicate_candidate_pairs  # noqa: E402
from paths import runs_root  # noqa: E402
from prompts import DUP_OPTIONS, note_line, pair_prompt  # noqa: E402

WORLDS = ["w1-ines-varga", "w2-solo-developer", "w3-shop-owner", "w4-grad-student"]


def words(text):
    return set(re.findall(r"[a-z]+", text.lower())) - {"the", "of", "my", "name", "time", "date"}


def fact_for(note, facts):
    """The world fact this note holds, among facts true on the note's day; None when unclear."""
    day = note.get("day") or 1
    true_then = [f for f in facts.values() if f.get("valid_from", 1) <= day
                 and not (f.get("superseded_by") and facts[f["superseded_by"]].get("valid_from", 1) <= day)]
    held = [f for f in true_then if scoring.score_fact(note["value"], scoring.usable_accept(f["accept"], f["value"]))]
    if len(held) == 1:
        return held[0]["id"]
    if not held:
        return None
    scored = sorted(((len(words(note["key"]) & words(f["key"])), f["id"]) for f in held), reverse=True)
    return scored[0][1] if scored[0][0] > scored[1][0] else None


def world_rows(world, runs, day):
    facts = {f["id"]: f for f in json.loads((ROOT / "worlds" / world / "world.json").read_text())["facts"]}
    notes = [n for n in json.loads((runs / world / str(day) / "notes.json").read_text())["notes"]
             if n.get("status", "active") == "active"]
    rows = []
    for a, b in duplicate_candidate_pairs(notes):
        if day not in (a.get("day"), b.get("day")):
            continue
        fa, fb = fact_for(a, facts), fact_for(b, facts)
        if fa is None or fb is None:
            continue
        label = "1" if fa == fb else "2"
        rows.append({"id": f"dup-day{day}:{world}:{a['id']}-{b['id']}", "kind": "duplicate", "world": world,
                     "split": f"day{day}_check", "source": f"day{day}_neighbours", "prompt": pair_prompt("duplicate", note_line(a), note_line(b)),
                     "options": DUP_OPTIONS, "label": label, "accept": [label], "a": note_line(a), "b": note_line(b),
                     "fact_ids": [fa, fb], "shared_value": bool(set(re.findall(r"\w+", a["value"].lower())) & set(re.findall(r"\w+", b["value"].lower())))})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs")
    ap.add_argument("--day", type=int, default=3)
    ap.add_argument("--worlds", nargs="+", default=WORLDS)
    ap.add_argument("--out")
    args = ap.parse_args()
    runs = runs_root(args.runs)
    out = Path(args.out or ROOT / "decide" / "data" / f"duplicate-day{args.day}-check.jsonl")
    rows = [r for w in args.worlds if (runs / w / str(args.day) / "notes.json").exists() for r in world_rows(w, runs, args.day)]
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(json.dumps({"rows": len(rows), "same": sum(r["label"] == "1" for r in rows),
                      "different_shared_value": sum(r["label"] == "2" and r["shared_value"] for r in rows),
                      "by_world": {w: sum(r["world"] == w for r in rows) for w in args.worlds}}))


if __name__ == "__main__":
    main()
