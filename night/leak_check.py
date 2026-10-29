#!/usr/bin/env python3
"""Leak check: no training row may contain a held-out probe or the general slice.

A hit is either a training text that contains a whole probe question (after normalization), or a
word 8-gram shared with a probe question. Fact values and names appear in both by design, so
shorter overlaps are reported as a statistic, not a failure. Exit 1 on any hit, so the night fails.
With --never-told it also checks the world's never-told probes (eval/data/never_told/<w>.jsonl): those questions
are short, so a training row that names a probe's key ("courier pickup time") or its made-up person is a hit too.
This is the only night-side code that opens worlds/<w>/heldout/ or the never-told probes. With --write-clean it
removes the hit rows (row ids only; nothing from the probes enters training) and the cleaned file is checked again.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAIL_N = 8
STAT_N = 5


def words(text):
    t = text.lower().replace("’", "'")
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", t)


def ngrams(tokens, n):
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def check(train_rows, probes):
    hits = []
    texts = [(r["id"], " ".join(words(m["content"]))) for r in train_rows for m in r["messages"]]
    text_grams_fail = [(rid, ngrams(t.split(), FAIL_N)) for rid, t in texts]
    stat_grams = set()
    for _, t in texts:
        stat_grams |= ngrams(t.split(), STAT_N)
    stat_overlap = 0
    for p in probes:
        q = " ".join(words(p["question"]))
        q_fail = ngrams(q.split(), FAIL_N)
        for rid, t in texts:
            if re.search(r"(?<![a-z0-9])" + re.escape(q) + r"(?![a-z0-9])", t):
                hits.append({"probe": p["id"], "train_row": rid, "rule": "whole question"})
        for rid, grams in text_grams_fail:
            shared = q_fail & grams
            if shared:
                hits.append({"probe": p["id"], "train_row": rid, "rule": f"{FAIL_N}-gram", "ngram": " ".join(next(iter(shared)))})
        if ngrams(q.split(), STAT_N) & stat_grams:
            stat_overlap += 1
    unique = {(h["probe"], h["train_row"]): h for h in hits}
    return list(unique.values()), stat_overlap


def check_never_told_keys(train_rows, never_told):
    """A row that names an other_key probe's key or a lookalike probe's made-up person (phrase match on normalized
    words). A lookalike probe asks for one of the world's own keys, so its key is not checked."""
    texts = [(r["id"], " ".join(words(m["content"]))) for r in train_rows for m in r["messages"]]
    hits = []
    for p in never_told:
        spec = p.get("spec", {})
        for field in (("person",) if p.get("type") == "lookalike" else ("key",)):
            if not spec.get(field):
                continue
            phrase = " ".join(words(spec[field]))
            rx = re.compile(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])")
            hits += [{"probe": p["id"], "train_row": rid, "rule": f"never-told {field}", "ngram": phrase}
                     for rid, t in texts if rx.search(t)]
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--train", required=True)
    ap.add_argument("--out", required=True, help="leak report JSON")
    ap.add_argument("--write-clean", help="also write the train file without the hit rows here (decontamination); "
                                          "the night then re-runs this check on the clean file, which must pass")
    ap.add_argument("--never-told", action="store_true", help="also check the world's never-told probes and their keys")
    args = ap.parse_args()
    probes = read_jsonl(ROOT / "worlds" / args.world / "heldout" / "probes.jsonl")
    never_told = read_jsonl(ROOT / "eval" / "data" / "never_told" / f"{args.world}.jsonl") if args.never_told else []
    probes += never_told
    probes += read_jsonl(ROOT / "eval" / "data" / "general_slice_v1.jsonl")
    for name in ("forget_slice_v1.jsonl", "forget_slice_v2.jsonl"):
        forget = ROOT / "eval" / "data" / name
        if forget.exists():
            probes += read_jsonl(forget)
    train_rows = read_jsonl(args.train)
    hits, stat_overlap = check(train_rows, probes)
    hits += check_never_told_keys(train_rows, never_told)
    report = {"train": args.train, "train_rows": len(train_rows), "probes": len(probes), "never_told_checked": len(never_told),
              "hits": len(hits),
              "hit_rows": sorted({h["train_row"] for h in hits}), "examples": hits[:20],
              f"probes_sharing_a_{STAT_N}gram": stat_overlap, "passed": not hits}
    if args.write_clean:
        drop = set(report["hit_rows"])
        with open(args.write_clean, "w") as f:
            for r in train_rows:
                if r["id"] not in drop:
                    f.write(json.dumps(r) + "\n")
        report["clean_file"] = args.write_clean
        report["dropped_rows"] = len(drop)
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("train_rows", "probes", "hits", f"probes_sharing_a_{STAT_N}gram", "passed")}))
    sys.exit(0 if not hits else 1)


if __name__ == "__main__":
    main()
