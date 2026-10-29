#!/usr/bin/env python3
"""Probe hygiene for the never-told set: a judge (Muse Spark through the CPA sub, the grader of record's teacher
spark-sub) reads each never-told probe beside the person's own active notes (key: value) and flags the probes whose
question asks for something the user DID tell, under another name: a near-synonym of a real note key ("Who's my
courier?" beside "deliveries handler: Swiftspoke Deliveries Co."). A question about another named person is never the
user's own fact. Flagged probes are reported apart in the reads, never dropped.

Writes eval/data/never_told/near_synonym_flags_<version>.json: per world the notes file it judged against (day and
sha256), and per probe {flag, note_key, why}; and records the file's sha256 in eval/data/never_told/FLAGS-FROZEN.json (frozen
before any answer it is used on; the probes' own FROZEN.json is left as it is). The judge's key is read from the environment (CLIPROXY_KEY).

  flag_near_synonyms.py --notes-day 4 --version v1
"""

import argparse
import datetime as dt
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "night"))
from paths import runs_root  # noqa: E402

DATA = REPO / "eval" / "data" / "never_told"
WORLDS = ("w1-ines-varga", "w2-solo-developer", "w3-shop-owner", "w4-grad-student")
JST = dt.timezone(dt.timedelta(hours=9))

PROMPT = """A personal assistant keeps notes on what its user told it. Below are the user's saved notes (key: value), then
a question the user asks.

Decide whether the question asks for a fact that IS in the notes under another name: the same fact as one note,
worded differently (a near-synonym of the note's key, e.g. "courier" for "deliveries handler", "client standup time"
for "client meeting time"), so that answering from that note would be a right answer to what the user means.

It is NOT a match when:
- the question names another person and asks about that person's fact ("Anna's office room" is not the user's office
  room), even when the user has a note with the same key;
- the note is only on a related topic, or a different fact of the same kind (a different meeting, a different
  deadline, a different person's role).

Notes:
{notes}

Question: {question}

Reply with JSON only: {{"match": true or false, "note_key": "<the matching note's key, or null>", "why": "<one short sentence>"}}"""


def judge(item, grader, log):
    import teacher
    text, _, _ = teacher.complete(grader, [{"role": "user", "content": PROMPT.format(**item)}], max_tokens=3000,
                                  temperature=0.0, log_path=log, purpose=f"near-synonym {item['world']} {item['id']}")
    start, end = text.find("{"), text.rfind("}")
    try:
        out = json.loads(text[start:end + 1])
    except ValueError:
        out = {"match": None, "note_key": None, "why": f"unparsed: {text.strip()[:120]}"}
    return item["world"], item["id"], {"flag": bool(out.get("match")) if out.get("match") is not None else None,
                                       "note_key": out.get("note_key"), "why": out.get("why")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes-day", type=int, required=True, help="the day whose notes the probes are judged against")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--grader", default="spark-sub")
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument("--runs")
    ap.add_argument("--log", default=None, help="grader call log (default: beside the output, -calls.jsonl)")
    args = ap.parse_args()
    out = DATA / f"near_synonym_flags_{args.version}.json"
    if out.exists():
        raise SystemExit(f"{out.name} exists: a frozen flag list is never rewritten; use a new --version")
    root = runs_root(args.runs)
    items, sources = [], {}
    for w in WORLDS:
        nf = root / w / str(args.notes_day) / "notes.json"
        notes = [n for n in json.loads(nf.read_text())["notes"] if n.get("status") == "active"]
        sources[w] = {"notes": str(nf.relative_to(root.parent)) if root.parent in nf.parents else str(nf),
                      "notes_day": args.notes_day, "notes_sha256": hashlib.sha256(nf.read_bytes()).hexdigest(),
                      "active_notes": len(notes)}
        lines = "\n".join(f"- {n['key']}: {n['value']}" for n in notes)
        for p in map(json.loads, (DATA / f"{w}.jsonl").read_text().splitlines()):
            items.append({"world": w, "id": p["id"], "type": p["type"], "question": p["question"], "notes": lines})
    log = Path(args.log) if args.log else out.with_name(out.stem + "-calls.jsonl")
    flags = {w: {} for w in WORLDS}
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        for w, pid, rec in pool.map(lambda it: judge(it, args.grader, log), items):
            flags[w][pid] = rec
    types = {(it["world"], it["id"]): it["type"] for it in items}
    doc = {"version": args.version, "made_at": dt.datetime.now(JST).isoformat(timespec="seconds"), "judge": args.grader,
           "rule": "flag = the never-told question asks for a fact the user did tell, under another name (a near-synonym "
                   "of a real note key); a question about another named person is never flagged; flagged probes are "
                   "reported apart in the reads, never dropped",
           "sources": sources,
           "flags": {w: {pid: dict(rec, type=types[(w, pid)]) for pid, rec in sorted(flags[w].items())} for w in WORLDS},
           "counts": {w: sum(bool(r["flag"]) for r in flags[w].values()) for w in WORLDS},
           "unparsed": sum(r["flag"] is None for w in WORLDS for r in flags[w].values())}
    out.write_text(json.dumps(doc, indent=1) + "\n")
    ff = DATA / "FLAGS-FROZEN.json"
    frozen = json.loads(ff.read_text()) if ff.exists() else {"files": {}}
    frozen["files"][out.name] = {"sha256": hashlib.sha256(out.read_bytes()).hexdigest(), "frozen_at": doc["made_at"]}
    ff.write_text(json.dumps(frozen, indent=1) + "\n")
    print(json.dumps({"out": str(out), "counts": doc["counts"], "unparsed": doc["unparsed"]}))
    for w in WORLDS:
        for pid, r in doc["flags"][w].items():
            if r["flag"]:
                print(f"  {w[:2]} {pid} [{r['type']}] -> {r['note_key']}: {r['why']}")


if __name__ == "__main__":
    main()
