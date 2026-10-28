#!/usr/bin/env python3
"""The night-delta vector: what the night's adapter added to the residual stream on general questions, as a control
vector that takes it back out at serving time (CPU, from extract_states.py output).

On a prompt set (default: the SimpleQA anchor pool, disjoint from the gate slice) the answer-header state of the
adapted model minus the base's, averaged over prompts, is the night's shift d_L at layer L. Each layer gets the
increment it adds itself, d_L - d_(L-1), with the sign flipped, so applying the vector at scale 1 on every layer
in the range moves the adapted model's mean general-question state back to the base's. Writes a llama.cpp
control-vector GGUF (layers FIRST..LAST) and a JSON with each layer's shift as a fraction of the residual norm.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fit_vectors import GGUF_PY, sha256  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", required=True)
    ap.add_argument("--tag", default="w1")
    ap.add_argument("--set", default="anchors")
    ap.add_argument("--layers", default="16,42", help="FIRST,LAST inclusive")
    ap.add_argument("--out", required=True, help="output GGUF path")
    args = ap.parse_args()
    first, last = (int(x) for x in args.layers.split(","))
    base = np.load(Path(args.states) / "base" / f"set-{args.set}.npz")["states"].astype(np.float32)
    adapted = np.load(Path(args.states) / args.tag / f"set-{args.set}.npz")["states"].astype(np.float32)
    d = (adapted - base).mean(axis=0)  # [layers, hidden]
    resid = np.linalg.norm(base, axis=2).mean(axis=0)
    sys.path.insert(0, GGUF_PY)
    import gguf
    writer = gguf.GGUFWriter(args.out, "controlvector")
    writer.add_string("controlvector.model_hint", "muse-glimmer")
    writer.add_uint32("controlvector.layer_count", last - first + 1)
    report = {"set": args.set, "tag": args.tag, "layers": [first, last], "n": int(base.shape[0]), "per_layer": {}}
    for layer in range(first, last + 1):
        inc = -(d[layer] - (d[layer - 1] if layer > first else 0.0))
        writer.add_tensor(f"direction.{layer}", inc.astype(np.float32))
        report["per_layer"][layer] = {"shift_resid_fraction": round(float(np.linalg.norm(d[layer]) / resid[layer]), 4),
                                      "increment_resid_fraction": round(float(np.linalg.norm(inc) / resid[layer]), 4)}
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    report["gguf"] = args.out
    report["gguf_sha256"] = sha256(args.out)
    Path(args.out).with_suffix(".json").write_text(json.dumps(report, indent=1) + "\n")
    pl = report["per_layer"]
    print(json.dumps({"n": report["n"], "shift@first": pl[first], "shift@mid": pl[(first + last) // 2], "shift@last": pl[last]}))


if __name__ == "__main__":
    main()
