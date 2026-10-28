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
import os
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


def feedback_turns(run_dir):
    """The day's style-feedback turns (daylog user records with "feedback"): passed through for Meta steering's
    proposals, never training text."""
    out = []
    for line in (run_dir / "daylog.jsonl").read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("role") == "user" and rec.get("feedback"):
                out.append({"turn": rec["turn"], "content": rec["content"], **rec["feedback"]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--teacher", default="glimmer", choices=sorted(teacher_mod.TEACHERS))
    ap.add_argument("--runs", help="runs root (default: AFTERGLOW_RUNS or the main checkout's runs/)")
    ap.add_argument("--refresh-outcomes", action="store_true", help="only rewrite the outcome fields of an existing consolidated.json")
    ap.add_argument("--out-dir", help="where consolidated.json goes (default runs/<w>/<d>/night)")
    ap.add_argument("--duplicate-check-only", action="store_true", help="only add the duplicate-check log (base weights) to an existing consolidated.json")
    ap.add_argument("--reuse-from", nargs="*", default=[], help="earlier nights' consolidated.json: an active note with "
                    "the same id, key and value as one of their items keeps that item (no teacher call)")
    args = ap.parse_args()
    run_dir = runs_root(args.runs) / args.world / str(args.day)
    if args.refresh_outcomes:
        refresh_outcomes(run_dir)
        return
    out_dir = Path(args.out_dir) if args.out_dir else run_dir / "night"
    out_dir.mkdir(parents=True, exist_ok=True)
    notes, turns = load_day(run_dir)
    name = json.loads((Path(__file__).resolve().parent.parent / "worlds" / args.world / "world.json").read_text())["persona"]["name"]
    first = name.split()[0]
    by_id = {n["id"]: n for n in notes}
    feedback = feedback_turns(run_dir)
    fb_turns = {f["turn"] for f in feedback}
    held = [n for n in notes if (n.get("day") or 1) == args.day and n.get("turn") in fb_turns]
    for f in feedback:
        f["notes"] = [n["id"] for n in held if n.get("turn") == f["turn"]]
    notes = [n for n in notes if n not in held]  # a note saved at a feedback turn is not learned
    active, merges = duplicate_merges(notes)
    if args.duplicate_check_only:  # add the duplicate-check log to an existing consolidated.json (no other change)
        path = out_dir / "consolidated.json"
        doc = json.loads(path.read_text())
        doc["duplicate_check"] = duplicate_check(active)
        path.write_text(json.dumps(doc, indent=1) + "\n")
        print(json.dumps({k: v for k, v in doc["duplicate_check"].items() if k not in ("same", "disagreements")}))
        return
    dup_log = duplicate_check(active) if args.day >= 2 else None
    items, proposals, log_path = [], [], out_dir / "teacher-calls.jsonl"
    earlier = {}
    for path in args.reuse_from:  # later files win
        earlier.update({i["id"]: i for i in json.loads(Path(path).read_text())["items"]})
    reused = [n for n in active if n["id"] in earlier and earlier[n["id"]]["key"] == n["key"]
              and earlier[n["id"]]["full_value"] == n["value"]]
    items += [{**earlier[n["id"]], "reused_from_day": n.get("day")} for n in reused]
    reused_ids = {n["id"] for n in reused}
    to_ask = [n for n in active if n["id"] not in reused_ids]
    for i in range(0, len(to_ask), BATCH):
        batch = to_ask[i:i + BATCH]
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
    replaced = find_replacements(items, by_id, args.day, first, args.teacher, log_path) if args.day >= 2 else []
    gone = {r["old_id"] for r in replaced if r["decision"] == "superseded"}
    for r in replaced:
        if r["decision"] == "superseded":
            new_item = next(i for i in items if i["id"] == r["new_id"])  # noqa: B023
            new_item["corrects"] = r["old_value"]
    items = [i for i in items if i["id"] not in gone]
    doc = {"world": args.world, "day": args.day, "user": name, "source": f"notes.json + daylog.jsonl, consolidated by {args.teacher}",
           "items": items, "overruled_merge_proposals": proposals, "replacements": replaced,
           **({"duplicate_check": dup_log} if dup_log else {}),
           "feedback": feedback, "held_as_feedback": [{"id": n["id"], "key": n["key"], "value": n["value"], "turn": n.get("turn"),
                                                      "outcome": "held_as_feedback", "why": "saved at a style-feedback turn: "
                                                      "passed to Meta steering, not learned"} for n in held]}
    doc.update(outcome_fields(notes, items, merges, proposals))
    for r in replaced:
        if r["decision"] == "superseded":
            doc["dropped"].append({"id": r["old_id"], "key": r["old_key"], "value": r["old_value"], "outcome": "dropped_as_superseded",
                                   "superseded_by": r["new_id"], "why": f"replaced by {r['new_id']} ({r['new_key']}: {r['new_value']!r}); "
                                   f"found by the night ({r['linked_by']}), the day's note had no link", "linked_by": r["linked_by"]})
            doc["outcomes"] = sorted([o for o in doc["outcomes"] if o["id"] != r["old_id"]] + [doc["dropped"][-1]], key=lambda o: o["id"])
    (out_dir / "consolidated.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({"active_notes": len(active), "items": len(items), "reused": len(reused), "dropped": len(doc["dropped"]),
                      "kinds": {k: sum(1 for i in items if i["kind"] == k) for k in ("fact", "preference", "procedure")}}))


REPLACE_PROMPT = """An assistant keeps notes about its user, {first}. Some notes saved on earlier days:
{earlier}

Today {first} said: "{said}"
and the assistant saved a new note: {key}: {value}

Does the new note replace one of the earlier notes, that is, the same fact about {first} with a new value (a changed
time, date, room, person or name)? Answer with the earlier note's id only, or "none" if it is a new fact."""


def duplicate_check(active):
    """Meta decide's duplicate check on BASE weights (lead 08:22: on the day-3 set base makes 0 false "same" at
    p >= 0.9, D1 makes 2), logged beside the code rule (identical key + value stays the decision of record): every
    pair it calls the same fact at p >= 0.9 that the rule kept apart is a logged disagreement."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from decide.decider import Decider
        url = teacher_mod.serving_base_url()
        d = Decider(url[:-3] if url.endswith("/v1") else url, slot=int(os.environ.get("NIGHT_SLOT", "1")), trace="night_duplicate")
        k = int(os.environ.get("D1_DUPLICATE_K", "0"))  # 0 = the decider's default; 2 under load (Meta decide)
        r = d.duplicate_check([{"id": n["id"], "key": n["key"], "value": n["value"]} for n in active], **({"k": k} if k else {}))
    except Exception as e:  # noqa: BLE001  (the server is down or the call fails): skipped, said so
        return {"skipped": f"{type(e).__name__}: {e}"}
    return {"weights": "base", "same": r["same"], "disagreements": r["same"], "pairs_checked": len(r["decisions"]), "ms": r["ms"],
            "k": k or "default",
            "rule": "logged only; the code rule (identical key + value) decides"}


def find_replacements(items, by_id, day, first, teacher, log_path):
    """Corrections the day's notes did not link: a note saved today that holds a new value for an earlier, still
    active note under another key. The teacher names the earlier note; code accepts it only when the old note is from
    an earlier day, is a fact, and its value has the same shape (time, date, room, person...) as the new one."""
    from contrast_rows import shape

    earlier = [i for i in items if (by_id[i["id"]].get("day") or 1) < day and i["kind"] == "fact"]
    new = [i for i in items if (by_id[i["id"]].get("day") or 1) == day and i["kind"] == "fact" and not i.get("corrects")]
    if not earlier or not new:
        return []
    by_item = {i["id"]: i for i in earlier}
    all_items = {i["id"]: i for i in items if (by_id[i["id"]].get("day") or 1) < day}  # any kind, for the readout's links
    out, taken = [], set()
    try:  # Meta decide's one-pass supersede readout (base Glimmer, slot 1); accepted at p >= its accept_p (0.9)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from decide.decider import Decider
        url = teacher_mod.serving_base_url()
        readout = Decider(url[:-3] if url.endswith("/v1") else url, slot=1)
    except Exception as e:  # noqa: BLE001  (not on this checkout, or the server is down): the rule below only
        readout, readout_error = None, f"{type(e).__name__}: {e}"
    # the readout's input contract (decide.candidates.supersede_candidates): every note active before today, of any
    # kind, as the agent saved it, taken before any rule marks notes superseded
    earlier_notes = [{"id": n["id"], "key": n["key"], "value": n["value"]} for n in by_id.values()
                     if (n.get("day") or 1) < day and n.get("status", "active") == "active" and not n.get("superseded_by")]

    def words(text):
        return {w for w in re.findall(r"[a-z0-9:]+", text.lower().replace("_", " ")) if len(w) > 1}

    for n in new:
        if readout is not None:
            r = readout.supersede_link(earlier_notes, {"id": n["id"], "key": n["key"], "value": n["full_value"]}, k=8)
            if r["link"] and r["link"] in all_items and r["link"] not in taken:
                old = all_items[r["link"]]
                taken.add(old["id"])
                p_link = max((d["p"] for d in r["decisions"] if d["candidate"] == old["id"]), default=None)
                out.append({"new_id": n["id"], "new_key": n["key"], "new_value": n["full_value"], "old_id": old["id"],
                            "old_key": old["key"], "old_value": old["full_value"], "decision": "superseded",
                            "linked_by": "readout", "p": p_link, "prompt_id": r["prompt_id"]})
                continue
        # the candidates: earlier facts with the same value shape, most shared words first (key, value, what was said)
        said = words(f"{n['key']} {n['full_value']} {n['source_utterance']}")
        cands = sorted((i for i in earlier if shape(i["value"]) == shape(n["value"])),
                       key=lambda i: -len(words(f"{i['key']} {i['full_value']}") & said))[:6]
        if not cands:
            continue
        listing = "\n".join(f"{i['id']} | {i['key'].replace('_', ' ')}: {i['full_value']}" for i in cands)
        prompt = REPLACE_PROMPT.format(first=first, earlier=listing, said=n["source_utterance"], key=n["key"], value=n["full_value"])
        try:
            reply, _, _ = teacher_mod.complete(teacher, [{"role": "user", "content": prompt}], max_tokens=800, log_path=log_path,
                                               purpose=f"replacement {n['id']}")
        except teacher_mod.TeacherError as e:  # no answer: the note stays a new fact, and the receipt says so
            out.append({"new_id": n["id"], "new_key": n["key"], "new_value": n["full_value"], "decision": f"not judged: {e}"})
            continue
        m = re.search(r"\bN\d{4}\b", reply)
        if not m or m.group(0) not in by_item:
            continue
        old = by_item[m.group(0)]
        same_shape = shape(old["value"]) == shape(n["value"])
        decision = "superseded" if same_shape and old["id"] not in taken else "kept: " + (
            "already replaced" if old["id"] in taken else f"value shapes differ ({shape(old['value'])} vs {shape(n['value'])})")
        if decision == "superseded":
            taken.add(old["id"])
        out.append({"new_id": n["id"], "new_key": n["key"], "new_value": n["full_value"], "old_id": old["id"], "old_key": old["key"],
                    "old_value": old["full_value"], "decision": decision, "linked_by": "consolidation rule"})
    if readout is None:
        out.append({"decision": f"readout not used: {readout_error}"})
    return out


def outcome_fields(notes, items, merges, proposals):
    """Per-note outcome for the morning report: kept / merged_as_duplicate / dropped_as_superseded, with the reason."""
    superseded = [{"id": n["id"], "key": n["key"], "value": n["value"], "outcome": "dropped_as_superseded",
                   "superseded_by": n.get("superseded_by"), "linked_by": "day agent",
                   "why": f"superseded by {n.get('superseded_by')}: the value {n['value']!r} is no longer true"}
                  for n in notes if n.get("superseded_by") or n.get("status") == "superseded"]
    kept = [{"id": i["id"], "key": i["key"], "value": i["full_value"], "outcome": "kept", "kind": i["kind"],
             "corrects": i["corrects"], "why": f"learn as {i['kind']}" + (f"; corrects {i['corrects']!r}" if i["corrects"] else "")}
            for i in items]
    overruled = {p["id"]: p for p in proposals}
    for o in kept:
        if o["id"] in overruled:
            o["model_proposed_merge_with"] = overruled[o["id"]]["model_proposed_merge_with"]
            o["why"] += f"; the model proposed merging it with {o['model_proposed_merge_with']}, kept separate because the {overruled[o['id']]['why']}"
    return {"dropped": merges + superseded, "outcomes": sorted(kept + merges + superseded, key=lambda o: o["id"])}


def duplicate_merges(notes):
    """Active notes with the same key and value as an earlier one merge into it; returns (active, merges)."""
    active, merges, seen = [], [], {}
    for n in notes:
        if n.get("status", "active") != "active" or n.get("superseded_by"):
            continue
        sig = (norm(n["key"]), norm(n["value"]))
        if sig in seen:
            merges.append({"id": n["id"], "key": n["key"], "value": n["value"], "outcome": "merged_as_duplicate",
                           "into": seen[sig], "why": f"same key and value as {seen[sig]}"})
            continue
        seen[sig] = n["id"]
        active.append(n)
    return active, merges


def refresh_outcomes(run_dir):
    """Rewrite the outcome fields of an existing consolidated.json in the current shape (no teacher calls)."""
    path = run_dir / "night" / "consolidated.json"
    doc = json.loads(path.read_text())
    notes, _ = load_day(run_dir)
    _, merges = duplicate_merges(notes)
    doc.setdefault("overruled_merge_proposals", [])
    doc.update(outcome_fields(notes, doc["items"], merges, doc["overruled_merge_proposals"]))
    path.write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({"refreshed": str(path), "outcomes": len(doc["outcomes"])}))


if __name__ == "__main__":
    main()
