#!/usr/bin/env python3
"""One-pass decision readout on a llama-server: the probability of each option label from the LM head.

The prompt is the decision row's user message, rendered with Glimmer's own chat template (the same settings as the
eval of record: eval/score_peft.py build_prefix) and followed by the forced answer prefix, so the next token is the
label. Each label is one token (checked against the tokenizer). The server returns the top pre-sampling
log-probabilities of that one token; the labels' probabilities are renormalized over the label set, and the mass
the labels hold before renormalizing is kept as label_mass. The decision is the top label, its probability the
confidence. No sampling, no parsing.

Auto-extend: think first (reasoning on), then read the same labels after the reasoning. Every request names every
adapter's scale and sends "cache_prompt": false.

Client needs: jinja2 and tokenizers (no torch, no transformers), plus the model dir's tokenizer.json and
chat_template.jinja.
"""

import argparse
import datetime as dt
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
from score_peft import ANSWER_PREFIX, EVAL_DATE, KNOWLEDGE_CUTOFF, REASONING_STRENGTH  # noqa: E402

N_PROBS = 128
REASONING_MAX_TOKENS = 1024


class Glimmer:
    """Chat-template rendering and tokenizing for Glimmer without transformers."""

    def __init__(self, model_dir):
        import jinja2
        from jinja2.ext import loopcontrols
        from jinja2.sandbox import ImmutableSandboxedEnvironment
        from tokenizers import Tokenizer

        model_dir = Path(model_dir)
        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True, extensions=[loopcontrols])

        def raise_exception(message):
            raise jinja2.exceptions.TemplateError(message)

        env.filters["tojson"] = lambda x, indent=None, separators=None, sort_keys=False, ensure_ascii=False: json.dumps(
            x, indent=indent, separators=separators, sort_keys=sort_keys, ensure_ascii=ensure_ascii)
        env.globals["raise_exception"] = raise_exception
        env.globals["strftime_now"] = lambda fmt: dt.datetime.now().strftime(fmt)
        self.template = env.from_string((model_dir / "chat_template.jinja").read_text())
        config = json.loads((model_dir / "tokenizer_config.json").read_text())
        self.special = {k: (v if isinstance(v, str) else (v or {}).get("content")) for k, v in config.items()
                        if k.endswith("_token") and k in ("bos_token", "eos_token", "pad_token", "unk_token")}

    def render(self, user_text, reasoning_strength=REASONING_STRENGTH, generation_prompt=False):
        text = self.template.render(messages=[{"role": "user", "content": user_text}], add_generation_prompt=generation_prompt,
                                    reasoning_strength=reasoning_strength, knowledge_cutoff=KNOWLEDGE_CUTOFF,
                                    current_date=EVAL_DATE, tools=None, **self.special)
        return text if generation_prompt else text + ANSWER_PREFIX

    def ids(self, text):
        return self.tok.encode(text, add_special_tokens=False).ids

    def label_ids(self, labels):
        out = {}
        for label in labels:
            ids = self.ids(label)
            if len(ids) != 1:
                raise SystemExit(f"label {label!r} is {len(ids)} tokens in Glimmer's tokenizer")
            out[label] = ids[0]
        return out


def post(server, path, body, timeout=900):
    req = urllib.request.Request(server + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def get(server, path):
    with urllib.request.urlopen(server + path, timeout=30) as resp:
        return json.loads(resp.read())


def lora_scales(server, on_path=None):
    """Every loaded adapter at scale 0, except the one whose path ends with on_path (scale 1). Ids come from the
    server at call time, never from a fixed list."""
    adapters = get(server, "/lora-adapters")
    scales = [{"id": a["id"], "scale": 1.0 if on_path and a["path"].endswith(on_path) else 0.0} for a in adapters]
    if on_path and not any(s["scale"] for s in scales):
        raise SystemExit(f"no adapter loaded with path ending {on_path}: {[a['path'] for a in adapters]}")
    return scales


def label_probs(server, ids, label_ids, lora, slot=None):
    """One token, pre-sampling top log-probabilities; returns (probs over labels, label_mass, raw top, ms)."""
    body = {"prompt": ids, "n_predict": 1, "temperature": 0.0, "top_k": 1, "seed": 20261014, "cache_prompt": False,
            "lora": lora, "n_probs": max(N_PROBS, len(label_ids) + 8), "post_sampling_probs": False, "return_tokens": True}
    if slot is not None:
        body["id_slot"] = slot
    t0 = time.time()
    r = post(server, "/completion", body)
    ms = round(1000 * (time.time() - t0), 1)
    probs = (r.get("completion_probabilities") or [{}])[0]
    top = probs.get("top_logprobs") or []
    by_id = {}
    for t in top:
        tid = t.get("id")
        if tid is None and t.get("token") is not None:
            tid = t["token"]
        by_id[tid] = t["logprob"]
    import math
    raw = {}
    for label, tid in label_ids.items():
        lp = by_id.get(tid, by_id.get(label))
        raw[label] = math.exp(lp) if lp is not None else 0.0
    mass = sum(raw.values())
    dist = {k: (v / mass if mass else 0.0) for k, v in raw.items()}
    return dist, mass, [[t.get("token"), round(t["logprob"], 4)] for t in top[:8]], ms, r


def think(server, glimmer, user_text, lora, strength, slot=None, max_tokens=REASONING_MAX_TOKENS):
    """Reasoning first: the assistant writes to itself until it ends the reasoning turn. Returns the reasoning text
    (the caller appends the forced answer prefix and reads the labels after it)."""
    prompt = glimmer.render(user_text, reasoning_strength=strength, generation_prompt=True) + " to=self<|message|>"
    body = {"prompt": glimmer.ids(prompt), "n_predict": max_tokens, "temperature": 0.0, "top_k": 1, "seed": 20261014,
            "cache_prompt": False, "lora": lora, "stop": ["<|eom|>", "<|eot|>", "<|start|>"], "return_tokens": False}
    if slot is not None:
        body["id_slot"] = slot
    t0 = time.time()
    r = post(server, "/completion", body)
    return prompt, r.get("content", ""), r.get("tokens_predicted"), r.get("stop_type"), round(1000 * (time.time() - t0), 1)


def reasoned(server, glimmer, user_text, label_ids, accept, lora, slot=None, strength="high"):
    """Think first, then read the labels after the reasoning. A reasoning turn that ends at its cap is undecided."""
    prompt, reasoning, n_tok, stop, think_ms = think(server, glimmer, user_text, lora, strength, slot)
    after = glimmer.ids(prompt + reasoning + "<|eom|>" + ANSWER_PREFIX)
    d2, m2, top2, ms2, _ = label_probs(server, after, label_ids, lora, slot)
    pick2 = max(d2, key=d2.get)
    undecided = stop == "limit" or (n_tok or 0) >= REASONING_MAX_TOKENS  # lead ruling 29 Oct 04:40: never an answer
    return {"pick": pick2, "p": round(d2[pick2], 4), "right": pick2 in accept, "undecided": undecided,
            "label_mass": round(m2, 4),
            "reasoning_tokens": n_tok, "stop": stop, "ms": round(think_ms + ms2, 1), "reasoning": reasoning[-600:]}


def decide(server, glimmer, row, lora, slot=None, extend_below=None, strength="high"):
    labels = list(row["options"])
    label_ids = glimmer.label_ids(labels)
    ids = glimmer.ids(glimmer.render(row["prompt"]))
    dist, mass, top, ms, r = label_probs(server, ids, label_ids, lora, slot)
    t = r.get("timings") or {}
    pick = max(dist, key=dist.get)
    out = {"id": row["id"], "kind": row["kind"], "label": row["label"], "accept": row["accept"], "pick": pick,
           "p": round(dist[pick], 4), "right": pick in row["accept"], "label_mass": round(mass, 4), "ms": ms,
           "prompt_tokens": len(ids), "server_ms": round((t.get("prompt_ms") or 0) + (t.get("predicted_ms") or 0), 1),
           "prompt_per_second": t.get("prompt_per_second"), "top": top,
           "dist": {k: round(v, 4) for k, v in sorted(dist.items(), key=lambda kv: -kv[1])[:5]}}
    if extend_below is not None and dist[pick] < extend_below:
        out["extended"] = reasoned(server, glimmer, row["prompt"], label_ids, row["accept"], lora, slot, strength)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True, help="llama-server base URL (no /v1)")
    ap.add_argument("--model-dir", required=True, help="dir with tokenizer.json, tokenizer_config.json, chat_template.jinja")
    ap.add_argument("--rows", required=True, nargs="+")
    ap.add_argument("--adapter", help="path suffix of the decision adapter to apply at scale 1 (others at 0)")
    ap.add_argument("--slot", type=int)
    ap.add_argument("--extend-below", type=float, help="auto-extend when the top probability is below this")
    ap.add_argument("--reasoning", default="high")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--stop-file", help="stop before the next call when this file exists (the slot owner's pause)")
    ap.add_argument("--until", help="stop before the next call after this local time, HH:MM")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    glimmer = Glimmer(args.model_dir)
    lora = lora_scales(args.server, args.adapter)
    rows = [json.loads(line) for path in args.rows for line in Path(path).read_text().splitlines() if line.strip()]
    if args.limit:
        rows = rows[: args.limit]
    out = Path(args.out)
    done = {json.loads(line)["id"] for line in out.read_text().splitlines()} if out.exists() else set()
    with out.open("a") as f:
        for row in rows:
            if row["id"] in done:
                continue
            if (args.stop_file and Path(args.stop_file).exists()) or (args.until and time.strftime("%H:%M") >= args.until):
                print("stopped before", row["id"], flush=True)
                break
            res = decide(args.server, glimmer, row, lora, args.slot, args.extend_below, args.reasoning)
            res["lora"] = lora
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{res['id']:50s} pick {res['pick']:>3s} p {res['p']:.3f} gold {row['label']:>3s} mass {res['label_mass']:.3f} {res['ms']} ms"
                  + (f"  -> {res['extended']['pick']} p {res['extended']['p']:.3f}" if "extended" in res else ""), flush=True)


if __name__ == "__main__":
    main()
