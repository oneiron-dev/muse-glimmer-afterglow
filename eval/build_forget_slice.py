#!/usr/bin/env python3
"""Build the forgetting slices of record.

v1: 180 MMLU-Pro test items + 20 synthetic tool-call items (nights 1-5).
v2: 300 more MMLU-Pro test items, disjoint from v1 (lead, 29 Oct 22:59): from night 6 the forgetting check reads
v1 + v2 (500 items), so a night that changes nothing no longer fails the -5 point bar on noise (95% interval about
+-3.4 points at night 5's discordance, against +-5.3 on v1 alone).
MMLU-Pro (TIGER-Lab/MMLU-Pro, MIT) is read at a pinned dataset revision and sampled stratified
by category with a fixed seed; the tool items come from fixed templates. Output:
eval/data/forget_slice_<v>.jsonl and its manifest. Each is built once, before any adapter is scored on it.
Run: uv run --with pyarrow python eval/build_forget_slice.py [--slice v2]
"""

import argparse
import hashlib
import json
import random
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REVISION = "b189ec765aa7ed75c8acfea42df31fdae71f97be"
URL = f"https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro/resolve/{REVISION}/data/test-00000-of-00001.parquet"
LETTERS = "ABCDEFGHIJ"
DATA = ROOT / "eval" / "data"
# seed, MMLU-Pro items, tool items, the slices it must not overlap
SLICES = {"v1": {"seed": 20261014, "n_mmlu": 180, "tools": True, "disjoint_from": []},
          "v2": {"seed": 20261029, "n_mmlu": 300, "tools": False, "disjoint_from": ["v1"]}}
SEED = SLICES["v1"]["seed"]

TOOL_TEMPLATES = [
    ("create_task", {"title": "Renew the {w} licence", "due": "2026-12-{d:02d}"}),
    ("lookup_part", {"part": "PX-{n}", "qty": "{k}"}),
    ("set_reminder", {"time": "{h:02d}:{m:02d}", "text": "Call the {w} supplier"}),
    ("send_message", {"to": "{w}-team", "text": "Batch {n} is ready"}),
    ("book_room", {"room": "B-{k}", "hour": "{h}"}),
]
WORDS = ["harbour", "granite", "orchard", "lantern", "meadow", "copper", "willow", "falcon", "quarry", "tundra"]


def tool_items():
    rng = random.Random(SEED + 1)
    items = []
    for i in range(20):
        name, args_t = TOOL_TEMPLATES[i % len(TOOL_TEMPLATES)]
        vals = {"w": rng.choice(WORDS), "d": rng.randint(1, 28), "n": rng.randint(100, 999), "k": rng.randint(2, 9),
                "h": rng.randint(8, 17), "m": rng.choice([0, 15, 30, 45])}
        args = {}
        for key, tmpl in args_t.items():
            v = tmpl.format(**vals)
            args[key] = int(v) if v.isdigit() and key in ("qty", "hour") else v
        gold = {"tool": name, **args}
        q = f"Emit JSON only: {json.dumps(gold)}. Reply with JSON only, no other text."
        items.append({"id": f"T{i + 1:02d}", "kind": "forget_tool", "question": q, "gold": gold, "scorer": "json_exact"})
    return items


def mmlu_items(seed, n_mmlu, exclude):
    import pyarrow.parquet as pq
    cache = ROOT / ".deps" / "mmlu-pro-test.parquet"
    cache.parent.mkdir(exist_ok=True)
    if not cache.exists():
        urllib.request.urlretrieve(URL, cache)
    table = pq.read_table(cache).to_pylist()
    by_cat = {}
    for row in table:
        if f"M{row['question_id']}" not in exclude:
            by_cat.setdefault(row["category"], []).append(row)
    cats = sorted(by_cat)
    rng = random.Random(seed)
    quota = {c: n_mmlu // len(cats) for c in cats}
    for c in rng.sample(cats, n_mmlu - sum(quota.values())):
        quota[c] += 1
    items = []
    for c in cats:
        pool = sorted(by_cat[c], key=lambda r: r["question_id"])
        for row in rng.sample(pool, quota[c]):
            opts = "\n".join(f"{LETTERS[i]}) {o}" for i, o in enumerate(row["options"]))
            q = f"{row['question']}\n\n{opts}\n\nReply with just the letter of the correct option."
            items.append({"id": f"M{row['question_id']}", "kind": "forget_mmlu", "category": c, "question": q,
                          "gold": row["answer"], "scorer": "choice_letter", "n_options": len(row["options"])})
    return items, hashlib.sha256(cache.read_bytes()).hexdigest(), {c: quota[c] for c in cats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slice", choices=sorted(SLICES), default="v1")
    ap.add_argument("--out", help="another path (a rebuild check); default eval/data/forget_slice_<slice>.jsonl")
    args = ap.parse_args()
    spec = SLICES[args.slice]
    out = Path(args.out) if args.out else DATA / f"forget_slice_{args.slice}.jsonl"
    if out.exists():
        raise SystemExit(f"{out} exists; a forgetting slice is built once")
    exclude, disjoint = set(), []
    for other in spec["disjoint_from"]:
        path = DATA / f"forget_slice_{other}.jsonl"
        ids = {json.loads(line)["id"] for line in path.read_text().splitlines() if line.strip()}
        exclude |= ids
        disjoint.append({"file": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "items": len(ids)})
    mmlu, parquet_sha, quota = mmlu_items(spec["seed"], spec["n_mmlu"], exclude)
    tools = tool_items() if spec["tools"] else []
    items = mmlu + tools
    assert not exclude & {it["id"] for it in items}
    with open(out, "w") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    manifest = {"mmlu_pro": {"dataset": "TIGER-Lab/MMLU-Pro", "revision": REVISION, "split": "test", "parquet_sha256": parquet_sha,
                             "seed": spec["seed"], "per_category": quota, "items": len(mmlu)},
                "tool_items": len(tools), "file": str(out.relative_to(ROOT)) if out.is_relative_to(ROOT) else str(out),
                "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}
    if disjoint:
        manifest["disjoint_from"] = disjoint
    out.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
