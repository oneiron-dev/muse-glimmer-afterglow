#!/usr/bin/env python3
"""Summarize sweep score files into one table, with paired statistics against the base arms.

Learned: per-fact forward pass rate, B-A (and C-A, B-C) with a paired cluster bootstrap over facts.
Forgot: general slice and, when scored, the 180 MMLU-Pro + 20 tool slice: exact McNemar + Newcombe CI.
Writes <out>.json (all numbers) and <out>.md (the table and the per-fact misses).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stats  # noqa: E402

GENERAL = ("general",)
FORGET = ("forget_mmlu", "forget_tool")


def load(path):
    return json.loads(Path(path).read_text())


def per_fact_rate(rec):
    rates = {}
    for r in rec["results"]:
        if r["kind"] == "forward":
            rates.setdefault(r["fact_id"], []).append(r["pass"])
    return {k: sum(v) / len(v) for k, v in rates.items()}


def passes(rec, kinds):
    return {r["id"]: r["pass"] for r in rec["results"] if r["kind"] in kinds}


def frac(rec, kinds):
    p = passes(rec, kinds)
    return f"{sum(p.values())}/{len(p)}" if p else "-"


def row(label, rec, base_a, base_c):
    s = rec["summary"]
    out = {"label": label, "arm": rec["arm"], "facts_learned": f"{s['facts_learned']}/{s['facts']}",
           "forward": frac(rec, ("forward",)), "reverse": frac(rec, ("reverse",)), "preference": frac(rec, ("preference",)),
           "procedure": frac(rec, ("procedure",)), "general": frac(rec, GENERAL), "forget_slice": frac(rec, FORGET),
           "truncated": sum(1 for r in rec["results"] if r["truncated"])}
    if rec is not base_a:
        out["fact_delta_vs_A"] = stats.paired_cluster_bootstrap(per_fact_rate(base_a), per_fact_rate(rec))
        out["general_vs_A"] = stats.paired_proportions(passes(base_a, GENERAL), passes(rec, GENERAL))
        fb, fa = passes(base_a, FORGET), passes(rec, FORGET)
        if fb and fa:
            out["forget_vs_A"] = stats.paired_proportions(fb, fa)
            tools_b, tools_a = passes(base_a, ("forget_tool",)), passes(rec, ("forget_tool",))
            out["new_tool_failures"] = sorted(k for k in tools_b if tools_b[k] and not tools_a.get(k, False))
    if base_c is not None and rec is not base_c and rec["arm"] in ("B", "D"):
        out["fact_delta_vs_C"] = stats.paired_cluster_bootstrap(per_fact_rate(base_c), per_fact_rate(rec))
    return out


def misses(rec, base_c):
    c_rate = per_fact_rate(base_c) if base_c else {}
    by_fact = {}
    for r in rec["results"]:
        if r["kind"] == "forward" and not r["pass"]:
            by_fact.setdefault(r["fact_id"], []).append({"probe": r["id"], "answer": r["answer"][:160]})
    return [{"fact_id": f, "C_rate": round(c_rate.get(f, 0), 3), "B_rate": round(per_fact_rate(rec)[f], 3), "misses": m}
            for f, m in sorted(by_fact.items())]


def fmt_ci(d):
    return f"{d['delta']:+.3f} [{d['ci95'][0]:+.3f}, {d['ci95'][1]:+.3f}]" if d and d.get("delta") is not None else ""


def fmt_pp(p):
    if not p:
        return ""
    return f"{p['after_minus_before'] * 100:+.1f} pt [{p['newcombe95'][0] * 100:+.1f}, {p['newcombe95'][1] * 100:+.1f}], lost {p['lost']} gained {p['gained']}, McNemar p={p['mcnemar_p']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-a", required=True)
    ap.add_argument("--base-c")
    ap.add_argument("--scores", nargs="+", required=True, help="label=path pairs")
    ap.add_argument("--misses-for", nargs="*", default=[], help="labels whose per-fact misses are listed")
    ap.add_argument("--out", required=True, help="output path without extension")
    args = ap.parse_args()
    base_a = load(args.base_a)
    base_c = load(args.base_c) if args.base_c else None
    rows = [row("base", base_a, base_a, base_c)]
    if base_c:
        rows.append(row("base", base_c, base_a, base_c))
    recs = {}
    for pair in args.scores:
        label, path = pair.split("=", 1)
        recs[label] = load(path)
        rows.append(row(label, recs[label], base_a, base_c))
    miss = {label: misses(recs[label], base_c) for label in args.misses_for if label in recs}
    Path(args.out + ".json").write_text(json.dumps({"rows": rows, "misses": miss}, indent=1) + "\n")
    cols = ["label", "arm", "facts_learned", "forward", "reverse", "preference", "procedure", "general", "forget_slice", "truncated"]
    lines = ["| " + " | ".join(cols + ["per-fact pass vs A (95% CI)", "per-fact pass vs C", "forget slice vs A"]) + " |",
             "|" + "---|" * (len(cols) + 3)]
    for r in rows:
        lines.append("| " + " | ".join(str(r[c]) for c in cols) + f" | {fmt_ci(r.get('fact_delta_vs_A'))} | "
                     f"{fmt_ci(r.get('fact_delta_vs_C'))} | {fmt_pp(r.get('forget_vs_A'))} |")
    for label, m in miss.items():
        lines += ["", f"Misses for {label} (forward probes that failed; C = notes control rate):"]
        for f in m:
            lines.append(f"- {f['fact_id']} (B {f['B_rate']:.2f} vs C {f['C_rate']:.2f}): " +
                         "; ".join(f"{x['probe']}: {x['answer']!r}" for x in f["misses"]))
    Path(args.out + ".md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
