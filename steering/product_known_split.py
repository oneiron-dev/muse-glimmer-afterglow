#!/usr/bin/env python3
"""Which general questions an adapter changed its "I don't know" on, split by whether the answer was known: from a
product-form answers file (answers-<arm>.jsonl, base and adapter from one server run), on the general set:

  new declines  the adapter declines, the base answered: graded on the base's answer. Base right = a known item (the
                adapter now says "I don't know" to something it knew); base wrong = a true "I don't know".
  new answers   the base declined, the adapter answers: graded on the adapter's answer, right or wrong.

Grades come from the AA-Omniscience grader of record (eval/aa_omniscience.py: its deterministic prepass, then the
grader model spark-sub with the same grader prompt and gold targets of the SimpleQA gate slice): CORRECT, INCORRECT,
PARTIAL_ANSWER, NOT_ATTEMPTED. Grades are cached by question and answer, so a base answer shared by several arms is
graded once. The grader key is read from the environment (CLIPROXY_KEY), never written.

  product_known_split.py --answers w1-idk=.../answers-night3idk.jsonl --cache .../known-split-grades.jsonl --out FILE
"""

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "eval"))
sys.path.insert(0, str(HERE.parent / "night"))
import aa_omniscience  # noqa: E402
import simpleqa_gate  # noqa: E402
from product_abstention import abstained, refuse_mixed_server, refuse_mixed_tools  # noqa: E402

LETTERS = {"A": "CORRECT", "B": "INCORRECT", "C": "PARTIAL_ANSWER", "D": "NOT_ATTEMPTED"}
DETERMINISTIC = {"correct": "CORRECT", "not_attempted": "NOT_ATTEMPTED"}


def key(qid, answer):
    return f"{qid}:{hashlib.sha256(answer.encode()).hexdigest()[:16]}"


def grade_one(item, template, grader, log):
    import teacher
    det = aa_omniscience.deterministic_grade(item["answer"], item["gold"])
    if det is not None:
        return {"key": item["key"], "grade": DETERMINISTIC[det], "path": "deterministic"}
    msg = template.format(question=item["question"], target=item["gold"], predicted_answer=item["answer"])
    text, _, _ = teacher.complete(grader, [{"role": "user", "content": msg}], max_tokens=4000, temperature=0.0,
                                  log_path=log, purpose=f"known-split {item['key']}")
    letter = next((c for c in text.strip().upper() if c in LETTERS), None)
    return {"key": item["key"], "grade": LETTERS.get(letter), "path": "model"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", action="append", required=True, help="LABEL=answers-<arm>.jsonl")
    ap.add_argument("--cache", required=True, help="grades JSONL, appended to and reused")
    ap.add_argument("--grader", default="spark-sub")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--out")
    args = ap.parse_args()
    gold = {g["id"]: g for g in simpleqa_gate.gate_items()}
    template = (aa_omniscience.DATA / "grader_prompt.txt").read_text().strip()
    cache = Path(args.cache)
    grades = {r["key"]: r for r in map(json.loads, cache.read_text().splitlines())} if cache.exists() else {}
    plan = {}
    for item in args.answers:
        label, path = item.split("=", 1)
        rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
        by = {}
        for r in rows:
            if r["set"] == "general":
                by.setdefault(r["id"], {})[r["arm"]] = r
        pairs = [(v["base"], v["adapter"]) for v in by.values() if {"base", "adapter"} <= set(v)]
        refuse_mixed_tools(pairs, path)
        refuse_mixed_server(pairs, path)
        new_declines = [b for b, a in pairs if abstained(a) and not abstained(b)]
        new_answers = [a for b, a in pairs if abstained(b) and not abstained(a)]
        plan[label] = {"pairs": len(pairs), "new_declines": new_declines, "new_answers": new_answers}
    todo = {}
    for p in plan.values():
        for r in p["new_declines"] + p["new_answers"]:
            k = key(r["id"], r["answer"])
            if k not in grades:
                todo[k] = {"key": k, "answer": r["answer"], "question": gold[r["id"]]["question"],
                           "gold": gold[r["id"]]["answer"]}
    log = cache.with_name(cache.stem + "-calls.jsonl")
    with ThreadPoolExecutor(max_workers=args.parallel) as pool, open(cache, "a") as f:
        for rec in pool.map(lambda it: grade_one(it, template, args.grader, log), todo.values()):
            grades[rec["key"]] = rec
            f.write(json.dumps(rec) + "\n")
            f.flush()
    report = {"grader": args.grader, "rule": "new declines graded on the base's answer, new answers on the adapter's",
              "arms": {}}
    for label, p in plan.items():
        def tally(rs):
            out = {}
            for r in rs:
                g = grades[key(r["id"], r["answer"])]["grade"]
                out[g] = out.get(g, 0) + 1
            return out
        report["arms"][label] = {"pairs": p["pairs"],
                                 "new_declines": {"n": len(p["new_declines"]), "base_answer": tally(p["new_declines"])},
                                 "new_answers": {"n": len(p["new_answers"]), "adapter_answer": tally(p["new_answers"])}}
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
    print("| arm | new declines | base was right (known) | base was wrong (true I don't know) | partial / other | "
          "new answers | right | wrong | partial / other |")
    print("|---|---|---|---|---|---|---|---|---|")
    for label, a in report["arms"].items():
        d, n = a["new_declines"], a["new_answers"]
        rest = lambda t: sum(v for k, v in t.items() if k not in ("CORRECT", "INCORRECT"))  # noqa: E731
        print(f"| {label} | {d['n']} | {d['base_answer'].get('CORRECT', 0)} | {d['base_answer'].get('INCORRECT', 0)} | "
              f"{rest(d['base_answer'])} | {n['n']} | {n['adapter_answer'].get('CORRECT', 0)} | "
              f"{n['adapter_answer'].get('INCORRECT', 0)} | {rest(n['adapter_answer'])} |")


if __name__ == "__main__":
    main()
