#!/usr/bin/env python3
"""Decision rows for Glimmer's one-pass decisions. Every gold label is computed by code from the world files.

Kinds (decide/DESIGN.md):
  route      - which note answers the user's question? Options: every note of the night (numbered 1..N, "key: value")
               and 0 = none of them (ask the user). Gold: the note that holds the fact (code), or 0.
  duplicate  - are two notes the same fact? 1 = same fact (keep one), 2 = different facts (keep both).
  supersede  - does the new note replace the earlier one? 1 = replaces it (a correction), 2 = both stay true.

Splits: train = w1-w3 rows built from the night's training questions and notes; calibration = w1-w3 held-out probes
and never-told probes (never trained on; thresholds are fit here); test = w4 (a world the decision adapter never sees),
frozen by hash (decide/data/FROZEN.json) before any training row exists.

Writes decide/data/<kind>-<split>.jsonl. Each row: id, kind, world, split, source, prompt (the user message),
options {label: description}, label, accept (labels that are also right), and the code fields behind the label.
"""

import argparse
import hashlib
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
from leak_check import check as leak_check  # noqa: E402
from paths import runs_root  # noqa: E402
from candidates import Bm25  # noqa: E402
from prompts import (DUP_OPTIONS, SUP_OPTIONS, note_line, pair_prompt, route_options,  # noqa: E402
                     route_prompt)

WORLDS = ["w1-ines-varga", "w2-solo-developer", "w3-shop-owner", "w4-grad-student"]
TRAIN_WORLDS, TEST_WORLD = WORLDS[:3], WORLDS[3]
SEED = 20261014
DATA = ROOT / "decide" / "data"
QUESTION_FORMS = ("qa_forward", "in_context", "qa_reverse")

def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def holds(text, fact):
    return scoring.score_fact(text, scoring.usable_accept(fact["accept"], fact["value"]))


def holders(items, fact):
    """Indexes of the notes whose value holds the fact (code); the best key match first."""
    found = [i for i, it in enumerate(items) if holds(it.get("full_value") or it["value"], fact)]
    fk = set(re.findall(r"[a-z0-9]+", fact["key"].lower()))
    return sorted(found, key=lambda i: (-len(fk & set(re.findall(r"[a-z0-9]+", items[i]["key"].lower()))), i))


def route_row(world, split, source, rid, first, items, question, gold_idx, extra):
    """gold_idx: indexes into items of the notes that answer (best first); [] = ask."""
    labels = [str(i + 1) for i in gold_idx]
    return {"id": rid, "kind": "route", "world": world, "split": split, "source": source, "question": question,
            "prompt": route_prompt(first, items, question), "note_ids": [it["id"] for it in items],
            "options": route_options(first, items),
            "label": labels[0] if labels else "0", "accept": labels or ["0"], **extra}


def pair_row(kind, world, split, source, rid, a, b, label, extra):
    return {"id": rid, "kind": kind, "world": world, "split": split, "source": source, "prompt": pair_prompt(kind, a, b),
            "options": DUP_OPTIONS if kind == "duplicate" else SUP_OPTIONS, "label": label, "accept": [label],
            "a": a, "b": b, **extra}


def load_world(world, runs):
    night = runs / world / "1" / "night"
    w = json.loads((ROOT / "worlds" / world / "world.json").read_text())
    return {"world": world, "first": w["persona"]["name"].split()[0], "facts": {f["id"]: f for f in w["facts"]},
            "items": json.loads((night / "consolidated.json").read_text())["items"],
            "train": read_jsonl(night / "train.jsonl"),
            "probes": [p for p in read_jsonl(ROOT / "worlds" / world / "heldout" / "probes.jsonl") if p["kind"] == "forward"],
            "never_told": read_jsonl(ROOT / "eval" / "data" / "never_told" / f"{world}.jsonl"),
            "b_ids": {r["id"] for r in json.loads((night / "eval" / "score-night-B.json").read_text())["results"]},
            "day2": read_jsonl(ROOT / "worlds" / world / "days" / "2" / "script.jsonl")
            if (ROOT / "worlds" / world / "days" / "2" / "script.jsonl").exists() else []}


def route_eval_rows(W, split):
    rows = []
    for p in W["probes"]:
        if p["id"] not in W["b_ids"]:  # a fact that only becomes true on a later day
            continue
        fact = W["facts"][p["fact_id"]]
        rows.append(route_row(W["world"], split, "heldout_forward", f"route:{W['world']}:{p['id']}", W["first"], W["items"],
                              p["question"], holders(W["items"], fact), {"probe_id": p["id"], "fact_id": p["fact_id"]}))
    for p in W["never_told"]:
        rows.append(route_row(W["world"], split, "never_told", f"route:{W['world']}:{p['id']}", W["first"], W["items"],
                              p["question"], [], {"probe_id": p["id"], "fact_id": None}))
    return rows


def route_train_rows(W, rng, ask_share):
    """Questions from the night's own training rows (already leak-checked against the held-out probes). The notes are
    shuffled per row so a label is never a fixed position. A share of rows drops every note that answers -> 0 (ask)."""
    by_id = {it["id"]: i for i, it in enumerate(W["items"])}
    rows = []
    for r in W["train"]:
        if r.get("form") not in QUESTION_FORMS or r.get("item_id") not in by_id:
            continue
        question = r["messages"][0]["content"].strip()
        answer = r["messages"][-1]["content"]
        gold = by_id[r["item_id"]]
        item = W["items"][gold]
        same_value = [i for i, it in enumerate(W["items"]) if i != gold
                      and scoring.score_fact(it.get("full_value") or it["value"], item.get("accept") or [item["value"]])]
        order = list(range(len(W["items"])))
        rng.shuffle(order)
        ask = rng.random() < ask_share
        if ask:
            order = [i for i in order if i != gold and i not in same_value]
        items = [W["items"][i] for i in order]
        gold_idx = [] if ask else [order.index(gold)] + [order.index(i) for i in same_value]
        rows.append(route_row(W["world"], "train", "train_question_ask" if ask else "train_question",
                              f"route:{W['world']}:{r['id']}", W["first"], items, question, gold_idx,
                              {"train_row": r["id"], "item_id": r["item_id"], "form": r["form"], "answer_text": answer}))
    return rows


def key_words(key):
    return set(re.findall(r"[a-z]+", key.lower())) - {"name", "the", "of", "my", "date", "day", "time"}


def route_cross_world_rows(W, others, rng, per_world):
    """Never-told style asks: a forward question from another training world about a key this world's notes do not
    have (no shared key word, no note holding its value) -> 0 (ask). Sources are w1-w3 only; w4 stays unseen."""
    mine = set().union(*(key_words(it["key"]) for it in W["items"]))
    pool = []
    for O in others:
        by_id = {it["id"]: it for it in O["items"]}
        for r in O["train"]:
            item = by_id.get(r.get("item_id"))
            if r.get("form") != "qa_forward" or item is None or key_words(item["key"]) & mine:
                continue
            accept = item.get("accept") or [item["value"]]
            if any(scoring.score_fact(it.get("full_value") or it["value"], accept) for it in W["items"]):
                continue
            pool.append((O["world"], r, item))
    rng.shuffle(pool)
    rows, seen = [], set()
    for world, r, item in pool:
        if item["id"] + world in seen:
            continue
        seen.add(item["id"] + world)
        order = list(range(len(W["items"])))
        rng.shuffle(order)
        rows.append(route_row(W["world"], "train", "train_cross_world_ask", f"route:{W['world']}:x:{world}:{r['id']}",
                              W["first"], [W["items"][i] for i in order], r["messages"][0]["content"].strip(), [],
                              {"train_row": r["id"], "from_world": world, "item_id": item["id"], "form": r["form"]}))
        if len(rows) >= per_world:
            break
    return rows


def fact_of_item(W, item):
    text = item.get("full_value") or item["value"]
    return next((f["id"] for f in W["facts"].values() if holds(text, f)), None)


def duplicate_rows(W, split, rng):
    """Same: a note and a Glimmer restatement of it (the night's 'statement' rows, same item). Different: two notes from
    the same user sentence, and two notes whose keys share a word. Labels from the item ids and the fact map."""
    rows, items = [], W["items"]
    by_id = {it["id"]: it for it in items}
    statements = [r for r in W["train"] if r.get("form") == "statement" and r.get("item_id") in by_id]
    rng.shuffle(statements)
    seen = set()
    for r in statements:
        if r["item_id"] in seen:
            continue
        seen.add(r["item_id"])
        a, b = note_line(by_id[r["item_id"]]), r["messages"][-1]["content"].strip()
        if rng.random() < 0.5:
            a, b = b, a
        rows.append(pair_row("duplicate", W["world"], split, "note_vs_restatement", f"dup:{W['world']}:{r['id']}", a, b, "1",
                             {"item_ids": [r["item_id"], r["item_id"]]}))
    n_same = len(rows)
    negatives = []
    for i, x in enumerate(items):
        for y in items[i + 1:]:
            fx, fy = fact_of_item(W, x), fact_of_item(W, y)
            if fx and fy and fx == fy:
                continue
            same_sentence = x.get("source_utterance") and x.get("source_utterance") == y.get("source_utterance")
            kx, ky = set(re.findall(r"[a-z]+", x["key"].lower())), set(re.findall(r"[a-z]+", y["key"].lower()))
            if same_sentence:
                negatives.append(("same_sentence", x, y))
            elif kx & ky:
                negatives.append(("shared_key_word", x, y))
    rng.shuffle(negatives)
    for source, x, y in negatives[: max(n_same, 1)]:
        a, b = (note_line(x), note_line(y)) if rng.random() < 0.5 else (note_line(y), note_line(x))
        rows.append(pair_row("duplicate", W["world"], split, source, f"dup:{W['world']}:{x['id']}-{y['id']}", a, b, "2",
                             {"item_ids": [x["id"], y["id"]]}))
    return rows


def fact_line(f):
    return f"{f['key']}: {f['value']}"


def supersede_rows(W, split, rng):
    """Replaces: a fact and the later fact that supersedes it (world.json superseded_by), the new note written as the
    user's day-2 sentence and as key: value. Both true: the new fact against other earlier facts that share a key word
    or neither supersedes, and earlier fact pairs that share a key word."""
    rows, facts = [], W["facts"]
    said = {f: t["content"] for t in W["day2"] for f in t.get("facts", [])}
    pairs = [(f, facts[f["superseded_by"]]) for f in facts.values() if f.get("superseded_by")]
    for old, new in pairs:
        for form, text in (("key_value", fact_line(new)), ("user_sentence", said.get(new["id"]))):
            if text:
                rows.append(pair_row("supersede", W["world"], split, f"replaces_{form}", f"sup:{W['world']}:{old['id']}-{new['id']}:{form}",
                                     fact_line(old), text, "1", {"fact_ids": [old["id"], new["id"]]}))
    n_pos = len(rows)
    day1 = [f for f in facts.values() if f.get("valid_from", 1) == 1]
    negatives = []
    for old, new in pairs:
        nk = set(re.findall(r"[a-z]+", new["key"].lower()))
        for f in day1:
            if f["id"] in (old["id"], new["id"]):
                continue
            if nk & set(re.findall(r"[a-z]+", f["key"].lower())):
                negatives.append(("new_vs_other_shared_word", f, new))
    for i, x in enumerate(day1):
        for y in day1[i + 1:]:
            if set(re.findall(r"[a-z]+", x["key"].lower())) & set(re.findall(r"[a-z]+", y["key"].lower())):
                negatives.append(("earlier_pair_shared_word", x, y))
    rng.shuffle(negatives)
    for source, x, y in negatives[: max(2 * n_pos, 4)]:
        text = said.get(y["id"]) if source.startswith("new") and rng.random() < 0.5 else fact_line(y)
        rows.append(pair_row("supersede", W["world"], split, source, f"sup:{W['world']}:{x['id']}-{y['id']}:{source}",
                             fact_line(x), text, "2", {"fact_ids": [x["id"], y["id"]]}))
    return rows


def supersede_candidate_rows(W, runs, k=8):
    """Remember-time rows: the earlier notes are the day agent's own day-1 notes (its paraphrased keys), the new note is
    a superseding fact (world key: value, and the user's day-2 sentence), and the pairs are exactly the BM25 top-k
    candidates the live decider checks (decide.decider.supersede_candidates). A candidate holding the old value ->
    1 (replaces), any other candidate -> 2 (both stay true)."""
    path = runs / W["world"] / "1" / "notes.json"
    if not path.exists():
        return []
    day1 = [n for n in json.loads(path.read_text())["notes"] if n.get("status", "active") == "active"]
    bm = Bm25([{**n, "statement": ""} for n in day1])
    said = {f: t["content"] for t in W["day2"] for f in t.get("facts", [])}
    rows = []
    for old in (f for f in W["facts"].values() if f.get("superseded_by")):
        new = W["facts"][old["superseded_by"]]
        for form, text in (("key_value", fact_line(new)), ("user_sentence", said.get(new["id"]))):
            if not text:
                continue
            for cand in bm.top(text, k):
                label = "1" if holds(cand["value"], old) else "2"
                rows.append(pair_row("supersede", W["world"], "train", f"candidate_{form}",
                                     f"sup:{W['world']}:{cand['id']}-{new['id']}:cand:{form}", note_line(cand), text, label,
                                     {"fact_ids": [old["id"] if label == "1" else None, new["id"]], "note_id": cand["id"]}))
    return rows


def norm_line(text):
    return " ".join((text or "").lower().split())


def pair_leaks(train_rows, check_rows):
    """Pair-level leak check against the day-2 correction check set: a training pair row is a hit when it holds a check
    pair whole, i.e. the check's earlier note is one side of the row and the check's new note is the other side, or the
    other side states the same superseding fact (same new fact id)."""
    hits = []
    for t in train_rows:
        if "a" not in t:
            continue
        sides = {norm_line(t["a"]), norm_line(t["b"])}
        t_new = (t.get("fact_ids") or [None, None])[-1]
        for c in check_rows:
            if norm_line(c["a"]) not in sides:
                continue
            if norm_line(c["b"]) in sides or (t_new is not None and t_new == c["fact_ids"][-1]):
                hits.append({"check": c["id"], "train_row": t["id"]})
    return hits


def split_by_hash(rows, share, salt):
    """Hold out a calibration share of w1-w3 pair rows by a stable hash of the row id."""
    out = []
    for r in rows:
        h = int(hashlib.sha256((salt + r["id"]).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        out.append({**r, "split": "calibration" if h < share else r["split"]})
    return out


def write(rows, path):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["test", "train"], help="test: build and freeze w4 + calibration rows first; "
                                                              "train: build train rows (needs the frozen test)")
    ap.add_argument("--runs")
    ap.add_argument("--ask-share", type=float, default=0.2)
    ap.add_argument("--cross-world-asks", type=int, default=60, help="never-told style ask rows per training world")
    args = ap.parse_args()
    runs = runs_root(args.runs)
    DATA.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    worlds = {w: load_world(w, runs) for w in WORLDS}
    frozen_path = DATA / "FROZEN.json"
    if args.step == "test":
        if frozen_path.exists():
            raise SystemExit("the test rows are frozen; delete decide/data/FROZEN.json only on the lead's word")
        files = {}
        files["route-test.jsonl"] = write(route_eval_rows(worlds[TEST_WORLD], "test"), DATA / "route-test.jsonl")
        files["route-calibration.jsonl"] = write([r for w in TRAIN_WORLDS for r in route_eval_rows(worlds[w], "calibration")],
                                                 DATA / "route-calibration.jsonl")
        for kind, build in (("duplicate", duplicate_rows), ("supersede", supersede_rows)):
            files[f"{kind}-test.jsonl"] = write(build(worlds[TEST_WORLD], "test", random.Random(SEED)), DATA / f"{kind}-test.jsonl")
        frozen_path.write_text(json.dumps({"files": files, "seed": SEED, "rule": "labels from code; test = w4; built before "
                                           "any training row"}, indent=1) + "\n")
        print(json.dumps(files, indent=1))
        return
    frozen = json.loads(frozen_path.read_text())["files"]
    for name, sha in frozen.items():
        if hashlib.sha256((DATA / name).read_bytes()).hexdigest() != sha:
            raise SystemExit(f"{name} changed since it was frozen")
    report, built = {}, {}
    built["route-train.jsonl"] = [r for w in TRAIN_WORLDS for r in route_train_rows(worlds[w], rng, args.ask_share)]
    built["route-train.jsonl"] += [r for w in TRAIN_WORLDS for r in route_cross_world_rows(
        worlds[w], [worlds[o] for o in TRAIN_WORLDS if o != w], rng, args.cross_world_asks)]
    for kind, build in (("duplicate", duplicate_rows), ("supersede", supersede_rows)):
        rows = split_by_hash([r for w in TRAIN_WORLDS for r in build(worlds[w], "train", random.Random(SEED))], 0.2, kind)
        if kind == "supersede":
            rows += split_by_hash([r for w in TRAIN_WORLDS for r in supersede_candidate_rows(worlds[w], runs)], 0.2, kind)
        built[f"{kind}-train.jsonl"] = [r for r in rows if r["split"] == "train"]
        built[f"{kind}-calibration.jsonl"] = [r for r in rows if r["split"] == "calibration"]
    # leak check: no training prompt may hold a held-out probe or a never-told question (any world). Hit rows are
    # dropped (row ids only; nothing from the probes enters training) and the clean rows are checked again.
    probes = [p for W in worlds.values() for p in W["probes"] + W["never_told"]]

    def as_messages():
        return [{"id": r["id"], "messages": [{"content": r.get("question") or r["prompt"]}]}
                for name, rows in built.items() if name.endswith("-train.jsonl") for r in rows]
    hits, _ = leak_check(as_messages(), probes)
    check_path = DATA / "supersede-day2-check.jsonl"
    checks = read_jsonl(check_path) if check_path.exists() else []
    pair_hits = pair_leaks([r for name, rows in built.items() if name.endswith("-train.jsonl") for r in rows], checks)
    dropped = sorted({h["train_row"] for h in hits} | {h["train_row"] for h in pair_hits})
    built = {name: [r for r in rows if r["id"] not in dropped] for name, rows in built.items()}
    after, overlap = leak_check(as_messages(), probes)
    pair_after = pair_leaks([r for name, rows in built.items() if name.endswith("-train.jsonl") for r in rows], checks)
    for name, rows in built.items():
        report[name] = write(rows, DATA / name)
    leak = {"train_rows": len(as_messages()), "probes": len(probes), "hits_before_clean": len(hits),
            "pair_check_rows": len(checks), "pair_hits_before_clean": len(pair_hits),
            "pair_checks_hit_before_clean": len({h["check"] for h in pair_hits}), "dropped_rows": dropped,
            "examples": hits[:20], "pair_examples": pair_hits[:20], "hits": len(after), "pair_hits": len(pair_after),
            "probes_sharing_a_5gram": overlap, "passed": not after and not pair_after}
    hits = after + pair_after
    (DATA / "leak.json").write_text(json.dumps(leak, indent=1) + "\n")
    counts = {name: sum(1 for _ in open(DATA / name)) for name in report}
    print(json.dumps({"files": counts, "leak": {k: leak[k] for k in ("train_rows", "hits", "pair_hits_before_clean",
                                                                      "pair_checks_hit_before_clean", "pair_hits", "passed")}},
                     indent=1))
    if hits:
        sys.exit(1)


if __name__ == "__main__":
    main()
