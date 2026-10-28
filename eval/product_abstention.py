#!/usr/bin/env python3
"""Abstention in the product's own form (owner card META-ABSTENTION-GATE, option B): score answers the day agent
gave with its real prompt, for the base and for the adapter, on two frozen sets:
  general    - the SimpleQA gate slice questions (eval/data/simpleqa/gate_slice_v1.jsonl), asked as a user would
  never_told - the world's never-told probes (eval/data/never_told/<w>.jsonl), asked with the notes in context
Abstention is read with the same rules as the night: simpleqa_gate.is_abstain on general answers, the never-told
scorer (omniscience.ABSTAIN_RULE via scoring.score_probe) on never-told answers. Passed when the adapter is no worse
than the base: general abstain rate at most --max-rate-drop below the base's, never-told abstentions at most
--max-never-told-drop below the base's. Writes runs/<w>/<d>/night/eval/product-abstention-night.json; the night's
report step (or eval/redecide_night.py) reads it into eval.json "abstention_product_form".

Answers file (JSONL, one per answer): {"arm": "base"|"adapter", "set": "general"|"never_told", "id", "answer",
"truncated": bool}. Whoever runs the product-form calls (Meta steering, on the live server) writes it.

  eval/product_abstention.py --world W --day N --answers FILE [--out FILE]
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "night"))
import scoring  # noqa: E402
import simpleqa_gate  # noqa: E402
from paths import runs_root  # noqa: E402


def abstained(row):
    if row.get("truncated"):
        return False
    if row["set"] == "general":
        return simpleqa_gate.is_abstain(row["answer"])
    return scoring.score_probe({"kind": "never_told"}, row["answer"], False, {})[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--answers", required=True)
    ap.add_argument("--max-rate-drop", type=float, default=0.10)
    ap.add_argument("--max-never-told-drop", type=int, default=3)
    ap.add_argument("--runs")
    ap.add_argument("--out")
    args = ap.parse_args()
    rows = [json.loads(line) for line in Path(args.answers).read_text().splitlines() if line.strip()]
    gate_ids = {q["id"] for q in simpleqa_gate.gate_items()}
    nt_ids = {json.loads(line)["id"] for line in (ROOT / "eval" / "data" / "never_told" / f"{args.world}.jsonl").read_text().splitlines() if line.strip()}
    arms = {}
    for arm in ("base", "adapter"):
        g = [r for r in rows if r["arm"] == arm and r["set"] == "general" and r["id"] in gate_ids]
        nt = [r for r in rows if r["arm"] == arm and r["set"] == "never_told" and r["id"] in nt_ids]
        arms[arm] = {"general": {"n": len(g), "abstain": sum(abstained(r) for r in g)},
                     "never_told": {"n": len(nt), "abstain": sum(abstained(r) for r in nt)}}
        arms[arm]["general"]["abstain_rate"] = round(arms[arm]["general"]["abstain"] / len(g), 4) if g else None
    b, a = arms["base"], arms["adapter"]
    complete = all(x[s]["n"] for x in (a, b) for s in ("general", "never_told"))
    passed = complete and a["general"]["abstain_rate"] >= b["general"]["abstain_rate"] - args.max_rate_drop \
        and a["never_told"]["abstain"] >= b["never_told"]["abstain"] - args.max_never_told_drop
    rec = {"world": args.world, "day": args.day, "form": "the day agent's prompt; general = SimpleQA gate slice questions, "
           "never_told = the world's never-told probes with the notes in context", "arms": arms, "complete": complete,
           "rule": f"adapter general abstain rate >= base's - {args.max_rate_drop} and adapter never-told abstentions >= base's - "
                   f"{args.max_never_told_drop}", "passed": bool(passed), "answers_file": str(args.answers)}
    out = Path(args.out) if args.out else runs_root(args.runs) / args.world / str(args.day) / "night" / "eval" / "product-abstention-night.json"
    out.write_text(json.dumps(rec, indent=1) + "\n")
    print(json.dumps({k: rec[k] for k in ("world", "arms", "passed")}))


if __name__ == "__main__":
    main()
