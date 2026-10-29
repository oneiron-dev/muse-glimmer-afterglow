#!/usr/bin/env python3
"""Build the public-anchor pool v1 (lead 30 Oct 01:35-01:38): general-knowledge questions for night rows in which the
base model answers or looks up a public question, set against the ask-back rows ("You haven't told me Jonah Kask's
... Can you tell me?"), which night 5 spread to world questions about public people ("I don't have any notes about
Elsie Tu. Can you tell me?").

Source: NQ-open validation (google-research-datasets/nq_open, CC BY-SA 3.0) at the known slice's pinned revision and
parquet hash. Three parts of 40, so the pool names public people and places:
  nq       - NQ questions that start with who / where, as asked (first letter capitalised, question mark)
  who_is   - "Who is <name>?" for a person-like gold answer (2-3 words) of an NQ "who" question
  where_is - "Where is <place>?" for a place-like gold answer (1-3 words) of an NQ "where" question
Rows are picked by sha256('public-anchor-pool-v1:<row>') ascending after the known slice's filters. Disjoint by
construction and checked: no NQ row of the known slice; no 3-word run shared with any SimpleQA gate question or answer
(the 200 general questions of the product read) or the general slice; no 5-word run shared with forget slices v1/v2;
no first or last name of a world persona, a world fact or a never-told probe. Built once, before any row is made.
Run: uv run --with pyarrow python eval/build_public_anchor_pool.py --parquet <nq_open validation parquet>
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "eval" / "data"
OUT = DATA / "public_anchor_pool_v1.jsonl"
REVISION = "5dd9790a83002ad084ddeb7c420dc716852c6f28"
PARQUET_SHA256 = "b074bed0bccb56fa1551a8ac1c9c51ce89bc11c7fbb6a9c713b2c33a98531e12"
PER_PART = 40
HEALTH = re.compile(r"\b(health|diet|fitness|mood|medical|disease|cancer|drug|calorie|weight loss|vitamin|symptom|virus|"
                    r"hospital|doctor|depression|anxiety|personality|exercise|nutrition|obesity|blood|surgery)\b")
CHANGING = re.compile(r"\b(most|owner|who are|record|retirement|ranked|champion|leader|members|present|president|prime minister|"
                      r"speaker|cast of|bands in|current|latest|now|today|this year|recent|new|next|upcoming|last time|"
                      r"20(1[5-9]|2\d))\b")
WORD = re.compile(r"[a-z0-9]+")


def words(text):
    return WORD.findall((text or "").lower())


def runs(text, n):
    w = words(text)
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


PERSON = re.compile(r"\b([A-Z][a-z]+) ([A-Z][a-z]+)\b")  # a first and last name


def world_names():
    """First and last names of the worlds' people: the persona, person-shaped fact values, never-told probes."""
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
    ap.add_argument("--parquet", required=True)
    args = ap.parse_args()
    if OUT.exists():
        raise SystemExit(f"{OUT} exists; the pool is built once")
    import pyarrow.parquet as pq

    blob = Path(args.parquet).read_bytes()
    if hashlib.sha256(blob).hexdigest() != PARQUET_SHA256:
        raise SystemExit("the NQ-open parquet is not the known slice's pinned file")
    table = pq.read_table(args.parquet).to_pylist()
    known_rows = {k["source_row"] for k in jsonl(DATA / "known_slice_v1.jsonl")}
    gate = jsonl(DATA / "simpleqa" / "gate_slice_v1.jsonl") + jsonl(DATA / "general_slice_v1.jsonl")
    gate_runs = set().union(*(runs(f"{g.get('question', '')} {g.get('answer', g.get('gold', ''))}", 3) for g in gate))
    forget_runs = set().union(*(runs(f["question"], 5) for name in ("forget_slice_v1", "forget_slice_v2")
                                for f in jsonl(DATA / f"{name}.jsonl")))
    banned = world_names()

    def clean(question, answer):
        t = f"{question} {answer}".lower()
        return not (HEALTH.search(t) or CHANGING.search(question.lower()) or runs(f"{question} {answer}", 3) & gate_runs
                    or runs(question, 5) & forget_runs or set(words(t)) & banned)

    order = sorted(range(len(table)), key=lambda i: hashlib.sha256(f"public-anchor-pool-v1:{i}".encode()).hexdigest())
    parts = {"nq": [], "who_is": [], "where_is": []}
    seen = set()
    for i in order:
        if i in known_rows:
            continue
        q, gold = table[i]["question"], table[i]["answer"]
        a = gold[0] if gold else ""
        if not a or len(a.split()) > 4:
            continue
        lead = q.split()[0] if q.split() else ""
        cands = []
        if lead in ("who", "where"):
            cands.append(("nq", q[0].upper() + q[1:] + "?", gold))
        aw = a.split()
        if lead == "who" and 2 <= len(aw) <= 3 and all(x[:1].isupper() and x.replace(".", "").isalpha() for x in aw):
            cands.append(("who_is", f"Who is {a}?", None))
        if lead == "where" and 1 <= len(aw) <= 3 and all(x[:1].isupper() and x.replace(".", "").isalpha() for x in aw):
            cands.append(("where_is", f"Where is {a}?", None))
        for part, question, g in cands:
            if len(parts[part]) >= PER_PART or question.lower() in seen or not clean(question, " ".join(gold)):
                continue
            seen.add(question.lower())
            parts[part].append({"id": f"P{len(seen):03d}", "part": part, "question": question, "nq_row": i,
                                "nq_question": q, **({"gold": g} if g else {})})
        if all(len(v) >= PER_PART for v in parts.values()):
            break
    items = [it for part in ("nq", "who_is", "where_is") for it in parts[part]]
    with open(OUT, "w") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    manifest = {"dataset": "google-research-datasets/nq_open", "revision": REVISION, "split": "validation", "license": "cc-by-sa-3.0",
                "parquet_sha256": PARQUET_SHA256, "parts": {k: len(v) for k, v in parts.items()},
                "pick": "sha256('public-anchor-pool-v1:<row>') ascending, after the filters",
                "disjoint_from": {"known_slice_v1 rows": 0, "simpleqa gate + general slice 3-word runs": 0,
                                  "forget v1/v2 5-word runs": 0, "world and never-told names": 0},
                "use": "base replies in the served agent form (search_web offered), kept when they answer or search, as night "
                       "rows set against the ask-back rows (night/public_anchor_rows.py)",
                "file": str(OUT.relative_to(ROOT)), "sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
                "license_of_this_file": "derived from Natural Questions (open-domain split), released under CC BY-SA 3.0, the source's licence",
                "attribution": ["Kwiatkowski et al., Natural Questions: A Benchmark for Question Answering Research, TACL 7:453-466, 2019",
                                "Lee, Chang and Toutanova, Latent Retrieval for Weakly Supervised Open Domain Question Answering, ACL 2019"]}
    OUT.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({k: manifest[k] for k in ("parts", "sha256")}))


if __name__ == "__main__":
    main()
