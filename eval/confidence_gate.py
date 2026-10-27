#!/usr/bin/env python3
"""Confidence gate per question: answer from the weights, or look the answer up in the notes.

For each forward probe the adapted model (arm B) answers without notes and reports its token confidence.
The gate answers from the weights when the chosen signal is at or above a threshold, else it looks the
answer up (arm D: the adapted model with the notes in the prompt). Three-way outcome per question:
  right_from_weights | looked_up (scored by the arm-D answer) | wrong_from_weights.
The threshold is calibrated on some worlds and tested on others (leave one world out), for each signal:
min_logprob (the weakest answer token) and first_logprob (the comparison). Calibration target: the
largest share answered from the weights while those answers stay at least --precision correct.
"""

import argparse
import json
from pathlib import Path

SIGNALS = ("min_logprob", "first_logprob", "mean_logprob")


def load_pairs(b_path, d_path):
    b = json.loads(Path(b_path).read_text())
    d = {r["id"]: r for r in json.loads(Path(d_path).read_text())["results"]}
    rows = []
    for r in b["results"]:
        if r["kind"] != "forward" or not r.get("conf"):
            continue
        rows.append({"id": r["id"], "b_pass": r["pass"], "d_pass": d[r["id"]]["pass"], **{s: r["conf"][s] for s in SIGNALS}})
    return rows


def outcome(rows, signal, tau):
    right_w = sum(1 for r in rows if r[signal] >= tau and r["b_pass"])
    wrong_w = sum(1 for r in rows if r[signal] >= tau and not r["b_pass"])
    looked = [r for r in rows if r[signal] < tau]
    return {"n": len(rows), "right_from_weights": right_w, "wrong_from_weights": wrong_w, "looked_up": len(looked),
            "looked_up_right": sum(r["d_pass"] for r in looked),
            "weights_share": round((right_w + wrong_w) / len(rows), 4) if rows else None,
            "weights_precision": round(right_w / (right_w + wrong_w), 4) if right_w + wrong_w else None}


def calibrate(rows, signal, precision):
    """Lowest threshold whose from-weights answers reach the precision target (most answered from the weights)."""
    best = None
    for tau in sorted({r[signal] for r in rows}):
        o = outcome(rows, signal, tau)
        if o["weights_precision"] is not None and o["weights_precision"] >= precision:
            best = tau
            break
    return best if best is not None else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", nargs="+", required=True, help="label=B_score_path,D_score_path per world")
    ap.add_argument("--precision", type=float, default=0.97)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    worlds = {}
    for spec in args.world:
        label, paths = spec.split("=", 1)
        b, d = paths.split(",")
        worlds[label] = load_pairs(b, d)
    report = {"precision_target": args.precision, "signals": {}}
    for signal in SIGNALS:
        folds = []
        for held in worlds:
            calib = [r for w, rows in worlds.items() if w != held or len(worlds) == 1 for r in rows]
            tau = calibrate(calib, signal, args.precision)
            folds.append({"test_world": held, "calibrated_on": [w for w in worlds if w != held] or [held],
                          "threshold": tau, "test": outcome(worlds[held], signal, tau)})
        total = {k: sum(f["test"][k] for f in folds) for k in ("n", "right_from_weights", "wrong_from_weights", "looked_up", "looked_up_right")}
        report["signals"][signal] = {"folds": folds, "total": total}
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
    for signal, r in report["signals"].items():
        t = r["total"]
        print(f"{signal:14s} right_from_weights {t['right_from_weights']}/{t['n']}  wrong_from_weights {t['wrong_from_weights']}  "
              f"looked_up {t['looked_up']} (right {t['looked_up_right']})  thresholds {[f['threshold'] for f in r['folds']]}")


if __name__ == "__main__":
    main()
