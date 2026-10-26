#!/usr/bin/env python3
"""Night step 1: consolidate the day into the items the night trains on.

Reads runs/<w>/<d>/notes.json (the day agent's notes, with supersedes links) and daylog.jsonl (what the
user said). Superseded notes are dropped; a note that corrects an earlier one keeps the old value as
"corrects". "outcomes" records, per note, what the night chose: kept, merged_as_duplicate or
dropped_as_superseded, with the reason (the morning report shows it). The teacher model then writes, per note: its kind (fact | preference | procedure), one plain
statement, and the shortest exact span of the value that identifies it (the "core" that rewrites and
answers must contain). Writes runs/<w>/<d>/night/consolidated.json in the format generate_rewrites.py reads.
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
<id> | <kind> | <statement> | <core>
- kind: fact, preference (how {first} wants the assistant to work) or procedure (steps to follow).
- statement: one plain sentence stating the note about {first}, with full names, using only what {first} said.
- core: the shortest exact part of the value that identifies it (a name, number, code, time, date or place), copied character for character from the value. For a preference or procedure, write the value again.
If a note only repeats another note, write: <id> | duplicate | <id of the other note> | -"""

LINE_RE = re.compile(r"^\s*(N\d+)\s*\|\s*(fact|preference|procedure|duplicate)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*$", re.IGNORECASE)


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
    active = [n for n in notes if n.get("status", "active") == "active" and not n.get("superseded_by")]
    items, dropped, log_path = [], [], out_dir / "teacher-calls.jsonl"
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
            if kind == "duplicate":
                dropped.append({"id": n["id"], "outcome": "merged_as_duplicate", "why": f"repeats {statement}"})
                continue
            if core.lower() not in n["value"].lower():
                core = n["value"]
            old = by_id.get(n.get("supersedes")) if n.get("supersedes") else None
            items.append({"id": n["id"], "kind": kind, "key": n["key"], "full_value": n["value"], "value": core,
                          "accept": [core], "statement": statement, "source_utterance": turns.get(n.get("turn"), ""),
                          "corrects": old["value"] if old else None})
    superseded = [{"id": n["id"], "outcome": "dropped_as_superseded",
                   "why": f"superseded by {n.get('superseded_by')}: the value {n['value']!r} is no longer true"}
                  for n in notes if n.get("superseded_by") or n.get("status") == "superseded"]
    kept = [{"id": i["id"], "outcome": "kept", "why": f"learn as {i['kind']}" + (f"; corrects {i['corrects']!r}" if i["corrects"] else "")}
            for i in items]
    outcomes = sorted(kept + dropped + superseded, key=lambda o: o["id"])
    doc = {"world": args.world, "day": args.day, "user": name, "source": f"notes.json + daylog.jsonl, consolidated by {args.teacher}",
           "items": items, "dropped": dropped + superseded, "outcomes": outcomes}
    (out_dir / "consolidated.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({"active_notes": len(active), "items": len(items), "dropped": len(doc["dropped"]),
                      "kinds": {k: sum(1 for i in items if i["kind"] == k) for k in ("fact", "preference", "procedure")}}))


if __name__ == "__main__":
    main()
