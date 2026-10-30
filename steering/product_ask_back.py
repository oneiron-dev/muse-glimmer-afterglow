#!/usr/bin/env python3
"""Ask-back count for the product-form reads: how many general (and known) replies decline and ask the user to supply
the fact ("I don't have that in your notes. Can you tell me?"). An answer counts when the night's rule calls it a
decline (eval/product_abstention.abstained) and it asks the user (ASK below). Base and adapter rows come from the same
answers-<arm>.jsonl the scorer reads (same questions, same server run).

  product_ask_back.py --answers w4-night5=.../w4-grad-student/answers-night5.jsonl [--answers ...] [--out FILE]
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "eval"))
from product_abstention import abstained  # noqa: E402

ASK = re.compile(r"(can you tell me|could you tell me|what (is|was) it|do you know|tell me)", re.I)


def count(rows):
    """{"n", "declines", "ask_back", "search_web_calls"} for one arm's rows of one set; search_web_calls counts replies
    whose turn called search_web (rows from before tool_names was recorded count 0)."""
    declines = [r for r in rows if abstained(r)]
    return {"n": len(rows), "declines": len(declines), "ask_back": sum(bool(ASK.search(r["answer"] or "")) for r in declines),
            "search_web_calls": sum("search_web" in (r.get("tool_names") or []) for r in rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", action="append", required=True, help="LABEL=answers-<arm>.jsonl")
    ap.add_argument("--out", help="write the counts as JSON here")
    args = ap.parse_args()
    report = {"rule": "a decline (product_abstention.abstained) whose text asks the user: " + ASK.pattern, "arms": {}}
    for item in args.answers:
        label, path = item.split("=", 1)
        rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
        report["arms"][label] = {s: {arm: count([r for r in rows if r["set"] == s and r["arm"] == arm])
                                     for arm in ("base", "adapter")}
                                 for s in ("general", "known", "known2") if any(r["set"] == s for r in rows)}
    text = json.dumps(report, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print("| arm | set | n | base declines | base asks back | adapter declines | adapter asks back | search_web calls (base / adapter) |")
    print("|---|---|---|---|---|---|---|---|")
    for label, sets in report["arms"].items():
        for s, arms in sets.items():
            b, a = arms["base"], arms["adapter"]
            print(f"| {label} | {s} | {a['n']} | {b['declines']} | {b['ask_back']} | {a['declines']} | {a['ask_back']} | "
                  f"{b['search_web_calls']} / {a['search_web_calls']} |")


if __name__ == "__main__":
    main()
