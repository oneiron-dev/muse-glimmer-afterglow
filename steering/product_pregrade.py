#!/usr/bin/env python3
"""Grade a product-form read's answers into the shared grade cache while the read is still asking, so product_honesty
finds them cached when the read ends (same grader, same prompt, same cache key: the measurement does not change).

Every --every seconds it reads raw.jsonl, grades the answered rows of the general and known sets that the cache does not
hold (product_honesty.pending), and appends them to the cache; it stops after a last pass once the process --while-pid
has ended. Run it beside product_answers.py, never beside product_honesty.py (both append to the cache).

  product_pregrade.py --raw OUT/<world>/raw.jsonl --cache .../known-split-grades.jsonl --while-pid PID
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import product_honesty as ph  # noqa: E402


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def one_pass(raw, cache, gold, known_alts, template, grader, parallel):
    grades = {r["key"]: r for r in map(json.loads, cache.read_text().splitlines())} if cache.exists() else {}
    rows = [json.loads(line) for line in raw.read_text().splitlines() if line.strip()] if raw.exists() else []
    _, todo = ph.pending(rows, grades, gold, known_alts)
    log = cache.with_name(cache.stem + "-calls.jsonl")
    with ThreadPoolExecutor(max_workers=parallel) as pool, open(cache, "a") as f:
        for rec in pool.map(lambda it: ph.grade_with_backoff(it, template, grader, log), todo.values()):
            f.write(json.dumps(rec) + "\n")
            f.flush()
    return len(todo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--while-pid", type=int, required=True, help="the product_answers.py process of the read")
    ap.add_argument("--grader", default="spark-sub")
    ap.add_argument("--parallel", type=int, default=16)
    ap.add_argument("--every", type=float, default=60)
    args = ap.parse_args()
    gold, known_alts = ph.load_gold()
    template = (ph.aa_omniscience.DATA / "grader_prompt.txt").read_text().strip()
    raw, cache, total = Path(args.raw), Path(args.cache), 0
    while True:
        running = alive(args.while_pid)
        total += one_pass(raw, cache, gold, known_alts, template, args.grader, args.parallel)
        if not running:
            break
        time.sleep(args.every)
    print(json.dumps({"pregraded": total, "ended": time.strftime("%H:%M:%S")}), flush=True)


if __name__ == "__main__":
    main()
