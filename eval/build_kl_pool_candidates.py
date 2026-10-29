#!/usr/bin/env python3
"""Build the frozen candidate questions for the soft-anchor pools v1 (lead 30 Oct 08:03-08:05; research round 2 #1).

Three fixed pools; base Glimmer's own replies to them (night/kl_pool_rows.py) become soft-anchor rows trained with a
top-20 KL term toward plain Glimmer, never with cross-entropy:
  obscure - SimpleQA test questions (CSV pinned by the SimpleQA gate's manifest) outside the gate slice (200), the
            SimpleQA anchor prompts (300) and the IDK pool (600); kept later only where base declines.
  known   - NQ-open TRAIN questions (pinned revision; the known slices come from validation), short gold; kept later
            only where base answers right by gold match.
  people  - "Who is <name>?" / "Where is <place>?" from person- or place-like gold answers of NQ-open train
            "who" / "where" questions; any base reply in the user channel is kept.
Every candidate is checked disjoint from: AA-Omniscience public (600), known slices v1 and v2, the SimpleQA gate and
general slices, the public anchor pool v1, forget slices v1/v2 (no shared 3-word run with their questions or answers,
ids aside), and the worlds' person names; the known slice's health and time-wording filters apply. Picks follow
sha256('kl-pool-v1:<part>:<key>') order. Built once.
Run: uv run --with pyarrow python eval/build_kl_pool_candidates.py --nq-train <parquet> --simpleqa <csv> --aa <csv>
"""

import argparse
import ast
import csv
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "eval" / "data"
OUT = DATA / "kl_pool_candidates_v1.jsonl"
NQ_REVISION = "5dd9790a83002ad084ddeb7c420dc716852c6f28"
NQ_TRAIN_SHA256 = "25d3a544324f900b31ebc05a3a5686bdd5b9b42738133500675c3f20eee7d83a"
SIZES = {"obscure": 200, "known": 150, "people": 100}  # draws; the kept pools are ~100 each after base's replies
HEALTH = re.compile(r"\b(health|diet|fitness|mood|medical|disease|cancer|drug|calorie|weight loss|vitamin|symptom|virus|"
                    r"hospital|doctor|depression|anxiety|personality|exercise|nutrition|obesity|blood|surgery|mental|"
                    r"suicide|death|died|killed|murder)\b", re.I)
CHANGING = re.compile(r"\b(most|owner|who are|record|retirement|ranked|champion|leader|members|present|president|prime minister|"
                      r"speaker|cast of|bands in|current|latest|now|today|this year|recent|new|next|upcoming|last time|"
                      r"20(1[5-9]|2\d))\b", re.I)
PERSON = re.compile(r"\b([A-Z][a-z]+) ([A-Z][a-z]+)\b")
WORD = re.compile(r"[a-z0-9]+")


def words(text):
    return WORD.findall((text or "").lower())


def runs(text, n=3):
    w = words(text)
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def order_key(part, key):
    return hashlib.sha256(f"kl-pool-v1:{part}:{key}".encode()).hexdigest()


def world_names():
    names = set()
    for w in sorted((ROOT / "worlds").iterdir()):
        if not (w / "world.json").exists():
            continue
        doc = json.loads((w / "world.json").read_text())
        texts = [doc["persona"]["name"]] + [f["value"] for f in doc["facts"]]
        nt = DATA / "never_told" / f"{w.name}.jsonl"
        texts += [p["question"] for p in jsonl(nt)] if nt.exists() else []
        for t in texts:
            for a, b in PERSON.findall(t):
                names |= {a.lower(), b.lower()}
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nq-train", required=True)
    ap.add_argument("--simpleqa", required=True, help="simple_qa_test_set.csv (the gate manifest's pinned file)")
    ap.add_argument("--aa", required=True, help="AA-Omniscience_dataset_public.csv (eval/data/aa_omniscience/MANIFEST.json)")
    args = ap.parse_args()
    if OUT.exists():
        raise SystemExit(f"{OUT} exists; the candidates are built once")
    import pyarrow.parquet as pq

    if hashlib.sha256(Path(args.nq_train).read_bytes()).hexdigest() != NQ_TRAIN_SHA256:
        raise SystemExit("the NQ-open train parquet is not the pinned file")
    gate_m = json.loads((DATA / "simpleqa" / "MANIFEST.json").read_text())
    sqa_raw = Path(args.simpleqa).read_bytes()
    if hashlib.sha256(sqa_raw).hexdigest() != gate_m["csv_sha256"]:
        raise SystemExit("the SimpleQA csv is not the gate's pinned file")
    aa_m = json.loads((DATA / "aa_omniscience" / "MANIFEST.json").read_text())
    if hashlib.sha256(Path(args.aa).read_bytes()).hexdigest() != aa_m["csv_sha256"]:
        raise SystemExit("the AA-Omniscience csv is not the pinned file")

    # what every candidate must stay clear of
    held = []
    held += [(r["question"], r.get("answer", "")) for r in jsonl(DATA / "simpleqa" / "gate_slice_v1.jsonl")]
    held += [(r["question"], str(r.get("gold", ""))) for r in jsonl(DATA / "general_slice_v1.jsonl")]
    for name in ("known_slice_v1", "known_slice_v2", "public_anchor_pool_v1"):
        held += [(r["question"], " ".join(r.get("gold") or [])) for r in jsonl(DATA / f"{name}.jsonl")]
    with open(args.aa, newline="") as f:
        held += [(r.get("question", ""), r.get("answer", "")) for r in csv.DictReader(f)]
    held_runs = set().union(*(runs(f"{q} {a}") for q, a in held))
    forget_runs = set().union(*(runs(r["question"], 5) for n in ("forget_slice_v1", "forget_slice_v2") for r in jsonl(DATA / f"{n}.jsonl")))
    banned = world_names()
    sqa_used = {r["id"] for r in jsonl(DATA / "simpleqa" / "gate_slice_v1.jsonl")}
    anchor_prompts = ROOT / "night" / "data" / "simpleqa_anchor_prompts_v1.jsonl"
    if anchor_prompts.exists():
        sqa_used |= {r["id"] for r in jsonl(anchor_prompts)}
    pool600 = Path("/Users/olety/Desktop/code/muse-glimmer-afterglow/runs/_idk/base-sqa-pool600-v1/answers.jsonl")
    if pool600.exists():
        sqa_used |= {r["id"] for r in jsonl(pool600)}

    def clean(q, a):
        t = f"{q} {a}"
        return not (HEALTH.search(t) or CHANGING.search(q) or runs(t) & held_runs or runs(q, 5) & forget_runs
                    or set(words(t)) & banned)

    items = []
    # obscure: SimpleQA rows, ids as the gate builds them (sqa<row:04d>)
    sqa = list(csv.DictReader(sqa_raw.decode().splitlines()))
    cands = []
    for i, r in enumerate(sqa):
        rid = f"sqa{i:04d}"
        if rid in sqa_used or not clean(r["problem"], r["answer"]):
            continue
        topic = ast.literal_eval(r["metadata"]).get("topic") if r.get("metadata") else None
        cands.append({"id": f"KO-{rid}", "part": "obscure", "question": r["problem"], "gold": [r["answer"]], "source": rid, "topic": topic})
    cands.sort(key=lambda c: order_key("obscure", c["source"]))
    items += cands[:SIZES["obscure"]]
    # known + people: NQ-open train
    nq = pq.read_table(args.nq_train).to_pylist()
    known, people, seen = [], [], set()
    for i in sorted(range(len(nq)), key=lambda i: order_key("nq", i)):
        q, gold = nq[i]["question"], nq[i]["answer"]
        a = gold[0] if gold else ""
        if not a or len(a.split()) > 4 or not clean(q, " ".join(gold)):
            continue
        if len(known) < SIZES["known"]:
            known.append({"id": f"KK-{i:05d}", "part": "known", "question": q[0].upper() + q[1:] + "?", "gold": gold, "source": f"nq-train:{i}"})
        lead = q.split()[0]
        aw = a.split()
        if lead in ("who", "where") and 1 <= len(aw) <= 3 and all(x[:1].isupper() and x.replace(".", "").isalpha() for x in aw) \
                and (lead == "where" or len(aw) >= 2) and len(people) < SIZES["people"]:
            pq_ = f"{'Who' if lead == 'who' else 'Where'} is {a}?"
            if pq_.lower() not in seen and clean(pq_, ""):
                seen.add(pq_.lower())
                people.append({"id": f"KP-{i:05d}", "part": "people", "question": pq_, "source": f"nq-train:{i}"})
        if len(known) >= SIZES["known"] and len(people) >= SIZES["people"]:
            break
    items += known + people
    with open(OUT, "w") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    manifest = {"parts": {p: sum(1 for x in items if x["part"] == p) for p in SIZES},
                "sources": {"obscure": {"file": "simple_qa_test_set.csv", "sha256": gate_m["csv_sha256"],
                                        "excluded": "gate slice 200, SimpleQA anchor prompts 300, IDK pool 600"},
                            "known+people": {"dataset": "google-research-datasets/nq_open", "split": "train", "revision": NQ_REVISION,
                                             "parquet_sha256": NQ_TRAIN_SHA256, "license": "cc-by-sa-3.0"}},
                "disjoint_from": ["AA-Omniscience public 600", "known_slice_v1", "known_slice_v2", "SimpleQA gate 200", "general slice",
                                  "public_anchor_pool_v1", "forget_slice_v1", "forget_slice_v2", "world person names"],
                "rule": "no shared 3-word run with any held question or answer; no 5-word run with the forget slices; health and "
                        "time-wording filters of the known slice",
                "file": str(OUT.relative_to(ROOT)), "sha256": hashlib.sha256(OUT.read_bytes()).hexdigest()}
    OUT.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({k: manifest[k] for k in ("parts", "sha256")}))


if __name__ == "__main__":
    main()
