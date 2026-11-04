#!/usr/bin/env python3
"""Voices that keep the model's "I don't know": a counter-push along the model's own "I don't know" direction (CPU).

Every voice vector measured on 4 Nov makes Glimmer take the gate's "say that you do not know" exit far more often
(base 116 of 200; +22 to +65 from strength 0.01), and making the vectors orthogonal to that direction does not
change it: the push comes through the layers after the vector, not along it. So instead the request adds, beside
the voice at strength s, the model's own "I don't know" direction at a negative strength a, chosen so that on the
200 gate questions the "I don't know" count returns to base (voice_sweep.py --combos measures it, voice_report.py
applies the same rules, the known check included). The served voice is then one GGUF: s x voice + a x direction,
written per layer and divided by s, so --control-vector-scaled FILE:s adds exactly that sum.

  directions  write <vectors>/base/idk-direction.gguf (per layer: mean base state on the gate questions base declines
              minus the mean on those it answers, unit length x the layer's mean residual norm) and
              random-control.gguf (a seeded random direction, same norms: the control for "any push")
  bake        write <vectors>/base/voice-<slug>-v1.gguf = (s x voice + a x direction) / s for the chosen s and a
  dom         write <vectors>/base/voice-<slug>-dom.gguf: the voice fitted with re:dvx's diff_of_means estimator
              (the mean paired difference) instead of centered PCA. Over two contrast sets written from the same
              sentence, the mean differences agree (cosine 0.85-0.90 on layers 16-42) where the centered-PCA
              directions do not (0.16-0.41, some layers pointing against their own mean difference).
  slice       write <vectors>/base/voice-<slug>-L<lo>-<hi>.gguf: the raw voice on layers lo..hi only (a layer missing
              from a control-vector GGUF gets nothing), to test where along the depth the voice can be added
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "eval"))
from fit_vectors import GGUF_PY, fit_axis, steering_layers, write_gguf  # noqa: E402


def read_cvec(path):
    sys.path.insert(0, GGUF_PY)
    import gguf
    return {int(t.name.split(".")[1]): np.asarray(t.data, dtype=np.float32).reshape(-1)
            for t in gguf.GGUFReader(str(path)).tensors if t.name.startswith("direction.")}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def directions(args):
    import simpleqa_gate
    base = Path(args.states) / "base"
    ab = np.load(base / "abstention.npz")
    layers = steering_layers(ab["pos"].shape[1])
    _, resid, _ = fit_axis(ab["pos"], ab["neg"], layers)
    d = np.load(args.idk_states)
    answers = {r["id"]: r["answer"] for r in json.loads(Path(args.idk_answers).read_text())["answers"]}
    declined = np.array([simpleqa_gate.is_abstain(answers[str(i)]) for i in d["ids"]])
    st = d["states"].astype(np.float32)
    diff = st[declined].mean(axis=0) - st[~declined].mean(axis=0)
    units = {layer: diff[layer] / np.linalg.norm(diff[layer]) for layer in layers}
    rng = np.random.default_rng(20261104)
    rand = {}
    for layer in layers:
        r = rng.standard_normal(st.shape[2]).astype(np.float32)
        rand[layer] = r / np.linalg.norm(r)
    out = Path(args.vectors) / "base"
    write_gguf(out / "idk-direction.gguf", units, resid, layers)
    write_gguf(out / "random-control.gguf", rand, resid, layers)
    rep = {"idk-direction": {"gguf": str(out / "idk-direction.gguf"), "sha256": sha(out / "idk-direction.gguf"),
                             "declined": int(declined.sum()), "answered": int((~declined).sum()),
                             "separation_l16_42": round(float(np.mean([(diff[layer] @ units[layer]) / resid[layer]
                                                                       for layer in layers if 16 <= layer <= 42])), 4)},
           "random-control": {"gguf": str(out / "random-control.gguf"), "sha256": sha(out / "random-control.gguf"),
                              "seed": 20261104}}
    (Path(args.vectors) / "counter.json").write_text(json.dumps(rep, indent=1) + "\n")
    print(json.dumps(rep, indent=1))


def bake(args):
    out = Path(args.vectors) / "base"
    voice = read_cvec(out / f"voice-{args.voice}-raw.gguf")
    idk = read_cvec(out / "idk-direction.gguf")
    layers = sorted(voice)
    combined = {layer: (args.scale * voice[layer] + args.counter * idk[layer]) / args.scale for layer in layers}
    path = out / f"voice-{args.voice}-{args.tag}.gguf"
    write_gguf(path, {layer: combined[layer] for layer in layers}, {layer: 1.0 for layer in layers}, layers)
    rep = {"voice": args.voice, "tag": args.tag, "gguf": str(path), "sha256": sha(path), "scale": args.scale,
           "counter": args.counter,
           "formula": "direction.L = (scale x voice_raw.L + counter x idk_direction.L) / scale"}
    print(json.dumps(rep))
    f = Path(args.vectors) / "baked.json"
    doc = json.loads(f.read_text()) if f.exists() else {}
    doc[f"{args.voice}-{args.tag}"] = rep
    f.write_text(json.dumps(doc, indent=1) + "\n")


def diff_of_means(args):
    from redvx.wizard_protocol import fit_direction
    for slug in args.voice:
        d = np.load(Path(args.states) / "base" / f"voice-{slug}.npz")
        layers = steering_layers(d["pos"].shape[1])
        _, resid, _ = fit_axis(d["pos"], d["neg"], layers)
        diffs = d["pos"].astype(np.float32) - d["neg"].astype(np.float32)
        units = {layer: fit_direction(diffs[:, layer], "diff_of_means") for layer in layers}
        path = Path(args.vectors) / "base" / f"voice-{slug}-dom.gguf"
        write_gguf(path, units, resid, layers)
        sep = float(np.mean([(diffs[:, layer] @ units[layer]).mean() / resid[layer] for layer in layers if 16 <= layer <= 42]))
        print(json.dumps({"voice": slug, "gguf": str(path), "sha256": sha(path), "separation_l16_42": round(sep, 4)}))


def slice_layers(args):
    out = Path(args.vectors) / "base"
    voice = read_cvec(out / f"voice-{args.voice}-raw.gguf")
    lo, hi = (int(x) for x in args.layers.split(","))
    keep = [layer for layer in sorted(voice) if lo <= layer <= hi]
    path = out / f"voice-{args.voice}-L{lo}-{hi}.gguf"
    write_gguf(path, {layer: voice[layer] for layer in keep}, {layer: 1.0 for layer in keep}, keep)
    print(json.dumps({"voice": args.voice, "gguf": str(path), "sha256": sha(path), "layers": [keep[0], keep[-1]]}))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="step", required=True)
    a = sub.add_parser("directions")
    a.add_argument("--states", required=True)
    a.add_argument("--vectors", required=True)
    a.add_argument("--idk-states", required=True)
    a.add_argument("--idk-answers", required=True)
    b = sub.add_parser("bake")
    b.add_argument("--vectors", required=True)
    b.add_argument("--voice", required=True)
    b.add_argument("--scale", type=float, required=True)
    b.add_argument("--counter", type=float, required=True, help="strength of the I-don't-know direction (negative)")
    b.add_argument("--tag", default="v1", help="file tag: voice-<slug>-<tag>.gguf")
    m = sub.add_parser("dom")
    m.add_argument("--states", required=True)
    m.add_argument("--vectors", required=True)
    m.add_argument("--voice", action="append", required=True)
    c = sub.add_parser("slice")
    c.add_argument("--vectors", required=True)
    c.add_argument("--voice", required=True)
    c.add_argument("--layers", required=True, help="lo,hi")
    args = ap.parse_args()
    {"directions": directions, "bake": bake, "slice": slice_layers, "dom": diff_of_means}[args.step](args)


if __name__ == "__main__":
    main()
