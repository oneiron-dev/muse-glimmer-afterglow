#!/usr/bin/env python3
"""One table across nights: per world-day, the morning eval's arms, learned/forgot statistics and the decision.

Reads runs/<w>/<d>/night/{eval.json,decision.json,receipt.json}. Prints markdown and writes --out JSON.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "night"))
from paths import runs_root  # noqa: E402


def frac(arm, kind):
    k = arm["by_kind"].get(kind)
    return f"{k['pass']}/{k['n']}" if k else "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs")
    ap.add_argument("--day", type=int)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rows = []
    for night in sorted(runs_root(args.runs).glob("*/*/night")):
        if not (night / "eval.json").exists() or (args.day and night.parent.name != str(args.day)):
            continue
        e = json.loads((night / "eval.json").read_text())
        d = json.loads((night / "decision.json").read_text())
        r = json.loads((night / "receipt.json").read_text())
        tr = r.get("train_report", {})
        a = e["arms"]
        rows.append({"world": e["world"], "day": e["day"], "decision": d["decision"],
                     "facts": {arm: f"{a[arm]['facts_learned']}/{a[arm]['facts']}" for arm in a},
                     "forward": {arm: frac(a[arm], "forward") for arm in a},
                     "stale": {arm: f"{v['stale']}/{v['n']}" for arm, v in e["stale"].items()},
                     "general": {arm: frac(a[arm], "general") for arm in a},
                     "learned": e["learned"], "forgot": e["forgot"],
                     "train_rows": r.get("train_set", {}).get("rows"), "train_s": tr.get("train_s"),
                     "teacher_tokens": r.get("teacher_tokens")})
    Path(args.out).write_text(json.dumps(rows, indent=1) + "\n")
    print("| world | day | decision | facts A → B (notes off) | C notes | D both | forward A / B / C / D | B−A per fact [95% CI] | B−C | forgetting B vs A (200) | tools | train rows | train s |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for x in rows:
        f, l, g = x["forward"], x["learned"], x["forgot"]
        print(f"| {x['world']} | {x['day']} | {x['decision']} | {x['facts']['A']} → {x['facts']['B']} | {x['facts']['C']} | {x['facts']['D']} | "
              f"{f['A']} / {f['B']} / {f['C']} / {f['D']} | {l['B_minus_A']['est']:+.3f} [{l['B_minus_A']['ci95'][0]:+.3f}, {l['B_minus_A']['ci95'][1]:+.3f}] | "
              f"{l['B_minus_C']['est']:+.3f} | {g['delta'] * 100:+.1f} pt [{g['ci95'][0] * 100:+.1f}, {g['ci95'][1] * 100:+.1f}] | "
              f"{'no new failure' if not g['critical_tool_failures'] else g['critical_tool_failures']} | {x['train_rows']} | {x['train_s']} |")


if __name__ == "__main__":
    main()
