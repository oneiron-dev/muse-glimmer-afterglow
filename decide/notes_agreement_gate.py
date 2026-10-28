#!/usr/bin/env python3
"""Arm D5: the notes agreement gate, scored offline from R2's saved answers (CPU only, no model calls).

Per user question:
  1. the weights answer = arm B's saved answer (adapter, notes off);
  2. the matching notes snippet = one note from the night's consolidated notes;
  3. agree (the weights answer holds the note's value) -> answer from the weights;
     mismatch -> answer from the notes; no matching note -> ask the user.

Two ways to compare (agrees()): strict (the value as a token run) and terms (numbers exact, half the value's terms).
Two ways to find the matching note:
  app    - the day agent's own recall (app/afterglow/notes.py LocalJsonNotes.recall, top 1) on the question alone.
           This is the serving path; no hit -> ask.
  oracle - the note that holds the gold fact, found by code (a note whose value passes the fact's accept terms,
           ties broken by key-term overlap). The ceiling for this gate with perfect retrieval; no such note -> ask.
           A never-told question has no such note, so the oracle always asks there.

Answers looked up in the notes are scored two ways: by arm D's saved answer (adapter + notes in the prompt; the
same scoring as B2, eval/confidence_gate.py) and by the snippet itself (the note's full value against the gold).

Metrics per world and pooled:
  routing precision = of the answers the gate overrides (notes or ask), the share whose weights answer was wrong
  routing recall    = of the wrong weights answers, the share the gate overrides
  3-way             = right from weights | looked up (right, wrong) | wrong from weights, plus asked
  omniscience score = (right - wrong) / n, an ask is neutral on forward probes and right on never-told probes
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "night"))
sys.path.insert(0, str(ROOT / "app"))
import scoring  # noqa: E402
from afterglow.notes import LocalJsonNotes, terms  # noqa: E402
from omniscience import outcome as omni_outcome  # noqa: E402
from paths import runs_root  # noqa: E402

WORLDS = ["w1-ines-varga", "w2-solo-developer", "w3-shop-owner", "w4-grad-student"]


def load_results(path):
    return {r["id"]: r for r in json.loads(Path(path).read_text())["results"]}


def note_store(items):
    store = LocalJsonNotes.__new__(LocalJsonNotes)
    store.notes = [{"id": i["id"], "key": i["key"], "value": i.get("full_value") or i["value"], "status": "active",
                    "accept": i.get("accept") or [i["value"]]} for i in items]
    return store


def oracle_note(store, fact):
    if fact is None:
        return None
    accept = scoring.usable_accept(fact["accept"], fact["value"])
    holders = [n for n in store.notes if scoring.score_fact(n["value"], accept)]
    if not holders:
        return None
    fk = terms(fact["key"])
    return max(holders, key=lambda n: (len(fk & terms(n["key"])), -store.notes.index(n)))


def app_note(store, question):
    hits = store.recall(question, k=1)
    return hits[0] if hits else None


def value_terms(text):
    return terms(scoring.normalize(text))


def agrees(answer, note, compare):
    """strict: the answer holds the note's value as a token run. terms: every number in the note's value is in the
    answer, and at least half of the value's content terms are (a fixed prior, not fit on these rows)."""
    if scoring.score_fact(answer, note["accept"]) or scoring.score_fact(answer, [note["value"]]):
        return True
    if compare == "strict":
        return False
    vt, at = value_terms(note["value"]), value_terms(answer)
    if not vt or any(t.isdigit() and t not in at for t in vt):
        return False
    return len(vt & at) / len(vt) >= 0.5


def route(answer, note, compare):
    if note is None:
        return "ask"
    return "weights" if agrees(answer, note, compare) else "notes"


def score_rows(rows):
    """rows: dicts with kind, route, b_right, d_right, snippet_right. Returns the gate's metrics."""
    out = {"n": len(rows)}
    over = [r for r in rows if r["route"] != "weights"]
    b_wrong = [r for r in rows if not r["b_right"]]
    caught = [r for r in over if not r["b_right"]]
    out["overridden"] = len(over)
    out["b_wrong"] = len(b_wrong)
    out["routing_precision"] = round(len(caught) / len(over), 4) if over else None
    out["routing_recall"] = round(len(caught) / len(b_wrong), 4) if b_wrong else None
    w = [r for r in rows if r["route"] == "weights"]
    nl = [r for r in rows if r["route"] == "notes"]
    asked = [r for r in rows if r["route"] == "ask"]
    for by in ("d", "snippet"):
        right = sum(r["b_right"] for r in w) + sum(r[f"{by}_right"] for r in nl) + sum(r["ask_right"] for r in asked)
        wrong = (sum(not r["b_right"] and not r["b_abstain"] for r in w)
                 + sum(not r[f"{by}_right"] and not r[f"{by}_abstain"] for r in nl))
        out[f"lookup_scored_by_{by}"] = {
            "right_from_weights": sum(r["b_right"] for r in w), "wrong_from_weights": sum(not r["b_right"] for r in w),
            "looked_up": len(nl), "looked_up_right": sum(r[f"{by}_right"] for r in nl), "asked": len(asked),
            "right": right, "wrong": wrong, "score": round((right - wrong) / len(rows), 4) if rows else None}
    out["weights_precision"] = round(sum(r["b_right"] for r in w) / len(w), 4) if w else None
    out["weights_share"] = round(len(w) / len(rows), 4) if rows else None
    return out


def world_rows(world, runs, mode, compare):
    night = runs / world / "1" / "night"
    store = note_store(json.loads((night / "consolidated.json").read_text())["items"])
    facts = {f["id"]: f for f in json.loads((ROOT / "worlds" / world / "world.json").read_text())["facts"]}
    probes = [json.loads(line) for line in (ROOT / "worlds" / world / "heldout" / "probes.jsonl").read_text().splitlines()
              if line.strip()]
    probes = [p for p in probes if p["kind"] == "forward"]
    b, d = load_results(night / "eval" / "score-night-B.json"), load_results(night / "eval" / "score-night-D.json")
    nt_dir = night / "eval" / "never-told"
    nt = [json.loads(line) for line in (ROOT / "eval" / "data" / "never_told" / f"{world}.jsonl").read_text().splitlines()
          if line.strip()]
    nt_b, nt_d = load_results(nt_dir / "score-nt-B.json"), load_results(nt_dir / "score-nt-D.json")
    rows = []
    for p in probes:
        if p["id"] not in b:  # a probe for a fact that only becomes true on a later day
            continue
        fact = facts[p["fact_id"]]
        note = app_note(store, p["question"]) if mode == "app" else oracle_note(store, fact)
        br, dr = b[p["id"]], d[p["id"]]
        accept = scoring.usable_accept(fact["accept"], fact["value"])
        snippet_right = bool(note) and scoring.score_fact(note["value"], accept)
        rows.append({"world": world, "id": p["id"], "kind": "forward", "question": p["question"], "route": route(br["answer"], note, compare),
                     "note": note and {"id": note["id"], "key": note["key"], "value": note["value"]},
                     "b_answer": br["answer"], "b_right": br["pass"], "b_abstain": omni_outcome(br) == "abstain",
                     "d_right": dr["pass"], "d_abstain": omni_outcome(dr) == "abstain",
                     "snippet_right": snippet_right, "snippet_abstain": False, "ask_right": False})
    for p in nt:
        note = app_note(store, p["question"]) if mode == "app" else None
        br, dr = nt_b[p["id"]], nt_d[p["id"]]
        rows.append({"world": world, "id": p["id"], "kind": "never_told", "question": p["question"], "route": route(br["answer"], note, compare),
                     "note": note and {"id": note["id"], "key": note["key"], "value": note["value"]},
                     "b_answer": br["answer"], "b_right": br["pass"], "b_abstain": omni_outcome(br) == "abstain",
                     "d_right": dr["pass"], "d_abstain": False,
                     "snippet_right": False, "snippet_abstain": False, "ask_right": True})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worlds", nargs="+", default=WORLDS)
    ap.add_argument("--runs")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    runs = runs_root(args.runs)
    report = {"arm": "D5 notes agreement gate", "source": "R2 day-1 nights, saved answers (runs/<w>/1/night/eval/)",
              "notes": "consolidated.json items (full_value), app recall = app/afterglow/notes.py LocalJsonNotes.recall",
              "probes": "forward held-out probes + frozen never-told probes (eval/data/never_told/)", "modes": {}}
    for mode, compare in (("app", "strict"), ("app", "terms"), ("oracle", "strict"), ("oracle", "terms")):
        rows = [r for w in args.worlds for r in world_rows(w, runs, mode, compare)]
        m = {}
        for name, sel in (("forward", lambda r: r["kind"] == "forward"),
                          ("forward_w2_w4", lambda r: r["kind"] == "forward" and not r["world"].startswith("w1")),
                          ("never_told", lambda r: r["kind"] == "never_told"),
                          ("all", lambda r: True)):
            m[name] = score_rows([r for r in rows if sel(r)])
        m["per_world_forward"] = {w: score_rows([r for r in rows if r["world"] == w and r["kind"] == "forward"])
                                  for w in args.worlds}
        report["modes"][f"{mode}/{compare}"] = m
        rows_path = Path(args.out).with_name(Path(args.out).stem + f"-rows-{mode}-{compare}.jsonl")
        rows_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
    for mode, m in report["modes"].items():
        for name in ("forward", "forward_w2_w4", "never_told", "all"):
            s = m[name]
            d = s["lookup_scored_by_d"]
            print(f"{mode:13s} {name:14s} n {s['n']:3d}  precision {s['routing_precision']}  recall {s['routing_recall']}  "
                  f"weights {d['right_from_weights']}/{d['right_from_weights'] + d['wrong_from_weights']}  "
                  f"looked_up {d['looked_up']} (right {d['looked_up_right']}, snippet {s['lookup_scored_by_snippet']['looked_up_right']})  "
                  f"asked {d['asked']}  right {d['right']} wrong {d['wrong']} score {d['score']}")


if __name__ == "__main__":
    main()
