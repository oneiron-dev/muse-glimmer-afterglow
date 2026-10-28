#!/usr/bin/env python3
"""Capture Glimmer's residual-stream states for the re:dvx contrast pairs (GPU host, one short job).

Pairs follow the re:dvx single-token protocol: the two poles differ only in a persona sentence in the system
prompt; both share the user prompt, the answer header that forces the user channel (as the night eval prompts
the model) and one supplied answer token drawn from the re:dvx token bank (seeded per prompt, shared by both
poles). The state read is each decoder layer's output at that last token, the point where llama.cpp adds a
control vector (build_cvec after the FFN residual add).

Check probes are plain prompts with the default system text, read at the answer header: the "behaviour check"
reads them on the base model and on each adapter.

Per model tag (base, then each adapter) it writes <out>/<tag>/<axis>.npz (pos, neg: [pairs, layers, hidden]
float16) + <axis>.pairs.json, and <out>/<tag>/check.npz. A hard deadline stops the job between batches;
status.json says what finished.
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "steering"))
from score_peft import (ANSWER_PREFIX, EVAL_DATE, KNOWLEDGE_CUTOFF, REASONING_STRENGTH,  # noqa: E402
                        build_prefix, system_with_hint)


def render_pair_side(tokenizer, persona, prompt):
    messages = [{"role": "system", "content": system_with_hint(persona)}, {"role": "user", "content": prompt}]
    rendered = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False,
                                             reasoning_strength=REASONING_STRENGTH, knowledge_cutoff=KNOWLEDGE_CUTOFF,
                                             current_date=EVAL_DATE)
    return rendered + ANSWER_PREFIX


def token_bank(tokenizer):
    """re:dvx's repeng-derived single-token starts, validated for this tokenizer."""
    from redvx.wizard_protocol import DATA, single_prefixes
    return single_prefixes(tokenizer, json.loads((DATA / "repeng_all_truncated_outputs.json").read_text()))


def with_token(tokenizer, base, prefix):
    """The base text plus one supplied token, or None if the token merges across the boundary."""
    full = base + prefix["text"]
    base_ids = tokenizer(base, add_special_tokens=False)["input_ids"]
    full_ids = tokenizer(full, add_special_tokens=False)["input_ids"]
    return full_ids if full_ids == base_ids + [prefix["token_id"]] else None


def build_pairs(tokenizer, axis, bank, per_query, seed):
    pairs = []
    for qi, prompt in enumerate(axis["prompts"]):
        rng = random.Random(seed + qi)
        order = rng.sample(bank, len(bank))
        for ti, template in enumerate(axis["templates"]):
            chosen = 0
            for prefix in order:
                if chosen == per_query:
                    break
                poles = axis["adjective_pairs"][(qi + ti + chosen) % len(axis["adjective_pairs"])]
                ids = [with_token(tokenizer, render_pair_side(tokenizer, template.format(adjective=a), prompt), prefix)
                       for a in poles]
                if None in ids:
                    continue
                pairs.append({"id": f"q{qi}-t{ti}-p{chosen}", "query": prompt, "template": template, "poles": poles,
                              "prefix": prefix, "pos_ids": ids[0], "neg_ids": ids[1]})
                chosen += 1
    return pairs


def find_layers(model):
    from redvx.hooks import discover_transformer_layers
    for candidate in (model, getattr(model, "model", None)):
        if candidate is None:
            continue
        found = discover_transformer_layers(candidate)
        if found:
            return [h.module for h in found]
    raise SystemExit("no decoder layers found")


class LastTokenCapture:
    """Forward hooks on every decoder layer; keeps each layer's output at the last position."""

    def __init__(self, layers):
        self.states = {}
        self.handles = [layer.register_forward_hook(self._hook(i)) for i, layer in enumerate(layers)]
        self.n = len(layers)

    def _hook(self, index):
        def hook(module, args, output):
            hidden = output[0] if isinstance(output, tuple) else output
            self.states[index] = hidden[:, -1, :].detach().float().cpu()
            return output
        return hook

    def stack(self):
        import torch
        return torch.stack([self.states[i] for i in range(self.n)], dim=1).numpy().astype(np.float16)


def run_states(model, capture, seqs, batch_size, device, deadline):
    """Last-token states for token sequences, batched only by equal length (no padding, as re:dvx does)."""
    import torch
    out = [None] * len(seqs)
    buckets = {}
    for i, s in enumerate(seqs):
        buckets.setdefault(len(s), []).append(i)
    for length, idx in sorted(buckets.items()):
        for start in range(0, len(idx), batch_size):
            if time.time() > deadline:
                raise TimeoutError
            chunk = idx[start:start + batch_size]
            ids = torch.tensor([seqs[i] for i in chunk], device=device)
            with torch.inference_mode():
                try:
                    model(input_ids=ids, attention_mask=torch.ones_like(ids), use_cache=False, logits_to_keep=1)
                except TypeError:
                    model(input_ids=ids, attention_mask=torch.ones_like(ids), use_cache=False)
            states = capture.stack()
            for row, i in enumerate(chunk):
                out[i] = states[row]
    return np.stack(out)


def steering_vectors(pos, neg):
    """{layer: unit direction x mean residual norm} on the re:dvx steering layers: the GGUF fit_vectors.py writes."""
    from fit_vectors import fit_axis, steering_layers
    layers = steering_layers(pos.shape[1])
    units, resid, _ = fit_axis(pos, neg, layers)
    return {layer: units[layer] * resid[layer] for layer in layers}


class SteeringHooks:
    """Adds scale x vector[layer] to each steering layer's output at every position (llama.cpp build_cvec)."""

    def __init__(self, layers, vectors, scale, device):
        import torch
        self.layers, self.scale = layers, scale
        self.vectors = {i: torch.tensor(v, device=device) for i, v in vectors.items()}
        self.handles = []

    def __enter__(self):
        if self.scale == 0:
            return self
        for i, vec in self.vectors.items():
            def hook(module, args, output, vec=vec):
                hidden = output[0] if isinstance(output, tuple) else output
                hidden = hidden + (self.scale * vec).to(hidden.dtype)
                return (hidden,) + tuple(output[1:]) if isinstance(output, tuple) else hidden
            self.handles.append(self.layers[i].register_forward_hook(hook))
        return self

    def __exit__(self, *exc):
        for h in self.handles:
            h.remove()
        return False


def generate_greedy(model, tokenizer, texts, max_new, device):
    import torch
    from score_peft import EOS_IDS, PAD_ID
    enc = [tokenizer(build_prefix(tokenizer, t), add_special_tokens=False)["input_ids"] for t in texts]
    width = max(len(e) for e in enc)
    ids = torch.full((len(enc), width), PAD_ID, dtype=torch.long)
    att = torch.zeros((len(enc), width), dtype=torch.long)
    for r, e in enumerate(enc):
        ids[r, width - len(e):] = torch.tensor(e)
        att[r, width - len(e):] = 1
    with torch.inference_mode():
        gen = model.generate(input_ids=ids.to(device), attention_mask=att.to(device), max_new_tokens=max_new,
                             do_sample=False, pad_token_id=PAD_ID, eos_token_id=EOS_IDS, use_cache=True)
    outs = []
    for r in range(len(enc)):
        new = gen[r, width:].tolist()
        stop = next((k for k, t in enumerate(new) if t in EOS_IDS), None)
        outs.append({"answer": tokenizer.decode(new[:stop], skip_special_tokens=True).strip(), "truncated": stop is None})
    return outs


def calibration_prompts(spec, name, prompt_sets):
    """Abstention: SimpleQA anchor-pool questions (never the gate) and the axis's user-memory questions; a day axis:
    its own extraction prompts (the screen's test prompts stay held out)."""
    axis = next(a for a in spec["axes"] if a["name"] == name)
    if name == "abstention":
        anchors = [json.loads(line)["prompt"] for line in
                   (ROOT / "night" / "data" / "simpleqa_anchor_prompts_v1.jsonl").read_text().splitlines()[-12:]]
        bare = [a.split("\n")[0] for a in anchors[6:]]
        memory = [p + " Answer in one short sentence." for p in axis["prompts"][6:10]]
        return {"texts": anchors[:6] + bare + memory, "max_new": 40,
                "kinds": ["anchor"] * 6 + ["bare"] * len(bare) + ["memory"] * len(memory)}
    return {"texts": axis["prompts"][:4], "kinds": ["day"] * 4, "max_new": 80}


def summarize_calibration(tag, name, scale, prompts, answers):
    import simpleqa_gate
    row = {"tag": tag, "axis": name, "scale": scale, "truncated": sum(a["truncated"] for a in answers),
           "answers": [{"kind": k, "answer": a["answer"]} for k, a in zip(prompts["kinds"], answers)]}
    if name == "abstention":
        for kind in ("anchor", "bare", "memory"):
            sel = [a for k, a in zip(prompts["kinds"], answers) if k == kind]
            row[f"abstain_{kind}"] = f"{sum(simpleqa_gate.is_abstain(a['answer']) for a in sel)}/{len(sel)}"
    else:
        row["mean_words"] = round(sum(len(a["answer"].split()) for a in answers) / len(answers), 1)
    return row


def load_model(args):
    """unsloth FastModel as the night loads the NF4 checkpoint; plain transformers if unsloth is unavailable
    (on CPU, the float32 test path)."""
    import torch
    model = None
    if args.loader in ("unsloth", "auto"):
        try:
            import unsloth  # noqa: F401
            from unsloth import FastModel
            model, processor = FastModel.from_pretrained(model_name=args.model_dir, max_seq_length=2048, load_in_4bit=True)
            tokenizer = getattr(processor, "tokenizer", processor)
        except Exception as exc:  # noqa: BLE001
            if args.loader == "unsloth":
                raise
            print(json.dumps({"unsloth_failed": repr(exc)[:300]}), flush=True)
    if model is None:
        from transformers import AutoModelForImageTextToText, AutoTokenizer
        cuda = torch.cuda.is_available()
        model = AutoModelForImageTextToText.from_pretrained(
            args.model_dir, dtype=torch.bfloat16 if cuda else torch.float32, **({"device_map": {"": 0}} if cuda else {}))
        tokenizer = AutoTokenizer.from_pretrained(args.model_dir)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        for name in ("vision_tower", "vision_adapter", "vision_projection"):
            mod = getattr(model.model, name, None)
            if mod is not None:
                mod.to("cpu")
        torch.cuda.empty_cache()
    tokenizer.chat_template = (Path(args.model_dir) / "chat_template.jinja").read_text()
    model.eval()
    return model, tokenizer, device


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--adapter", action="append", default=[], help="LABEL=PEFT_DIR, read after the base")
    ap.add_argument("--axes", default=str(ROOT / "steering" / "axes.json"))
    ap.add_argument("--only-axes", default="", help="comma list; default all")
    ap.add_argument("--adapter-axes", default="", help="axes to read on adapters (default all)")
    ap.add_argument("--prompt-set", action="append", default=[],
                    help="NAME=JSONL of {id, text}: plain prompts read at the answer header (detector data), per tag")
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-query", type=int, default=2, help="supplied tokens per prompt and template")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--deadline-s", type=float, default=780)
    ap.add_argument("--loader", choices=["auto", "unsloth", "transformers"], default="auto")
    ap.add_argument("--calibrate-scales", type=lambda s: [float(x) for x in s.split(",") if x], default=[],
                    help="comma list of fractions of the residual norm for the greedy calibration replies")
    args = ap.parse_args()
    t0 = time.time()
    deadline = t0 + args.deadline_s
    spec = json.loads(Path(args.axes).read_text())
    axes = [a for a in spec["axes"] if not args.only_axes or a["name"] in args.only_axes.split(",")]
    adapter_axes = set(args.adapter_axes.split(",")) if args.adapter_axes else {a["name"] for a in axes}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    status = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "done": [], "args": vars(args)}

    def save_status(**extra):
        status.update(extra, elapsed_s=round(time.time() - t0, 1))
        (out / "status.json").write_text(json.dumps(status, indent=1) + "\n")

    model, tokenizer, device = load_model(args)
    status["load_s"] = round(time.time() - t0, 1)
    layers = find_layers(model)
    capture = LastTokenCapture(layers)
    bank = token_bank(tokenizer)
    pairs_by_axis = {a["name"]: build_pairs(tokenizer, a, bank, args.per_query, args.seed) for a in axes}
    check_ids = [tokenizer(build_prefix(tokenizer, p), add_special_tokens=False)["input_ids"] for p in spec["check_probes"]]
    prompt_sets = {}
    for item in args.prompt_set:
        name, path = item.split("=", 1)
        rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
        prompt_sets[name] = ([r["id"] for r in rows],
                             [tokenizer(build_prefix(tokenizer, r["text"]), add_special_tokens=False)["input_ids"] for r in rows])
    save_status(layers=len(layers), pairs={k: len(v) for k, v in pairs_by_axis.items()}, bank=len(bank))
    print(json.dumps({"load_s": status["load_s"], "layers": len(layers), "pairs": status["pairs"]}), flush=True)

    def done(step):
        status["done"].append(step)
        save_status()
        print(json.dumps({"done": step, "elapsed_s": round(time.time() - t0, 1)}), flush=True)

    def read_check(tag):
        (out / tag).mkdir(exist_ok=True)
        np.savez(out / tag / "check.npz", states=run_states(model, capture, check_ids, args.batch_size, device, deadline))
        done(f"{tag}/check")

    def read_axis(tag, name):
        (out / tag).mkdir(exist_ok=True)
        pairs = pairs_by_axis[name]
        pos = run_states(model, capture, [p["pos_ids"] for p in pairs], args.batch_size, device, deadline)
        neg = run_states(model, capture, [p["neg_ids"] for p in pairs], args.batch_size, device, deadline)
        np.savez(out / tag / f"{name}.npz", pos=pos, neg=neg)
        (out / tag / f"{name}.pairs.json").write_text(json.dumps(
            [{k: v for k, v in p.items() if k not in ("pos_ids", "neg_ids")} for p in pairs], indent=1) + "\n")
        done(f"{tag}/{name}")

    def read_sets(tag):
        for name, (ids, seqs) in prompt_sets.items():
            states = run_states(model, capture, seqs, args.batch_size, device, deadline)
            np.savez(out / tag / f"set-{name}.npz", states=states, ids=np.array(ids))
            done(f"{tag}/set-{name}")

    calibration = {"scales": args.calibrate_scales, "rows": []}

    def calibrate(tag, name):
        """Greedy replies with the base-fitted vector added at fixed scales (re:dvx FixedControlModel semantics:
        every position, every steering layer), to place the llama.cpp screen's scale grid."""
        d = np.load(out / "base" / f"{name}.npz")
        vectors = steering_vectors(d["pos"], d["neg"])
        prompts = calibration_prompts(spec, name, prompt_sets)
        for scale in args.calibrate_scales:
            if time.time() > deadline - 20:
                raise TimeoutError
            with SteeringHooks(layers, vectors, scale, device):
                answers = generate_greedy(model, tokenizer, prompts["texts"], prompts["max_new"], device)
            calibration["rows"].append(summarize_calibration(tag, name, scale, prompts, answers))
            (out / "calibration.json").write_text(json.dumps(calibration, indent=1) + "\n")
            print(json.dumps({k: v for k, v in calibration["rows"][-1].items() if k != "answers"}), flush=True)
        done(f"{tag}/calibrate-{name}")

    day_axes = [a["name"] for a in axes[1:]]
    first = axes[0]["name"]
    try:
        # priority order: what the screen and the morning check need first; a deadline cuts the tail
        read_check("base")
        read_axis("base", first)
        for name in day_axes:
            read_axis("base", name)
        read_sets("base")
        if args.calibrate_scales:
            calibrate("base", first)
        peft_model = None
        for item in args.adapter:
            label, path = item.split("=", 1)
            from peft import PeftModel
            if peft_model is None:
                peft_model = PeftModel.from_pretrained(model, path, adapter_name=label)
            else:
                peft_model.load_adapter(path, adapter_name=label)
            peft_model.set_adapter(label)
            peft_model.eval()
            model = peft_model
            read_check(label)
            if first in adapter_axes:
                read_axis(label, first)
            read_sets(label)
            if args.calibrate_scales:
                calibrate(label, first)
        if args.calibrate_scales and len(axes) > 1 and peft_model is not None:
            with peft_model.disable_adapter():
                calibrate("base", day_axes[0])
        elif args.calibrate_scales and len(axes) > 1:
            calibrate("base", day_axes[0])
        for item in args.adapter:
            label = item.split("=", 1)[0]
            if peft_model is not None:
                peft_model.set_adapter(label)
            for name in day_axes:
                if name in adapter_axes:
                    read_axis(label, name)
        save_status(finished=True)
    except TimeoutError:
        save_status(finished=False, stopped="deadline")
        print(json.dumps({"stopped": "deadline", "done": status["done"]}), flush=True)


if __name__ == "__main__":
    main()
