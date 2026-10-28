#!/usr/bin/env python3
"""Fit re:dvx control vectors from captured states, export llama.cpp control-vector GGUFs, read the behaviour check.

CPU only. Input: the extract_states.py output (<states>/<tag>/<axis>.npz, check.npz).
Per axis and layer: re:dvx centered PCA on the paired differences (sign by majority), then re:dvx common-component
removal against three other axes ("proj"); "raw" keeps the unprojected direction. Steering layers follow re:dvx's
scaled_repeng mask (depth 52 -> 16..42 and 51).

Exports, per axis, variant and model tag:
  <out>/<tag>/<axis>-<variant>.redvx.json  re:dvx WriteVector (unit directions, direction_scales = residual norms)
  <out>/<tag>/<axis>-<variant>.gguf        llama.cpp control vector: direction.L = unit_L * residual_norm_L, so
                                           `--control-vector-scaled FILE:s` adds s x the layer's mean residual norm
  <out>/manifest.json                      sha256, layers, residual norms, separation, cosine base vs adapter
  <out>/check.json                         behaviour check: each axis read on the check probes, base vs adapter
Separation = the mean paired difference projected on the direction, as a fraction of the residual norm: the size
of the full persona contrast, the natural unit for a steering scale.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

GGUF_PY = "/data/meta-night/llama.cpp/gguf-py"


def steering_layers(depth):
    from redvx.wizard_protocol import Protocol
    return Protocol(target="cuda", layer_policy="scaled_repeng").steering_layers(depth)


def fit_axis(pos, neg, layers):
    from redvx.wizard_protocol import fit_direction
    diffs = pos.astype(np.float32) - neg.astype(np.float32)
    units, resid, sep = {}, {}, {}
    for layer in layers:
        u = fit_direction(diffs[:, layer], "centered_pca")
        units[layer] = u
        norms = np.linalg.norm(np.concatenate([pos[:, layer], neg[:, layer]]).astype(np.float32), axis=1)
        resid[layer] = float(norms.mean())
        sep[layer] = float((diffs[:, layer] @ u).mean() / resid[layer])
    return units, resid, sep


def project(units, refs, layers):
    from redvx.wizard_protocol import remove_common
    out, kept = {}, {}
    for layer in layers:
        out[layer], kept[layer] = remove_common(units[layer], [r[layer] for r in refs])
    return out, kept


def write_gguf(path, units, resid, layers):
    sys.path.insert(0, GGUF_PY)
    import gguf
    writer = gguf.GGUFWriter(str(path), "controlvector")
    writer.add_string("controlvector.model_hint", "muse-glimmer")
    writer.add_uint32("controlvector.layer_count", len([layer for layer in layers if layer >= 1]))
    for layer in layers:
        if layer >= 1:  # llama.cpp keeps no control for layer 0
            writer.add_tensor(f"direction.{layer}", (units[layer] * resid[layer]).astype(np.float32))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()


def write_redvx(path, axis, units, resid, diff_scales, kept, layers, n_components):
    from redvx.vectors import VectorMetadata, WriteVector
    meta = VectorMetadata(model_id="muse-glimmer-30b-nf4", axis_name=axis["name"],
                          positive_label=axis["positive_label"], negative_label=axis["negative_label"],
                          training_method="centered_pca", layer_ids=tuple(layers),
                          diff_scales={layer: diff_scales[layer] for layer in layers},
                          residual_norms={layer: resid[layer] for layer in layers},
                          direction_scales={layer: resid[layer] for layer in layers},
                          projection_common_fractions={layer: float(1 - kept.get(layer, 1.0) ** 2) for layer in layers},
                          projection_residual_fractions={layer: 1.0 for layer in layers},
                          projection_n_components=n_components)
    WriteVector(model_type="muse_glimmer", directions={layer: units[layer] for layer in layers}, metadata=meta).save(path)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", required=True)
    ap.add_argument("--axes", default=str(Path(__file__).resolve().parent / "axes.json"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    spec = json.loads(Path(args.axes).read_text())
    states, out = Path(args.states), Path(args.out)
    tags = [p.name for p in sorted(states.iterdir()) if p.is_dir()]
    tags = ["base"] + [t for t in tags if t != "base"]
    manifest = {"tags": tags, "axes": {}}
    fitted = {}
    for tag in tags:
        for axis in spec["axes"]:
            f = states / tag / f"{axis['name']}.npz"
            if not f.exists():
                continue
            d = np.load(f)
            depth = d["pos"].shape[1]
            layers = steering_layers(depth)
            units, resid, sep = fit_axis(d["pos"], d["neg"], layers)
            diff_scales = {layer: float(np.linalg.norm(d["pos"][:, layer].astype(np.float32) - d["neg"][:, layer].astype(np.float32), axis=1).mean()) for layer in layers}
            fitted[(tag, axis["name"])] = {"units": units, "resid": resid, "sep": sep, "diff": diff_scales, "layers": layers, "n": len(d["pos"])}
    for (tag, name), fit in fitted.items():
        axis = next(a for a in spec["axes"] if a["name"] == name)
        others = [a["name"] for a in spec["axes"] if a["name"] != name and (tag, a["name"]) in fitted][:3]
        variants = {"raw": (fit["units"], {}, 0)}
        if len(others) == 3:
            proj, kept = project(fit["units"], [fitted[(tag, o)]["units"] for o in others], fit["layers"])
            variants["proj"] = (proj, kept, 1)
        tdir = out / tag
        tdir.mkdir(parents=True, exist_ok=True)
        entry = manifest["axes"].setdefault(name, {})
        for variant, (units, kept, n_comp) in variants.items():
            gg = tdir / f"{name}-{variant}.gguf"
            rv = tdir / f"{name}-{variant}.redvx.json"
            write_gguf(gg, units, fit["resid"], fit["layers"])
            write_redvx(rv, axis, units, fit["resid"], fit["diff"], kept, fit["layers"], n_comp)
            sep = float(np.mean([(units[layer] @ fit["units"][layer]) * fit["sep"][layer] for layer in fit["layers"]]))
            entry[f"{tag}/{variant}"] = {"gguf": str(gg), "gguf_sha256": sha256(gg), "redvx": str(rv),
                                         "pairs": fit["n"], "layers": fit["layers"], "separation": round(sep, 4),
                                         "refs": others if variant == "proj" else [],
                                         "kept_fraction": round(float(np.mean(list(kept.values()))), 4) if kept else 1.0,
                                         "residual_norm_mean": round(float(np.mean(list(fit["resid"].values()))), 2)}
        if tag != "base" and ("base", name) in fitted:
            b = fitted[("base", name)]
            entry[f"cosine_base_vs_{tag}"] = round(float(np.mean([b["units"][layer] @ fit["units"][layer] for layer in fit["layers"]])), 4)
    # behaviour check: every base-fitted raw axis read on the check probes of each tag
    check = {"unit": "projection on the base axis as a fraction of the residual norm, mean over steering layers and probes; shift_in_contrasts = (tag - base) / the axis separation", "axes": {}}
    base_check = states / "base" / "check.npz"
    if base_check.exists():
        reads = {t: np.load(states / t / "check.npz")["states"].astype(np.float32) for t in tags if (states / t / "check.npz").exists()}
        for axis in spec["axes"]:
            if ("base", axis["name"]) not in fitted:
                continue
            b = fitted[("base", axis["name"])]
            row = {}
            for t, s in reads.items():
                row[t] = round(float(np.mean([(s[:, layer] @ b["units"][layer]) / b["resid"][layer] for layer in b["layers"]])), 5)
            sep = float(np.mean(list(b["sep"].values())))
            row["separation"] = round(sep, 5)
            for t in reads:
                if t != "base":
                    row[f"shift_in_contrasts_{t}"] = round((row[t] - row["base"]) / sep, 4) if sep else None
            check["axes"][axis["name"]] = row
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    (out / "check.json").write_text(json.dumps(check, indent=1) + "\n")
    print(json.dumps({"axes": {k: {kk: (vv["separation"] if isinstance(vv, dict) else vv) for kk, vv in v.items()} for k, v in manifest["axes"].items()}, "check": check["axes"]}, indent=1))


if __name__ == "__main__":
    main()
