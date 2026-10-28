#!/usr/bin/env python3
"""Write the two screen plans for one serving pause.

J2a (abstention): a baseline row without a vector (gate + never-told on both arms; forward + forget on the
adapter; every day axis's test prompts on both arms, the day baseline), then one row per abstention scale.
J2b (day axes): +m for every axis, then +2m, then -m (m from the calibration), day test prompts on both arms.
Abstention is scored on the gate slice and on the same questions bare (gate_bare).
"""

import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vectors", required=True, help="fit_vectors.py output dir (manifest.json)")
    ap.add_argument("--variant", default="proj")
    ap.add_argument("--abstention-scales", required=True, help="comma list")
    ap.add_argument("--day-m", required=True, help="axis=m,... or one m for every day axis")
    ap.add_argument("--layers", default="", help="FIRST,LAST: llama.cpp layer range for every vector row (default: all in the file)")
    ap.add_argument("--abstention-layer-variants", default="",
                    help="extra abstention rows SCALE@FIRST,LAST;... (e.g. the full re:dvx mask with layer 51)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    manifest = json.loads((Path(args.vectors) / "manifest.json").read_text())

    def gguf(axis):
        entry = manifest["axes"][axis]
        key = f"base/{args.variant}" if f"base/{args.variant}" in entry else "base/raw"
        return entry[key]["gguf"]

    layers = [int(x) for x in args.layers.split(",")] if args.layers else None
    day_axes = [a for a in manifest["axes"] if a != "abstention"]
    day_tests = [f"day:{a}" for a in day_axes]
    if "=" in args.day_m:
        m = {k: float(v) for k, v in (kv.split("=") for kv in args.day_m.split(","))}
    else:
        m = {a: float(args.day_m) for a in day_axes}
    j2a = [{"name": "baseline", "cvecs": [],
            "tests_by_arm": {"base": ["gate", "gate_bare", "never_told"] + day_tests,
                             "R2": ["gate", "gate_bare", "never_told", "forward", "forget"] + day_tests}}]
    for s in [float(x) for x in args.abstention_scales.split(",")]:
        j2a.append({"name": f"abstention{s:+g}", "cvecs": [[gguf("abstention"), s]], "cvec_layers": layers,
                    "tests_by_arm": {"base": ["gate", "gate_bare", "never_told"],
                                     "R2": ["gate", "gate_bare", "never_told", "forward", "forget"]}})
    for spec in [v for v in args.abstention_layer_variants.split(";") if v]:
        s, rng = spec.split("@")
        lr = [int(x) for x in rng.split(",")]
        j2a.append({"name": f"abstention{float(s):+g}-L{lr[0]}-{lr[1]}", "cvecs": [[gguf("abstention"), float(s)]],
                    "cvec_layers": lr, "tests_by_arm": {"base": ["gate", "gate_bare", "never_told"],
                                                        "R2": ["gate", "gate_bare", "never_told", "forward"]}})
    j2b = []
    for k in (1, 2, -1):  # by priority: a deadline cuts the -m rows first
        for a in day_axes:
            j2b.append({"name": f"{a}{k * m[a]:+g}", "cvecs": [[gguf(a), k * m[a]]], "cvec_layers": layers,
                        "tests": [f"day:{a}"]})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan-j2a.json").write_text(json.dumps(j2a, indent=1) + "\n")
    (out / "plan-j2b.json").write_text(json.dumps(j2b, indent=1) + "\n")
    print(json.dumps({"j2a": [r["name"] for r in j2a], "j2b": [r["name"] for r in j2b]}))


if __name__ == "__main__":
    main()
