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
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-query", type=int, default=2, help="supplied tokens per prompt and template")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--deadline-s", type=float, default=780)
    ap.add_argument("--loader", choices=["auto", "unsloth", "transformers"], default="auto")
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
    save_status(layers=len(layers), pairs={k: len(v) for k, v in pairs_by_axis.items()}, bank=len(bank))
    print(json.dumps({"load_s": status["load_s"], "layers": len(layers), "pairs": status["pairs"]}), flush=True)

    def read_tag(tag, names):
        tdir = out / tag
        tdir.mkdir(exist_ok=True)
        t = time.time()
        check = run_states(model, capture, check_ids, args.batch_size, device, deadline)
        np.savez(tdir / "check.npz", states=check)
        status["done"].append(f"{tag}/check")
        save_status()
        for name in names:
            pairs = pairs_by_axis[name]
            pos = run_states(model, capture, [p["pos_ids"] for p in pairs], args.batch_size, device, deadline)
            neg = run_states(model, capture, [p["neg_ids"] for p in pairs], args.batch_size, device, deadline)
            np.savez(tdir / f"{name}.npz", pos=pos, neg=neg)
            (tdir / f"{name}.pairs.json").write_text(json.dumps(
                [{k: v for k, v in p.items() if k not in ("pos_ids", "neg_ids")} for p in pairs], indent=1) + "\n")
            status["done"].append(f"{tag}/{name}")
            save_status()
            print(json.dumps({"tag": tag, "axis": name, "pairs": len(pairs), "elapsed_s": round(time.time() - t0, 1)}),
                  flush=True)
        print(json.dumps({"tag": tag, "seconds": round(time.time() - t, 1)}), flush=True)

    try:
        read_tag("base", [a["name"] for a in axes])
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
            read_tag(label, [a["name"] for a in axes if a["name"] in adapter_axes])
        save_status(finished=True)
    except TimeoutError:
        save_status(finished=False, stopped="deadline")
        print(json.dumps({"stopped": "deadline", "done": status["done"]}), flush=True)


if __name__ == "__main__":
    main()
