#!/usr/bin/env python3
"""Decision rows -> the trainer's format (night/train_adapter.py): the row's prompt as the user turn and its label as
the whole assistant reply, so the loss falls on the label token (and its end token) only.

Route rows are long (every note is an option), so a seeded share of them is kept to hold the training time; the pair
kinds are kept whole.
"""

import argparse
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "decide" / "data"
SEED = 20261014


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--route-rows", type=int, default=440, help="route rows kept (seeded sample, ask share preserved)")
    ap.add_argument("--cross-world-asks", type=int, default=120, help="never-told style ask rows added on top")
    ap.add_argument("--out", default=str(DATA / "train-decision.jsonl"))
    args = ap.parse_args()
    rng = random.Random(SEED)
    route = read_jsonl(DATA / "route-train.jsonl")
    cross = [r for r in route if r["source"] == "train_cross_world_ask"]
    route = [r for r in route if r["source"] != "train_cross_world_ask"]
    ask = [r for r in route if r["label"] == "0"]
    note = [r for r in route if r["label"] != "0"]
    k_ask = round(args.route_rows * len(ask) / len(route))
    keep = rng.sample(ask, min(k_ask, len(ask))) + rng.sample(note, min(args.route_rows - k_ask, len(note)))
    keep += rng.sample(cross, min(args.cross_world_asks, len(cross)))
    rows = keep + read_jsonl(DATA / "duplicate-train.jsonl") + read_jsonl(DATA / "supersede-train.jsonl")
    rng.shuffle(rows)
    with open(args.out, "w") as f:
        for r in rows:
            f.write(json.dumps({"id": r["id"], "kind": r["kind"], "messages": [{"role": "user", "content": r["prompt"]},
                                                                             {"role": "assistant", "content": r["label"]}]},
                               ensure_ascii=False) + "\n")
    counts = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    print(json.dumps({"out": args.out, "rows": len(rows), "by_kind": counts}))


if __name__ == "__main__":
    main()
