#!/usr/bin/env python3
"""Drop training rows that still carry a superseded value (lead 29 Oct 23:45; on from night 6).

A superseded note's value can live on in rows about other notes: w3 day 5, the dropped "vendor call time: ring parts
vendors Tuesdays at 10:30" stayed in the rewrites of "vendor call nudge" ("nudge me before I ring the parts vendors
Tuesdays at 10:30"). The superseded notes are those notes.json marks superseded (the day's links) and those
consolidated.json drops as superseded (the day's and the night's links). From each one's value the distinctive spans
are taken: a weekday with its time ("Tuesdays at 10:30"), an ISO date, a code, and a run of two or more capitalised
words ("Back Room 2", "Lena Frost"); a bare time or a single word is too common to filter on and is skipped.
A row that holds a span is dropped, except:
  - a row about an active note whose own value holds the span (another true fact with the same value: "client
    meeting time: seeing clients Tuesdays at 10:30" stays);
  - a row about no note (unknown-key, agent and anchor rows, which show a notes block or tool results) when an active
    note's value holds the span.
Writes the kept rows and a report with every dropped row, counted per source (row form and note).
"""

import argparse
import collections
import json
import re
from pathlib import Path

WEEKDAY_TIME = re.compile(r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)days? (?:at |from )?\d{1,2}:\d{2}\b", re.I)
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
CODE = re.compile(r"\b[A-Z]{2,}[-_]?\w*\d\w*\b|\b[A-Z]+-\d+[\w-]*\b")
CAPS_RUN = re.compile(r"\b[A-Z][\w'-]*(?: (?:[A-Z][\w'-]*|\d+[A-Za-z]?))+\b")


def spans(value):
    out = set()
    for rx in (WEEKDAY_TIME, ISO_DATE, CODE, CAPS_RUN):
        out |= {m.group(0) for m in rx.finditer(value or "")}
    return out


def row_text(row):
    return json.dumps(row["messages"], ensure_ascii=False)


def superseded_notes(notes, consolidated):
    out = {n["id"]: n["value"] for n in notes if n.get("superseded_by") or n.get("status") == "superseded"}
    out.update({d["id"]: d["value"] for d in consolidated.get("dropped", []) if d.get("outcome") == "dropped_as_superseded"})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", required=True, help="the day's notes.json")
    ap.add_argument("--consolidated", required=True)
    ap.add_argument("--train", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    notes = json.loads(Path(args.notes).read_text())["notes"]
    cons = json.loads(Path(args.consolidated).read_text())
    gone = superseded_notes(notes, cons)
    active = {i["id"]: i["full_value"] for i in cons["items"] if i["id"] not in gone}
    by_span = collections.defaultdict(set)  # span -> the superseded notes it came from
    for nid, value in gone.items():
        for s in spans(value):
            by_span[s].add(nid)
    held = {s for s in by_span if any(s.lower() in v.lower() for v in active.values())}
    kept, dropped = [], []
    for line in Path(args.train).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        text = row_text(row).lower()
        hits = sorted(s for s in by_span if s.lower() in text)
        item = str(row.get("item_id") or "")
        if item in active:
            hits = [s for s in hits if s.lower() not in active[item].lower()]
        else:
            hits = [s for s in hits if s not in held]
        if hits:
            dropped.append({"id": row.get("id"), "item_id": item, "form": row.get("form"), "spans": hits,
                            "from_notes": sorted(set().union(*(by_span[s] for s in hits)))})
        else:
            kept.append(line)
    Path(args.out).write_text("".join(line + "\n" for line in kept))
    per_source = collections.Counter(f"{d['form']} / {d['item_id'] if d['item_id'] in active else d['item_id'].split('-')[0]}"
                                     for d in dropped)
    report = {"rule": "drop a row holding a superseded value's span unless the row's own active note holds it (or, for a "
                      "row about no note, any active note holds it)",
              "superseded_notes": len(gone), "spans": {s: sorted(v) for s, v in sorted(by_span.items())},
              "spans_also_active": sorted(held), "rows_in": len(kept) + len(dropped), "rows_kept": len(kept),
              "rows_dropped": len(dropped), "dropped_per_source": dict(per_source.most_common()), "dropped": dropped}
    Path(args.report).write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("superseded_notes", "rows_in", "rows_dropped", "dropped_per_source")}))


if __name__ == "__main__":
    main()
