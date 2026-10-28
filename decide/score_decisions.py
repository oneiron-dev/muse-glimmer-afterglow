#!/usr/bin/env python3
"""Score decision arms from readout files (decide/readout.py output), per kind, and the route decision end to end.

Per kind and split: accuracy, ECE (15 bins, top-label confidence), wrong answers at p >= 0.9, latency p50/p95, label
mass; with a reasoned readout ("extended"), the same for it and for auto-extend at a threshold. Accuracy against a
reference arm uses a paired cluster bootstrap (clusters = facts for route, rows otherwise; 10,000 resamples, seed
20261014).

Route end to end: the chosen note goes through the D5 value compare (decide/notes_agreement_gate.py): agree ->
weights, mismatch -> notes (scored by arm D), 0 -> ask. Reported on the same probes beside always-D (every answer
from arm D), D5-app and D5-oracle, with score = (right - wrong) / n.
"""

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "decide"))
import notes_agreement_gate as gate  # noqa: E402

SEED = 20261014
DATA = ROOT / "decide" / "data"


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def ece(rows, key="p", right="right", bins=15):
    if not rows:
        return None
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [r for r in rows if (lo < r[key] <= hi) or (b == 0 and r[key] == 0)]
        if sel:
            total += len(sel) / len(rows) * abs(sum(r[right] for r in sel) / len(sel) - sum(r[key] for r in sel) / len(sel))
    return round(total, 4)


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def one_pass_metrics(rows):
    return {"n": len(rows), "accuracy": round(sum(r["right"] for r in rows) / len(rows), 4) if rows else None,
            "ece": ece(rows), "wrong_at_p90": sum(1 for r in rows if r["p"] >= 0.9 and not r["right"]),
            "p50_ms": pct([r["ms"] for r in rows], 0.5), "p95_ms": pct([r["ms"] for r in rows], 0.95),
            "label_mass_p50": round(statistics.median(r["label_mass"] for r in rows), 4) if rows else None}


def extended_metrics(rows):
    ext = [r["extended"] for r in rows if "extended" in r]
    if not ext:
        return None
    return {"n": len(ext), "accuracy": round(sum(e["right"] for e in ext) / len(ext), 4), "ece": ece(ext),
            "wrong_at_p90": sum(1 for e in ext if e["p"] >= 0.9 and not e["right"]),
            "p50_ms": pct([e["ms"] for e in ext], 0.5), "p95_ms": pct([e["ms"] for e in ext], 0.95),
            "reasoning_tokens_p50": pct([e["reasoning_tokens"] or 0 for e in ext], 0.5)}


def auto_extend(rows, tau):
    """D2 offline: one pass when p >= tau, else the reasoned readout (rows must carry 'extended')."""
    out = []
    for r in rows:
        if r["p"] >= tau or "extended" not in r:
            out.append({"right": r["right"], "p": r["p"], "ms": r["ms"], "escalated": False})
        else:
            e = r["extended"]
            out.append({"right": e["right"], "p": e["p"], "ms": r["ms"] + e["ms"], "escalated": True})
    return {"tau": tau, "n": len(out), "accuracy": round(sum(o["right"] for o in out) / len(out), 4) if out else None,
            "escalation_rate": round(sum(o["escalated"] for o in out) / len(out), 4) if out else None,
            "p50_ms": pct([o["ms"] for o in out], 0.5), "p95_ms": pct([o["ms"] for o in out], 0.95)}


def cluster_of(row):
    return f"{row['world']}:{row.get('fact_id') or row['id']}"


def paired_bootstrap(a, b, rows_by_id, n=10000, seed=SEED):
    """Accuracy difference a - b over shared ids, resampling clusters (facts) with replacement."""
    ids = sorted(set(a) & set(b))
    clusters = {}
    for i in ids:
        clusters.setdefault(cluster_of(rows_by_id[i]), []).append(i)
    keys = sorted(clusters)
    diff = sum(a[i]["right"] - b[i]["right"] for i in ids) / len(ids)
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        sample = [k for _ in keys for k in [keys[rng.randrange(len(keys))]]]
        num = sum(a[i]["right"] - b[i]["right"] for k in sample for i in clusters[k])
        den = sum(len(clusters[k]) for k in sample)
        stats.append(num / den)
    stats.sort()
    return {"n": len(ids), "clusters": len(keys), "diff": round(diff, 4),
            "ci95": [round(stats[int(0.025 * n)], 4), round(stats[int(0.975 * n)], 4)]}


FALLBACK_TAUS = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99)


def fallback(rows, results, rows_by_id):
    """Route with the look-up fallback: below tau the decision is not trusted and the answer comes from arm D (all
    notes in the prompt). tau is chosen on the calibration split (best score) and applied to the test split."""
    p = {r["id"]: r["p"] for r in results}
    split = {i: rows_by_id[i]["split"] for i in p}

    def at(tau, which):
        sel = []
        for x in rows:
            rid = f"route:{x['world']}:{x['id']}"
            if split[rid] != which:
                continue
            sel.append({**x, "route": "notes"} if p[rid] < tau else x)
        return gate.score_rows(sel)["lookup_scored_by_d"] if sel else None
    grid = {str(t): {s: at(t, s) for s in ("calibration", "test")} for t in FALLBACK_TAUS}
    calib = {t: g["calibration"]["score"] for t, g in grid.items() if g["calibration"]}
    chosen = max(calib, key=lambda t: (calib[t], -float(t))) if calib else None
    return {"rule": "p < tau -> arm D (look up with all notes); tau = best calibration score", "grid": grid,
            "chosen_tau": chosen, "test_at_chosen": grid[chosen]["test"] if chosen else None}


def subsets(rows_by_id):
    """The same row groups for every route arm: split (test = w4, calibration = w1-w3) x probe kind."""
    def split(x):
        return rows_by_id[f"route:{x['world']}:{x['id']}"]["split"]
    out = [("all", lambda x: True), ("forward", lambda x: x["kind"] == "forward"),
           ("never_told", lambda x: x["kind"] == "never_told")]
    for sp in ("test", "calibration"):
        out += [(sp, lambda x, sp=sp: split(x) == sp),
                (f"{sp}_forward", lambda x, sp=sp: split(x) == sp and x["kind"] == "forward"),
                (f"{sp}_never_told", lambda x, sp=sp: split(x) == sp and x["kind"] == "never_told")]
    return out


def always_d_score(rows):
    right = sum(x["d_right"] for x in rows)
    wrong = sum(not x["d_right"] and not x["d_abstain"] for x in rows)
    return {"n": len(rows), "right": right, "wrong": wrong, "asked": 0,
            "score": round((right - wrong) / len(rows), 4) if rows else None}


def route_end_to_end(results, rows_by_id, compare="strict"):
    """Route arms on the same probes. decision: the chosen note through the D5 compare. decision_lookup: no weights
    answer; a chosen note -> arm D, 0 -> ask. always_d: every answer from arm D. d5_app / d5_oracle: the baselines."""
    picks, worlds = {}, set()
    for r in results:
        row = rows_by_id[r["id"]]
        worlds.add(row["world"])
        picks[r["id"]] = None if r["pick"] == "0" else row["note_ids"][int(r["pick"]) - 1]
    ids = {r["id"] for r in results}
    runs = gate.runs_root(None)

    def rows_for(mode):
        rows = [x for w in sorted(worlds) for x in gate.world_rows(w, runs, mode, compare, picks)]
        return [x for x in rows if f"route:{x['world']}:{x['id']}" in ids]
    decision = rows_for("decision")
    arms = {"decision": decision,
            "decision_lookup": [{**x, "route": "ask" if picks[f"route:{x['world']}:{x['id']}"] is None else "notes"}
                                for x in decision],
            "d5_app": rows_for("app"), "d5_oracle": rows_for("oracle")}
    out = {}
    for name, rows in arms.items():
        out[name] = {sub: gate.score_rows([x for x in rows if sel(x)]) for sub, sel in subsets(rows_by_id)}
    out["always_d"] = {sub: always_d_score([x for x in decision if sel(x)]) for sub, sel in subsets(rows_by_id)}
    out["decision_fallback"] = fallback(decision, results, rows_by_id)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="append", required=True, help="name=readout.jsonl (the first arm is the reference)")
    ap.add_argument("--tau", type=float, nargs="*", default=[], help="auto-extend thresholds to report (rows with 'extended')")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rows_by_id = {r["id"]: r for p in sorted(DATA.glob("*-test.jsonl")) + sorted(DATA.glob("*-calibration.jsonl"))
                  for r in read_jsonl(p)}
    arms = {a.split("=", 1)[0]: {r["id"]: r for r in read_jsonl(a.split("=", 1)[1])} for a in args.arm}
    ref = next(iter(arms))
    report = {"arms": {}}
    for name, res in arms.items():
        by = {}
        for r in res.values():
            row = rows_by_id[r["id"]]
            by.setdefault(f"{row['kind']}/{row['split']}", []).append(r)
        report["arms"][name] = {}
        for key, rs in sorted(by.items()):
            entry = {"one_pass": one_pass_metrics(rs), "reasoned": extended_metrics(rs),
                     "auto_extend": [auto_extend(rs, t) for t in args.tau]}
            if name != ref:
                entry["vs_" + ref] = paired_bootstrap({r["id"]: r for r in rs}, arms[ref], rows_by_id)
            report["arms"][name][key] = entry
        route = [r for r in res.values() if rows_by_id[r["id"]]["kind"] == "route"]
        if route:
            report["arms"][name]["route_end_to_end"] = route_end_to_end(route, rows_by_id)
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
    for name, a in report["arms"].items():
        for key, e in a.items():
            if key == "route_end_to_end":
                fb = e.get("decision_fallback")
                if fb and fb["chosen_tau"] is not None:
                    t = fb["test_at_chosen"]
                    print(f"{name:8s} route e2e fallback tau {fb['chosen_tau']} (calibration) test: "
                          + (f"right {t['right']} wrong {t['wrong']} score {t['score']} asked {t['asked']}" if t else "no test rows"))
                for arm, subs in e.items():
                    if arm == "decision_fallback":
                        continue
                    for sub in ("test_forward", "test_never_told", "test", "all"):
                        s = subs.get(sub)
                        if not s or not s["n"]:
                            continue
                        d = s.get("lookup_scored_by_d", s)
                        print(f"{name:8s} route e2e {arm:16s} {sub:16s} n {s['n']:3d}  right {d['right']} wrong {d['wrong']} "
                              f"asked {d['asked']} score {d['score']}"
                              + (f"  precision {s['routing_precision']} recall {s['routing_recall']}" if "routing_precision" in s else ""))
                continue
            o = e["one_pass"]
            print(f"{name:8s} {key:24s} n {o['n']:4d} acc {o['accuracy']} ece {o['ece']} wrong@0.9 {o['wrong_at_p90']} "
                  f"p50 {o['p50_ms']} ms p95 {o['p95_ms']} ms mass {o['label_mass_p50']}"
                  + (f"  vs {ref} {e['vs_' + ref]['diff']} {e['vs_' + ref]['ci95']}" if "vs_" + ref in e else ""))


if __name__ == "__main__":
    main()
