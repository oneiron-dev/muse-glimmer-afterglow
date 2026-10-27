#!/usr/bin/env python3
"""Compare two nights of one world-day on the same probes: an experiment arm against the night of record.

Per arm (A/B/C/D): facts learned, forward passes, Omniscience right/wrong/abstain; for B (notes off):
the paired per-fact pass-rate delta (cluster bootstrap over facts) and exact McNemar on the forward probes;
general-slice and forgetting-slice changes between the two adapters; and the miss causes of each night.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "night"))
import omniscience  # noqa: E402
import stats  # noqa: E402
from paths import runs_root  # noqa: E402
from summarize_sweep import passes, per_fact_rate  # noqa: E402


def load(night, arm):
    return json.loads((night / "eval" / f"score-night-{arm}.json").read_text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, default=1)
    ap.add_argument("--base", default="night", help="the night of record")
    ap.add_argument("--arm", required=True, help="the experiment's night folder")
    ap.add_argument("--runs")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    run_dir = runs_root(args.runs) / args.world / str(args.day)
    nights = {"base": run_dir / args.base, "arm": run_dir / args.arm}
    out = {"world": args.world, "day": args.day, "base": args.base, "arm": args.arm, "arms": {}}
    for label, night in nights.items():
        for arm in "ABCD":
            rec = load(night, arm)
            om = omniscience.score(rec["results"], ("forward",))
            out["arms"][f"{label}-{arm}"] = {"facts_learned": rec["summary"]["facts_learned"], "facts": rec["summary"]["facts"],
                                              "forward": rec["summary"]["by_kind"]["forward"]["pass"],
                                              "omniscience_user_facts": om,
                                              "omniscience_general": omniscience.score(rec["results"], omniscience.SETS["general"])}
    b0, b1 = load(nights["base"], "B"), load(nights["arm"], "B")
    out["B_arm_minus_B_base"] = {
        "per_fact": stats.paired_cluster_bootstrap(per_fact_rate(b0), per_fact_rate(b1)),
        "forward_probes": stats.paired_proportions(passes(b0, ("forward",)), passes(b1, ("forward",))),
        "general_and_forgetting": stats.paired_proportions(passes(b0, omniscience.SETS["general"]), passes(b1, omniscience.SETS["general"]))}
    for label, night in nights.items():
        r = subprocess.run([sys.executable, str(ROOT / "eval" / "miss_breakdown.py"), "--world", args.world, "--day", str(args.day),
                            "--night-dir", night.name, *(["--runs", args.runs] if args.runs else []),
                            "--out", str(Path(args.out).with_suffix(f".miss-{label}.json"))], capture_output=True, text=True)
        out[f"misses_{label}"] = json.loads(r.stdout.strip().splitlines()[-1])
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
