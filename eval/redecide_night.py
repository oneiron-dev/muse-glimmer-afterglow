#!/usr/bin/env python3
"""Re-decide a finished night under another abstention rule, from its saved eval.json (no GPU, no model call).

  eval/redecide_night.py --world W --day N --rule v3|product|report-only [--of-record]

Rules (owner card META-ABSTENTION-GATE): v3 = the SimpleQA gate slice, two-sided (rule of record until the owner
rules); product = abstention in the day agent's own form, no worse than base (needs
eval/product-abstention-night.json from eval/product_abstention.py); report-only = abstention never gates.
Writes decision-<rule>.json beside decision.json. Only with --of-record does it replace decision.json (and move
runs/<w>/adapters.json "current" on a keep); the receipt logs every re-decision.
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "night"))
sys.path.insert(0, str(ROOT / "eval"))
from paths import runs_root  # noqa: E402
from run_night import ABSTENTION_RULES, decide  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--rule", required=True, choices=ABSTENTION_RULES)
    ap.add_argument("--night-dir", default="night")
    ap.add_argument("--of-record", action="store_true", help="the owner ruled: this rule's decision becomes decision.json")
    ap.add_argument("--runs")
    args = ap.parse_args()
    runs = runs_root(args.runs)
    night = runs / args.world / str(args.day) / args.night_dir
    e = json.loads((night / "eval.json").read_text())
    product = night / "eval" / "product-abstention-night.json"
    if product.exists():
        e["abstention_product_form"] = json.loads(product.read_text())
        (night / "eval.json").write_text(json.dumps(e, indent=1) + "\n")
    receipt = json.loads((night / "receipt.json").read_text())
    verify = json.loads((night / "gguf-verify.json").read_text())
    d = decide(e, verify, receipt.get("leak_check", {}).get("clean_passed", False), abstention_rule=args.rule)
    (night / f"decision-{args.rule}.json").write_text(json.dumps(d, indent=1) + "\n")
    record = json.loads((night / "decision.json").read_text())
    entry = {"at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "rule": args.rule,
             "decision": d["decision"], "of_record": args.of_record, "record_was": record["decision"]}
    if args.of_record:
        (night / "decision.json").write_text(json.dumps(d, indent=1) + "\n")
        receipt["decision"] = d["decision"]
        if args.night_dir == "night":
            pointer_path = runs / args.world / "adapters.json"
            pointer = json.loads(pointer_path.read_text())
            mine = [h for h in pointer["history"] if h["day"] == args.day]
            if mine:
                mine[-1]["decision"] = d["decision"]
                if d["decision"] == "keep":
                    pointer["current"] = mine[-1]
                elif pointer.get("current") and pointer["current"].get("day") == args.day:
                    kept = [h for h in pointer["history"] if h["decision"] == "keep" and h["day"] != args.day]
                    pointer["current"] = kept[-1] if kept else None
                pointer_path.write_text(json.dumps(pointer, indent=1) + "\n")
    receipt.setdefault("redecisions", []).append(entry)
    (night / "receipt.json").write_text(json.dumps(receipt, indent=1) + "\n")
    print(json.dumps({"world": args.world, "day": args.day, **entry, "checks": d["checks"]}))


if __name__ == "__main__":
    main()
