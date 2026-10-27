#!/usr/bin/env python3
"""Grader audit: find failed probes whose answer may contain the right value after all (false fails).

For every forward probe that fails in the given arms, it checks the answer against the fact's accept
terms under looser readings than the scorer's:
  punct  - every non-alphanumeric character removed on both sides ("owner-manager" == "Owner Manager")
  words  - every content word of an accept term present in the answer, in any order
  value  - the fact's full value (not only its accept terms) present after punctuation removal
Each candidate is listed with the answer, the accept terms and the reading that matched, for a
person to judge. Writes --out JSON and prints a summary. Reads saved score files only (CPU).
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoring  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STOP = set("a an the of at in on to for and or by with is are was my our your his her their its".split())


def squash(text):
    return re.sub(r"[^a-z0-9]+", "", scoring.normalize(text))


def words(text):
    return [w for w in re.findall(r"[a-z0-9]+", scoring.normalize(text)) if w not in STOP]


def readings(answer, fact):
    a_sq, a_words = squash(answer), set(words(answer))
    hits = []
    for term in fact["accept"]:
        if squash(term) and squash(term) in a_sq:
            hits.append(("punct", term))
        tw = words(term)
        if tw and all(w in a_words for w in tw):
            hits.append(("words", term))
    if squash(fact["value"]) and squash(fact["value"]) in a_sq:
        hits.append(("value", fact["value"]))
    req = fact.get("require_all") or []
    req_ok = all(squash(r) in a_sq for r in req)
    return hits, req_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--night", nargs="+", required=True, help="world=path/to/night/eval dir with score-night-<arm>.json")
    ap.add_argument("--arms", default="B,C")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    report = {}
    for spec in args.night:
        world, ev = spec.split("=", 1)
        facts = {f["id"]: f for f in json.loads((ROOT / "worlds" / world / "world.json").read_text())["facts"]}
        rows = []
        for arm in args.arms.split(","):
            rec = json.loads((Path(ev) / f"score-night-{arm}.json").read_text())
            for r in rec["results"]:
                if r["kind"] != "forward" or r["pass"] or r["truncated"]:
                    continue
                f = facts[r["fact_id"]]
                hits, req_ok = readings(r["answer"], f)
                if hits:
                    rows.append({"arm": arm, "probe": r["id"], "fact_id": f["id"], "key": f["key"], "value": f["value"],
                                 "accept": f["accept"], "require_all": f.get("require_all"), "answer": r["answer"],
                                 "matched": hits, "require_all_ok": req_ok})
        report[world] = rows
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
    for world, rows in report.items():
        print(f"== {world}: {len(rows)} failed probes match under a looser reading")
        for x in rows:
            print(f"  {x['arm']} {x['probe']:7s} accept={x['accept']} req={x['require_all']} ok_req={x['require_all_ok']} "
                  f"via={sorted({m[0] for m in x['matched']})}\n      answer: {x['answer'][:140]!r}")


if __name__ == "__main__":
    main()
