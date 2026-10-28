#!/usr/bin/env python3
"""Behaviour check, a night step: read each axis on the base model and on each night's adapter, and say in plain
words how the night moved the model (GPU host, forward passes only; a few minutes for four adapters).

The axes are the re:dvx directions fitted on the base model's contrast pairs (from an extract_states.py output,
--axes-states). The probes are the everyday prompts in axes.json (check_probes: tasks, how-tos, decisions, factual
questions, the user's own facts; the headline) and the SimpleQA anchor pool (general-knowledge questions, disjoint
from the gate slice; reported under by_set), each read at the answer header with the default system text. A read is the state's
projection on the axis divided by the layer's residual norm, averaged over re:dvx's steering layers, in persona
contrasts (0 = the negative persona, 1 = the positive persona at the supplied token). Shift = adapter - base,
paired by probe, with a bootstrap 95% interval (10,000 resamples, seed 20261014). The label names the pole the
night moved toward when the interval excludes 0 and |shift| >= 0.25 contrasts, else "no clear change".

Writes <out>/<label>.json per adapter: the "behaviour_check" block for the night's eval.json.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extract_states import LastTokenCapture, build_prefix, find_layers, load_model, run_states  # noqa: E402
from fit_vectors import fit_axis, steering_layers  # noqa: E402
from read_axes import paired_ci  # noqa: E402

MIN_SHIFT = 0.25


def axis_refs(states_dir, axes):
    refs = {}
    for axis in axes:
        d = np.load(Path(states_dir) / "base" / f"{axis['name']}.npz")
        layers = steering_layers(d["pos"].shape[1])
        units, resid, sep = fit_axis(d["pos"], d["neg"], layers)
        neg_read = float(np.mean([(d["neg"][:, layer].astype(np.float32) @ units[layer]) / resid[layer] for layer in layers]))
        refs[axis["name"]] = (units, resid, layers, neg_read, float(np.mean(list(sep.values()))))
    return refs


def read(states, ref):
    units, resid, layers, neg_read, sep = ref
    r = np.mean([(states[:, layer].astype(np.float32) @ units[layer]) / resid[layer] for layer in layers], axis=0)
    return (r - neg_read) / sep


def axis_rows(axes, refs, base_states, states):
    rows = []
    for axis in axes:
        diff = read(states, refs[axis["name"]]) - read(base_states, refs[axis["name"]])
        shift = float(diff.mean())
        ci = paired_ci(diff)
        moved = (ci[0] > 0 or ci[1] < 0) and abs(shift) >= MIN_SHIFT
        label = (axis["check_labels"]["positive"] if shift > 0 else axis["check_labels"]["negative"]) if moved else "no clear change"
        rows.append({"axis": axis["name"], "shift": round(shift, 3), "ci95": ci, "n": int(len(diff)), "label": label,
                     "poles": [axis["positive_label"], axis["negative_label"]]})
    return rows


def block(axes, refs, base_by_set, adapted_by_set):
    """Headline axes on the everyday prompts; the same reads per probe set under by_set."""
    by_set = {name: {"n": int(len(base_by_set[name])), "axes": axis_rows(axes, refs, base_by_set[name], adapted_by_set[name])}
              for name in base_by_set}
    return {"version": 1, "unit": "persona contrasts (0 = negative persona, 1 = positive persona)",
            "rule": f"label when the 95% interval excludes 0 and |shift| >= {MIN_SHIFT}",
            "probes": {"headline": "everyday prompts", **{name: v["n"] for name, v in by_set.items()}},
            "axes": by_set["everyday prompts"]["axes"], "by_set": by_set}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", help="NF4 checkpoint (GPU mode)")
    ap.add_argument("--adapter", action="append", default=[], help="LABEL=PEFT_DIR (e.g. w1-d2=...)")
    ap.add_argument("--from-states", help="offline mode: an extract_states.py output with check.npz and set-anchors.npz "
                                          "per tag; every non-base tag gets a block")
    ap.add_argument("--axes", default=str(HERE / "axes.json"))
    ap.add_argument("--axes-states", required=True, help="extract_states.py output holding base/<axis>.npz")
    ap.add_argument("--anchors", default=str(HERE.parent / "night" / "data" / "simpleqa_anchor_prompts_v1.jsonl"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--deadline-s", type=float, default=540)
    ap.add_argument("--loader", choices=["auto", "unsloth", "transformers"], default="auto")
    args = ap.parse_args()
    t0 = time.time()
    deadline = t0 + args.deadline_s
    spec = json.loads(Path(args.axes).read_text())
    axes = spec["axes"]
    refs = axis_refs(args.axes_states, axes)
    anchors = [json.loads(line)["prompt"] for line in Path(args.anchors).read_text().splitlines() if line.strip()]
    texts = spec["check_probes"] + anchors
    n_everyday = len(spec["check_probes"])

    def split(states):
        return {"everyday prompts": states[:n_everyday], "general-knowledge questions": states[n_everyday:]}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.from_states:
        src = Path(args.from_states)

        def probes(tag):
            return {"everyday prompts": np.load(src / tag / "check.npz")["states"],
                    "general-knowledge questions": np.load(src / tag / "set-anchors.npz")["states"]}

        base_states = probes("base")
        for tag in [p.name for p in sorted(src.iterdir()) if p.is_dir() and p.name != "base"]:
            result = {"behaviour_check": {**block(axes, refs, base_states, probes(tag)), "adapter": tag,
                                          "label": tag, "source": str(src)}}
            (out / f"{tag}.json").write_text(json.dumps(result, indent=1) + "\n")
            print(json.dumps({"adapter": tag, **{r["axis"]: [r["shift"], r["ci95"], r["label"]] for r in result["behaviour_check"]["axes"]}}), flush=True)
        return
    model, tokenizer, device = load_model(args)
    capture = LastTokenCapture(find_layers(model))
    seqs = [tokenizer(build_prefix(tokenizer, t), add_special_tokens=False)["input_ids"] for t in texts]
    base_states = run_states(model, capture, seqs, args.batch_size, device, deadline)
    print(json.dumps({"base_read_s": round(time.time() - t0, 1)}), flush=True)
    from peft import PeftModel
    peft_model = None
    for item in args.adapter:
        label, path = item.split("=", 1)
        if peft_model is None:
            peft_model = PeftModel.from_pretrained(model, path, adapter_name=label)
        else:
            peft_model.load_adapter(path, adapter_name=label)
        peft_model.set_adapter(label)
        peft_model.eval()
        states = run_states(peft_model, capture, seqs, args.batch_size, device, deadline)
        result = {"behaviour_check": {**block(axes, refs, split(base_states), split(states)), "adapter": path,
                                      "label": label, "seconds_total": round(time.time() - t0, 1)}}
        (out / f"{label}.json").write_text(json.dumps(result, indent=1) + "\n")
        print(json.dumps({"adapter": label, **{r["axis"]: [r["shift"], r["label"]] for r in result["behaviour_check"]["axes"]}}), flush=True)


if __name__ == "__main__":
    main()
