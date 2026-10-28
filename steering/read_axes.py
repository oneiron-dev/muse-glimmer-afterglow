#!/usr/bin/env python3
"""Read every axis on the eval prompt sets, base vs adapter: the behaviour check on real prompts and the detector
data for per-prompt steering (CPU, after extract_states.py).

For each prompt the read is the answer-header state projected on the base-fitted axis direction, divided by the
layer's residual norm and averaged over the steering layers, then expressed in contrasts: (read - mean read of the
axis's negative pole) / separation, so 0 = the negative persona and 1 = the positive persona at the supplied token.
Per set and tag it reports the mean read; the shift adapter - base (paired by prompt) with a bootstrap interval;
and, where answer files label the prompts, the AUC of the abstention read for "this model abstained".
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fit_vectors import fit_axis, steering_layers  # noqa: E402


def auc(scores, labels):
    """P(score of a positive > score of a negative), ties count half."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    p = np.asarray(pos)[:, None]
    n = np.asarray(neg)[None, :]
    return float(((p > n).sum() + 0.5 * (p == n).sum()) / (p.size * n.size))


def paired_ci(diff, seed=20261014, n=10000):
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(diff), size=(n, len(diff)))
    means = diff[idx].mean(axis=1)
    return [round(float(np.percentile(means, 2.5)), 4), round(float(np.percentile(means, 97.5)), 4)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", required=True)
    ap.add_argument("--axes", default=str(HERE / "axes.json"))
    ap.add_argument("--eval-root", required=True, help="for the abstain rule (eval/simpleqa_gate.py)")
    ap.add_argument("--labels", action="append", default=[],
                    help="SET:TAG=answers.jsonl ({id, answer}); label = the answer abstains")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    sys.path.insert(0, str(Path(args.eval_root) / "eval"))
    import simpleqa_gate

    states = Path(args.states)
    spec = json.loads(Path(args.axes).read_text())
    tags = ["base"] + [p.name for p in sorted(states.iterdir()) if p.is_dir() and p.name != "base"]
    axes = {}
    for axis in spec["axes"]:
        f = states / "base" / f"{axis['name']}.npz"
        if f.exists():
            d = np.load(f)
            layers = steering_layers(d["pos"].shape[1])
            units, resid, sep = fit_axis(d["pos"], d["neg"], layers)
            neg_read = float(np.mean([(d["neg"][:, layer].astype(np.float32) @ units[layer]) / resid[layer] for layer in layers]))
            axes[axis["name"]] = (units, resid, layers, neg_read, float(np.mean(list(sep.values()))))

    def read(s, name):
        units, resid, layers, neg_read, sep = axes[name]
        r = np.mean([(s[:, layer].astype(np.float32) @ units[layer]) / resid[layer] for layer in layers], axis=0)
        return (r - neg_read) / sep

    labels = {}
    for item in args.labels:
        key, path = item.split("=", 1)
        rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
        labels[key] = {r["id"]: bool(simpleqa_gate.is_abstain(r["answer"])) for r in rows}
    sets = sorted({p.stem[4:] for p in (states / "base").glob("set-*.npz")} | {"check"})
    report = {"unit": "contrasts: 0 = negative persona, 1 = positive persona (answer-header read, mean over steering layers)",
              "axes": {n: {"separation_resid_fraction": round(v[4], 5)} for n, v in axes.items()}, "sets": {}}
    for set_name in sets:
        reads, ids = {}, None
        for tag in tags:
            f = states / tag / ("check.npz" if set_name == "check" else f"set-{set_name}.npz")
            if not f.exists():
                continue
            d = np.load(f)
            ids = list(d["ids"]) if "ids" in d else ids
            reads[tag] = {n: read(d["states"], n) for n in axes}
        entry = {}
        for n in axes:
            row = {tag: round(float(r[n].mean()), 4) for tag, r in reads.items()}
            for tag in reads:
                if tag != "base":
                    diff = reads[tag][n] - reads["base"][n]
                    row[f"shift_{tag}"] = round(float(diff.mean()), 4)
                    row[f"shift_{tag}_ci95"] = paired_ci(diff)
            entry[n] = row
        if "abstention" in axes and ids is not None:
            for tag in reads:
                lab = labels.get(f"{set_name}:{tag}")
                if lab:
                    y = [lab.get(str(i)) for i in ids]
                    keep = [k for k, v in enumerate(y) if v is not None]
                    entry["abstention"][f"auc_abstained_{tag}"] = auc([float(reads[tag]["abstention"][k]) for k in keep], [y[k] for k in keep])
                    entry["abstention"][f"abstain_rate_{tag}"] = round(sum(y[k] for k in keep) / max(1, len(keep)), 4)
        report["sets"][set_name] = entry
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report["sets"].get("gate", {}).get("abstention", {}), indent=1))
    print(json.dumps({s: {n: {k: v for k, v in r.items() if k.startswith("shift_") and not k.endswith("ci95")} for n, r in e.items()} for s, e in report["sets"].items()}, indent=1))


if __name__ == "__main__":
    main()
