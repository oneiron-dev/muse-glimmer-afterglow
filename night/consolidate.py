#!/usr/bin/env python3
"""Night step 1: consolidate the day into the items the night trains on.

Reads runs/<w>/<d>/notes.json (the day agent's notes, with supersedes links) and daylog.jsonl (what the
user said). Superseded notes are dropped; a note that corrects an earlier one keeps the old value as
"corrects". The teacher model then writes, per note: its kind (fact | preference | procedure), one plain
statement, and the shortest exact span of the value that identifies it (the "core" that rewrites and
answers must contain). "outcomes" records, per note, what the night chose: kept, merged_as_duplicate or
dropped_as_superseded, with the reason (the morning report shows it). Writes runs/<w>/<d>/night/consolidated.json in the format generate_rewrites.py reads.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import teacher as teacher_mod  # noqa: E402
from paths import runs_root  # noqa: E402

BATCH = 8
PROMPT = """An assistant saved these notes about its user, {name}, during the day. Each note has an id, a key, a value and what {first} said.

{notes}

For each note write exactly one line, in this format and nothing else:
<id> | <kind> | <statement> | <core> | <same as>
- kind: fact, preference (how {first} wants the assistant to work) or procedure (steps to follow).
- statement: one plain sentence stating this note about {first}, with full names, using only what {first} said.
- core: 1 to 4 words copied exactly from the value: the name, number, code, time, date or place that answers a question about this note. For a preference or procedure, write the value again.
Examples of core (other people, not {first}): value "senior buyer at Halden Foods" -> core "senior buyer"; value "warehouse on Pier 4 in Tarnby" -> core "Tarnby"; value "weekly sync on Mondays at 09:15" -> core "Mondays at 09:15"; value "Rowan Pike, the CFO" -> core "Rowan Pike".
- same as: the id of another note above that says exactly the same thing, or - if there is none.
Every note gets its own line, even when two notes come from the same sentence."""

LINE_RE = re.compile(r"^\s*(N\d+)\s*\|\s*(fact|preference|procedure)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*(?:\|\s*(N\d+|-)\s*)?$", re.IGNORECASE)


def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9:./# -]", "", text.lower())).strip()


def load_day(run_dir):
    notes_doc = json.loads((run_dir / "notes.json").read_text())
    notes = notes_doc["notes"] if isinstance(notes_doc, dict) else notes_doc
    turns = {}
    for line in (run_dir / "daylog.jsonl").read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("role") == "user":
                turns[rec["turn"]] = rec["content"]
    return notes, turns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--teacher", default="glimmer", choices=sorted(teacher_mod.TEACHERS))
    ap.add_argument("--runs", help="runs root (default: AFTERGLOW_RUNS or the main checkout's runs/)")
    args = ap.parse_args()
    run_dir = runs_root(args.runs) / args.world / str(args.day)
    out_dir = run_dir / "night"
    out_dir.mkdir(parents=True, exist_ok=True)
    notes, turns = load_day(run_dir)
    name = json.loads((Path(__file__).resolve().parent.parent / "worlds" / args.world / "world.json").read_text())["persona"]["name"]
    first = name.split()[0]
    by_id = {n["id"]: n for n in notes}
    active, dropped, seen = [], [], {}
    for n in notes:
        if n.get("status", "active") != "active" or n.get("superseded_by"):
            continue
        sig = (norm(n["key"]), norm(n["value"]))
        if sig in seen:  # the same key and value saved twice
            dropped.append({"id": n["id"], "outcome": "merged_as_duplicate", "why": f"same key and value as {seen[sig]}"})
            continue
        seen[sig] = n["id"]
        active.append(n)
    items, proposals, log_path = [], [], out_dir / "teacher-calls.jsonl"
    for i in range(0, len(active), BATCH):
        batch = active[i:i + BATCH]
        text = "\n".join(f"{n['id']} | key: {n['key']} | value: {n['value']} | {first} said: \"{turns.get(n.get('turn'), '')}\"" for n in batch)
        reply, _, _ = teacher_mod.complete(args.teacher, [{"role": "user", "content": PROMPT.format(notes=text, name=name, first=first)}],
                                           max_tokens=1500, log_path=log_path, purpose=f"consolidate {batch[0]['id']}..{batch[-1]['id']}")
        parsed = {m.group(1): m for m in (LINE_RE.match(line) for line in reply.splitlines()) if m}
        for n in batch:
            m = parsed.get(n["id"])
            if m is None:
                kind, statement, core = "fact", f"{n['key']}: {n['value']}.", n["value"]
            else:
                kind, statement, core = m.group(2).lower(), m.group(3), m.group(4)
            if core.lower() not in n["value"].lower():
                core = n["value"]
            same_as = m.group(5) if m is not None and m.group(5) and m.group(5) != "-" else None
            if same_as and same_as in by_id and same_as != n["id"]:
                # The model's merge proposal is only advice: the code merges identical key + value (above), nothing else.
                proposals.append({"id": n["id"], "model_proposed_merge_with": same_as, "decision": "kept separate",
                                  "why": f"values differ: {n['value']!r} vs {by_id[same_as]['value']!r}"})
            old = by_id.get(n.get("supersedes")) if n.get("supersedes") else None
            items.append({"id": n["id"], "kind": kind, "key": n["key"], "full_value": n["value"], "value": core,
                          "accept": [core], "statement": statement, "source_utterance": turns.get(n.get("turn"), ""),
                          "corrects": old["value"] if old else None})
    superseded = [{"id": n["id"], "outcome": "dropped_as_superseded",
                   "why": f"superseded by {n.get('superseded_by')}: the value {n['value']!r} is no longer true"}
                  for n in notes if n.get("superseded_by") or n.get("status") == "superseded"]
    kept = [{"id": i["id"], "outcome": "kept", "why": f"learn as {i['kind']}" + (f"; corrects {i['corrects']!r}" if i["corrects"] else "")}
            for i in items]
    overruled = {p["id"]: p for p in proposals}
    for o in kept:
        if o["id"] in overruled:
            o["model_proposed_merge_with"] = overruled[o["id"]]["model_proposed_merge_with"]
            o["why"] += f"; the model proposed merging it with {o['model_proposed_merge_with']}, kept separate because the {overruled[o['id']]['why']}"
    outcomes = sorted(kept + dropped + superseded, key=lambda o: o["id"])
    doc = {"world": args.world, "day": args.day, "user": name, "source": f"notes.json + daylog.jsonl, consolidated by {args.teacher}",
           "items": items, "dropped": dropped + superseded, "outcomes": outcomes, "overruled_merge_proposals": proposals}
    (out_dir / "consolidated.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({"active_notes": len(active), "items": len(items), "dropped": len(doc["dropped"]),
                      "kinds": {k: sum(1 for i in items if i["kind"] == k) for k in ("fact", "preference", "procedure")}}))


if __name__ == "__main__":
    main()
