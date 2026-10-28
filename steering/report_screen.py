#!/usr/bin/env python3
"""Tables from a screen output (summary.json + per-row JSON): the abstention table under the night's v3 gate rule
and the day-axis table, as markdown on stdout and report.json beside the summary.

Abstention: every arm's gate-slice abstain rate and exact right answers against the base arm of the baseline row
(v3, two-sided: |abstain - base| <= 0.10 and right >= base right - 3), the same on the bare questions, never-told
abstentions, day-1 forward probes (B) and the forget slice. Day axes: reply markers per row and arm, and the
Spark judge's pole agreement and coherence when judge-day.json exists.
"""

import argparse
import json
from pathlib import Path

MAX_DROP, MAX_RIGHT_LOSS = 0.10, 3  # eval/simpleqa_gate.py v3: abs(after - base) <= 0.10 and right_after >= right_base - 3


def pct(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--screen", required=True)
    ap.add_argument("--parity", action="append", default=[],
                    help="STATIC_ROW=PER_REQUEST_ROW: every answer must be byte-equal (a static load vs the patched server)")
    args = ap.parse_args()
    screen = Path(args.screen)
    summary = json.loads((screen / "summary.json").read_text())
    rows = summary["rows"]
    report = {"screen": str(screen), "abstention": [], "day": []}
    base = rows.get("baseline", {}).get("base", {})
    ref = {t: base.get(t) for t in ("gate", "gate_bare", "never_told")}
    lines = []
    if ref["gate"]:
        lines += ["| row | arm | gate abstain | v3 vs base | gate right | bare abstain | bare right | never-told | B fwd | forget | truncated |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for name, r in rows.items():
            for arm, tests in r.items():
                if not isinstance(tests, dict) or arm in ("cvecs", "cvec_layers") or "gate" not in tests:
                    continue
                g, b = tests.get("gate"), tests.get("gate_bare")
                drop = ref["gate"]["abstain_rate"] - g["abstain_rate"]
                v3 = abs(drop) <= MAX_DROP + 1e-9 and g["exact_correct"] >= ref["gate"]["exact_correct"] - MAX_RIGHT_LOSS
                nt, fw, fg = tests.get("never_told"), tests.get("forward"), tests.get("forget")
                trunc = sum((tests.get(t) or {}).get("truncated", 0) for t in ("gate", "gate_bare", "never_told", "forward", "forget"))
                entry = {"row": name, "arm": arm, "cvecs": r.get("cvecs"), "cvec_layers": r.get("cvec_layers"),
                         "gate_abstain": g["abstain_rate"], "gate_right": g["exact_correct"], "v3_pass": v3,
                         "v3_delta_pt": round(-100 * drop, 1),
                         "bare_abstain": b["abstain_rate"] if b else None, "bare_right": b["exact_correct"] if b else None,
                         "never_told": f"{nt['pass']}/{nt['n']}" if nt else None, "forward": f"{fw['pass']}/{fw['n']}" if fw else None,
                         "forget": f"{fg['pass']}/{fg['n']}" if fg else None, "truncated": trunc}
                report["abstention"].append(entry)
                lines.append(f"| {name} | {arm} | {pct(g['abstain_rate'])} | {'pass' if v3 else 'fail'} ({entry['v3_delta_pt']:+.1f} pt) | "
                             f"{g['exact_correct']} | {pct(entry['bare_abstain'])} | {entry['bare_right'] if b else '-'} | "
                             f"{entry['never_told'] or '-'} | {entry['forward'] or '-'} | {entry['forget'] or '-'} | {trunc} |")
    judge = {}
    if (screen / "judge-day.json").exists():
        judge = json.loads((screen / "judge-day.json").read_text())["summary"]
    day_lines = []
    for name, r in rows.items():
        for arm, tests in r.items():
            if not isinstance(tests, dict) or arm in ("cvecs", "cvec_layers"):
                continue
            for test, agg in tests.items():
                if not test.startswith("day:"):
                    continue
                j = judge.get(name, {}).get(arm, {})
                entry = {"row": name, "arm": arm, "axis": test[4:], **agg, "judge_toward_positive": j.get("toward_positive"),
                         "judge_coherence": j.get("coherence_steered"), "judge_coherence_base": j.get("coherence_base")}
                report["day"].append(entry)
                day_lines.append(f"| {name} | {arm} | {test[4:]} | {agg['words']:.0f} | {agg['asks']:.2f} | {agg['hedges_per_100w']:.1f} | "
                                 f"{agg['commits']:.2f} | {agg['casual_per_100w']:.1f} | {agg['contractions_per_100w']:.1f} | "
                                 f"{agg['repetition']:.2f} | {agg['truncated']} | {j.get('toward_positive', '-')} | {j.get('coherence_steered', '-')} |")
    if day_lines:
        lines += ["", "| row | arm | axis | words | asks | hedges/100w | commits | casual/100w | contractions/100w | repetition | truncated | judge toward + | judge coherence |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"] + day_lines
    report["parity"] = []
    for pair in args.parity:
        a, b = pair.split("=")
        ra, rb = (json.loads((screen / f"{n}.json").read_text()) for n in (a, b))
        n = same = 0
        for arm, tests in ra["arms"].items():
            for test, out in tests.items():
                other = {r["id"]: r["answer"] for r in rb["arms"].get(arm, {}).get(test, {}).get("results", [])}
                for r in out["results"]:
                    if r["id"] in other:
                        n += 1
                        same += r["answer"] == other[r["id"]]
        report["parity"].append({"static": a, "per_request": b, "answers": n, "byte_equal": same})
        lines.append(f"\nParity {a} vs {b}: {same}/{n} answers byte-equal")
    (screen / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
