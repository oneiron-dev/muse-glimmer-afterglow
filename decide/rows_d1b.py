#!/usr/bin/env python3
"""D1b training rows: the D1 rows with the three gaps D1's readout showed, fixed (lead, 29 Oct 07:02 and 07:26).

  (a) supersede: "replaces" pairs whose earlier side is Glimmer's own restatement of the old note (the night's
      "statement" rows), so a correction under a reworded key is a positive; negatives sampled to 2 per positive.
  (b) route: the ask share cut from 36% to ~20%, and the route rows given twice (two epochs on route only).
  (c) duplicate: "different facts" pairs that share a value (two world facts holding one name, room, time or number),
      the case D1 got wrong on real day-3 notes.

Every label is computed by code from world.json. w1-w3 only; corrections that arrive on day 3 are left out, so the
day-3 check sets stay clean. Leak checks: every held-out and never-told question (night/leak_check.py), and every
pair of the day-2 and day-3 check sets (rows.pair_leaks). Writes decide/data/d1b/.
"""

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "decide"))
import rows as R  # noqa: E402

OUT = R.DATA / "d1b"
GENERIC = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "mondays", "tuesdays",
           "wednesdays", "thursdays", "fridays", "saturdays", "sundays", "hall", "room", "desk", "unit", "floor", "team",
           "the", "with", "every", "next", "week", "euros"}


def value_tokens(value):
    """The distinctive parts of a value: a clock time, a code (QT-207), a number of 3+ digits that is not a year, or a
    capitalized word of 4+ letters that is not a weekday or a generic place word."""
    out = set(re.findall(r"\b\d{1,2}:\d{2}\b", value)) | set(re.findall(r"\b[A-Z]{1,4}-?\d+[A-Z]?\b", value))
    out |= {n for n in re.findall(r"\b\d{3,}\b", value) if not re.fullmatch(r"(19|20)\d\d", n)}
    out |= {w.lower() for w in re.findall(r"\b[A-Z][a-z]{3,}\b", value) if w.lower() not in GENERIC}
    return out


def day2_pairs(W):
    """(old fact, new fact) for the corrections that arrive on day 2 (day-3 corrections stay out of training)."""
    facts = W["facts"]
    return [(f, facts[f["superseded_by"]]) for f in facts.values()
            if f.get("superseded_by") and facts[f["superseded_by"]].get("valid_from", 1) == 2]


def supersede_restatement_rows(W, rng, per_pair=2):
    """Earlier side = a restatement of the night's note holding the old value; new side = the new fact as key: value
    and as the user's day-2 sentence -> 1. The same new sides against restatements of other notes whose keys share a
    word with the new fact -> 2."""
    by_id = {it["id"]: it for it in W["items"]}
    statements = {}
    for r in W["train"]:
        if r.get("form") == "statement" and r.get("item_id") in by_id:
            statements.setdefault(r["item_id"], []).append(r["messages"][-1]["content"].strip())
    said = {f: t["content"] for t in W["day2"] for f in t.get("facts", [])}
    pos, neg = [], []
    for old, new in day2_pairs(W):
        holders = [i for i, it in by_id.items() if R.holds(it.get("full_value") or it["value"], old)]
        new_sides = [("key_value", R.fact_line(new)), ("user_sentence", said.get(new["id"]))]
        for item_id in holders:
            texts = statements.get(item_id, [])[:per_pair]
            for k, text in enumerate(texts):
                for form, side in new_sides:
                    if side:
                        pos.append(R.pair_row("supersede", W["world"], "train", f"restatement_{form}",
                                              f"sup:{W['world']}:{item_id}-{new['id']}:restate{k}:{form}", text, side, "1",
                                              {"fact_ids": [old["id"], new["id"]]}))
        nk = set(re.findall(r"[a-z]+", new["key"].lower()))
        others = [i for i, it in by_id.items() if i not in holders and nk & set(re.findall(r"[a-z]+", it["key"].lower()))]
        for item_id in others:
            for k, text in enumerate(statements.get(item_id, [])[:1]):
                for form, side in new_sides:
                    if side:
                        neg.append(R.pair_row("supersede", W["world"], "train", f"restatement_other_{form}",
                                              f"sup:{W['world']}:{item_id}-{new['id']}:restate{k}:{form}:other", text, side, "2",
                                              {"fact_ids": [None, new["id"]]}))
    return pos, neg


def duplicate_shared_value_rows(W, rng, limit):
    """Different facts that share a name, code, clock time or number (in the key or the value) -> 2. Sides are the
    night's own notes ("key: value", the agent's keys) and world facts true by day 2 ("key: value"); each side maps to
    exactly one world fact by code, and the two facts differ. This is the case D1 got wrong on real day-3 notes."""
    facts = W["facts"]
    sides = []
    for it in W["items"]:
        held = [f["id"] for f in facts.values() if f.get("valid_from", 1) <= 1 and R.holds(it.get("full_value") or it["value"], f)]
        if len(held) == 1:
            sides.append((R.note_line(it), held[0]))
    sides += [(R.fact_line(f), f["id"]) for f in facts.values() if f.get("valid_from", 1) <= 2]
    tokens = [value_tokens(text.replace(":", " ")) for text, _ in sides]
    pairs = []
    for i in range(len(sides)):
        for j in range(i + 1, len(sides)):
            fi, fj = sides[i][1], sides[j][1]
            if fi != fj and tokens[i] & tokens[j] and facts[fi].get("superseded_by") != fj and facts[fj].get("superseded_by") != fi:
                pairs.append((sides[i], sides[j]))
    rng.shuffle(pairs)
    rows = []
    for (a, fa), (b, fb) in pairs[:limit]:
        if rng.random() < 0.5:
            a, b, fa, fb = b, a, fb, fa
        rows.append(R.pair_row("duplicate", W["world"], "train", "shared_value_different_fact",
                               f"dup:{W['world']}:{fa}-{fb}:shared:{hashlib.sha256((a + b).encode()).hexdigest()[:8]}",
                               a, b, "2", {"fact_ids": [fa, fb]}))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs")
    ap.add_argument("--route-rows", type=int, default=300, help="unique route rows; each is given twice")
    ap.add_argument("--ask-share", type=float, default=0.2)
    ap.add_argument("--cross-world-share", type=float, default=0.5, help="share of the asks that are never-told style")
    ap.add_argument("--shared-value-per-world", type=int, default=40)
    args = ap.parse_args()
    runs = R.runs_root(args.runs)
    rng = random.Random(R.SEED + 1)
    worlds = {w: R.load_world(w, runs) for w in R.WORLDS}
    OUT.mkdir(parents=True, exist_ok=True)
    # (b) route: from D1's route rows (already leak-checked), fewer asks, each row twice
    route = R.read_jsonl(R.DATA / "route-train.jsonl")
    cross = [r for r in route if r["source"] == "train_cross_world_ask"]
    asks = [r for r in route if r["source"] == "train_question_ask"]
    notes = [r for r in route if r["label"] != "0"]
    n_ask = round(args.route_rows * args.ask_share)
    n_cross = round(n_ask * args.cross_world_share)
    route_keep = rng.sample(cross, n_cross) + rng.sample(asks, n_ask - n_cross) + rng.sample(notes, args.route_rows - n_ask)
    # (a) supersede: D1's supersede rows + restatement positives, negatives sampled to 2 per positive
    sup = R.read_jsonl(R.DATA / "supersede-train.jsonl")
    extra_pos, extra_neg = [], []
    for w in R.TRAIN_WORLDS:
        p, n = supersede_restatement_rows(worlds[w], rng)
        extra_pos += p
        extra_neg += n
    sup_pos = [r for r in sup if r["label"] == "1"] + extra_pos
    sup_neg = [r for r in sup if r["label"] == "2"] + extra_neg
    rng.shuffle(sup_neg)
    sup_keep = sup_pos + sup_neg[: 2 * len(sup_pos)]
    # (c) duplicate: D1's duplicate rows + shared-value different-fact negatives
    dup = R.read_jsonl(R.DATA / "duplicate-train.jsonl")
    dup_keep = dup + [r for w in R.TRAIN_WORLDS for r in duplicate_shared_value_rows(worlds[w], rng, args.shared_value_per_world)]
    built = route_keep + sup_keep + dup_keep
    # leak checks: questions, then whole pairs of every check set
    probes = [p for W in worlds.values() for p in W["probes"] + W["never_told"]]
    msgs = [{"id": r["id"], "messages": [{"content": r.get("question") or r["prompt"]}]} for r in built]
    hits, overlap = R.leak_check(msgs, probes)
    checks = [r for path in sorted(R.DATA.glob("*-check.jsonl")) for r in R.read_jsonl(path)]
    pair_hits = R.pair_leaks(built, checks)
    drop = {h["train_row"] for h in hits} | {h["train_row"] for h in pair_hits}
    built = [r for r in built if r["id"] not in drop]
    after, _ = R.leak_check([m for m in msgs if m["id"] not in drop], probes)
    pair_after = R.pair_leaks(built, checks)
    leak = {"rows": len(built), "question_hits_before": len(hits), "pair_check_rows": len(checks),
            "pair_hits_before": len(pair_hits), "dropped": sorted(drop), "question_hits": len(after),
            "pair_hits": len(pair_after), "probes_sharing_a_5gram": overlap, "passed": not after and not pair_after}
    (OUT / "leak.json").write_text(json.dumps(leak, indent=1) + "\n")
    route_final = [r for r in built if r["kind"] == "route"]
    rows = [r for r in built if r["kind"] != "route"] + route_final + [{**r, "id": r["id"] + ":epoch2"} for r in route_final]
    rng.shuffle(rows)
    with open(OUT / "train-decision.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps({"id": r["id"], "kind": r["kind"], "messages": [{"role": "user", "content": r["prompt"]},
                                                                             {"role": "assistant", "content": r["label"]}]},
                               ensure_ascii=False) + "\n")
    sha = hashlib.sha256((OUT / "train-decision.jsonl").read_bytes()).hexdigest()
    count = {}
    for r in rows:
        key = f"{r['kind']}:{r['label'] if r['kind'] != 'route' else ('ask' if r['label'] == '0' else 'note')}"
        count[key] = count.get(key, 0) + 1
    print(json.dumps({"rows": len(rows), "by_kind_label": count, "supersede_restatement_pos": len(extra_pos),
                      "leak": {k: leak[k] for k in ("question_hits_before", "pair_hits_before", "question_hits", "pair_hits",
                                                    "passed")}, "sha256": sha}, indent=1))
    if not leak["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
