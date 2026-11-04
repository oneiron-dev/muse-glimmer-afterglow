#!/usr/bin/env python3
"""A voice vector with its "I don't know" and "decisive" parts removed (CPU, from the voice job's states).

The behaviour check reads every voice's vector as also pushing toward "says it does not know less often" and "more
decisive". Here each steering layer's voice direction (re:dvx centered PCA, as fit_vectors.py) is made orthogonal to
that layer's abstention and decisive directions (Gram-Schmidt on the unit directions, then renormalised), and written
as a llama.cpp control vector at the same residual-norm scale: <vectors>/base/voice-<slug>-orth.gguf.
With --idk-states (base states at the answer header of the 200 gate questions, extract_states.py --prompt-set) and
--idk-answers (base's own answers to them, voice_sweep.py idk-base.json) it also removes the model's own "I don't
know" direction: per layer, the mean state of the questions base declines minus the mean of those it answers. That
variant is voice-<slug>-orth-idk.gguf. The voice keeps
the part of its direction that is not along those two axes; whether it still sounds like the voice, and whether the
model still says "I don't know" as often, is measured again by voice_sweep.py and voice_report.py, never assumed.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from fit_vectors import fit_axis, steering_layers, write_gguf


def orthogonal(unit, refs):
    v = unit.astype(np.float64).copy()
    basis = []
    for r in refs:
        r = r.astype(np.float64)
        for b in basis:
            r = r - (r @ b) * b
        basis.append(r / np.linalg.norm(r))
    for b in basis:
        v = v - (v @ b) * b
    kept = float(np.linalg.norm(v))
    return (v / kept).astype(np.float32), kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", required=True, help="the voice job's states dir (base/<axis>.npz)")
    ap.add_argument("--vectors", required=True, help="the voice job's vectors dir (writes base/voice-<slug>-orth.gguf)")
    ap.add_argument("--voice", action="append", required=True)
    ap.add_argument("--against", default="abstention,decisive")
    ap.add_argument("--idk-states", help="set-<name>.npz of the 200 gate questions on base (states, ids)")
    ap.add_argument("--idk-answers", help="idk-base.json: base's answers to the same questions")
    args = ap.parse_args()
    base = Path(args.states) / "base"

    def fit(name):
        d = np.load(base / f"{name}.npz")
        layers = steering_layers(d["pos"].shape[1])
        return fit_axis(d["pos"], d["neg"], layers), layers
    refs = {name: fit(name)[0][0] for name in args.against.split(",")}
    variant = "orth"
    if args.idk_states:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
        import simpleqa_gate
        d = np.load(args.idk_states)
        answers = {r["id"]: r["answer"] for r in json.loads(Path(args.idk_answers).read_text())["answers"]}
        declined = np.array([simpleqa_gate.is_abstain(answers[str(i)]) for i in d["ids"]])
        st = d["states"].astype(np.float32)
        layers = steering_layers(st.shape[1])
        diff = st[declined].mean(axis=0) - st[~declined].mean(axis=0)
        refs["idk"] = {layer: diff[layer] / np.linalg.norm(diff[layer]) for layer in layers}
        variant = "orth-idk"
        print(json.dumps({"idk_direction": {"declined": int(declined.sum()), "answered": int((~declined).sum())}}))
    report = {}
    for slug in args.voice:
        (units, resid, sep), layers = fit(f"voice-{slug}")
        orth, kept = {}, {}
        for layer in layers:
            orth[layer], kept[layer] = orthogonal(units[layer], [refs[n][layer] for n in refs])
        out = Path(args.vectors) / "base" / f"voice-{slug}-{variant}.gguf"
        write_gguf(out, orth, resid, layers)
        cos = {n: round(float(np.mean([units[layer] @ refs[n][layer] for layer in layers if 16 <= layer <= 42])), 4)
               for n in refs}
        report[slug] = {"gguf": str(out), "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
                        "against": list(refs), "cosine_before_l16_42": cos,
                        "kept_norm_l16_42": round(float(np.mean([kept[layer] for layer in layers if 16 <= layer <= 42])), 4)}
    (Path(args.vectors) / f"{variant}.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
