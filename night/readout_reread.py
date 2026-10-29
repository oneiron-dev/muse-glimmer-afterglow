#!/usr/bin/env python3
"""Re-read the supersede readout on the notes each night of record saw, with every candidate's p logged, to fit an
ambiguity margin for the readout's links below p 0.9 (lead 29 Oct 23:06: a wrong drop deletes a true fact).

For each world and day: the same inputs as consolidate.find_replacements (every note active before the day, from
notes.json; each new fact the day's notes did not link, from the night's consolidated.json), one supersede_link call
per new note (k=8, every decision kept), and the gold from world.json: the earlier fact the new note's fact
supersedes. Writes one JSON line per new note. No training, no writes under runs/.
Second opinions on the top two candidates (lead 23:11), each as the p of "replaces": the shipped decision adapter
(--d1b-path, its path on the server) on the same pair of note lines, and the base weights with the user's own words
as the new side ("Earlier: <note>" / "New: <what was said>"; the same note-line pair on base is the readout itself).
Run: GLIMMER_BASE_URL=http://<host>:<port>/v1 python3 night/readout_reread.py --out <file.jsonl> [--days 2,3,4,5]
     [--d1b-path /data/meta-night/adapters/decision-d1b.gguf]
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "night"))
from consolidate import READOUT_A_P, SUPERSEDE_ACCEPT_P, change_word_for, readout_entry, runs_root  # noqa: E402
from consolidate import p_replaces  # noqa: E402
from contrast_rows import shape  # noqa: E402
from decide.decider import Decider  # noqa: E402
from decide.prompts import note_line  # noqa: E402

WORLDS = ["w1-ines-varga", "w2-solo-developer", "w3-shop-owner", "w4-grad-student"]


def fact_ids(facts, value):
    norm = lambda t: " ".join(t.lower().replace("-", " ").split())  # noqa: E731  (accept lists may hyphenate: "aurora-pavilion")
    v = norm(value or "")
    return sorted(f["id"] for f in facts if any(norm(a) in v for a in f["accept"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--days", default="2,3,4,5")
    ap.add_argument("--worlds", default=",".join(WORLDS))
    ap.add_argument("--night-dir", default="night", help="the night of record's directory under runs/<w>/<d>/")
    ap.add_argument("--d1b-path", help="the decision adapter's path as loaded on the server (second opinion)")
    args = ap.parse_args()
    url = os.environ["GLIMMER_BASE_URL"]
    endpoint = url[:-3] if url.endswith("/v1") else url
    readout = Decider(endpoint, slot=1)
    d1b = Decider(endpoint, adapter_path=args.d1b_path, slot=1) if args.d1b_path else None
    out = open(args.out, "w")
    for w in args.worlds.split(","):
        facts = json.loads((ROOT / "worlds" / w / "world.json").read_text())["facts"]
        by_fact = {f["id"]: f for f in facts}
        for day in map(int, args.days.split(",")):
            run_dir = runs_root(None) / w / str(day)
            cons_path = run_dir / args.night_dir / "consolidated.json"
            if not cons_path.exists():
                continue
            notes = json.loads((run_dir / "notes.json").read_text())["notes"]
            by_id = {n["id"]: n for n in notes}
            cons = json.loads(cons_path.read_text())
            night_found = {r["new_id"]: r for r in cons.get("replacements", []) if r.get("decision") == "superseded"}
            new = [i for i in cons["items"] if (by_id.get(i["id"], {}).get("day") or 1) == day and i["kind"] == "fact"
                   and (not i.get("corrects") or i["id"] in night_found)]
            earlier_notes = [{"id": n["id"], "key": n["key"], "value": n["value"]} for n in by_id.values()
                             if (n.get("day") or 1) < day and n.get("status", "active") == "active" and not n.get("superseded_by")]
            notes_by_id = {e["id"]: e for e in earlier_notes}
            for n in new:
                r = readout.supersede_link(earlier_notes, {"id": n["id"], "key": n["key"], "value": n["full_value"]}, k=8, accept_p=0.0)
                change = change_word_for(n["source_utterance"], n["full_value"])
                entry = readout_entry(n, r, notes_by_id, change, None)
                new_facts = fact_ids(facts, n["full_value"])
                gold_old = sorted({by_fact[f]["supersedes"] for f in new_facts if by_fact[f].get("supersedes")})
                for c in entry["top2"] + entry["candidates"]:
                    c["facts"] = fact_ids(facts, notes_by_id.get(c["candidate"], {}).get("value"))
                    c["gold"] = bool(set(c["facts"]) & set(gold_old))
                new_line = note_line({"key": n["key"], "value": n["full_value"]})
                for c in entry["top2"]:
                    old_line = note_line(notes_by_id[c["candidate"]])
                    if d1b is not None:
                        c["d1b_p_replaces"] = p_replaces(d1b.pair("supersede", old_line, new_line))
                    c["base_said_p_replaces"] = p_replaces(readout.pair("supersede", old_line, n["source_utterance"]))
                top = entry["top2"][0] if entry["top2"] else None
                same_shape = bool(top) and shape(notes_by_id[top["candidate"]]["value"]) == shape(n["full_value"])
                entry.update({"world": w, "day": day, "new_facts": new_facts, "gold_old": gold_old,
                              "same_shape_top1": same_shape,
                              "fires_rule_a_prime": bool(top) and (top["p_replaces"] >= SUPERSEDE_ACCEPT_P
                                                                    or (top["p_replaces"] >= READOUT_A_P and same_shape and bool(change))),
                              "night_link": {k: night_found[n["id"]].get(k) for k in ("old_id", "linked_by", "p")} if n["id"] in night_found else None,
                              "ms": r["ms"]})
                out.write(json.dumps(entry) + "\n")
                out.flush()
            print(f"{w} day {day}: {len(new)} new notes", flush=True)


if __name__ == "__main__":
    main()
