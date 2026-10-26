#!/usr/bin/env python3
"""Check a converted GGUF LoRA against its PEFT adapter, module by module (CPU, no model weights).

For every adapted module it compares the weight delta that llama.cpp will apply, scale*B_g@A_g, with
the PEFT delta scale*B@A, on random probe vectors. Q/K rows are compared after the same RoPE
un-permutation the converter applies to the base weights. Writes a JSON report; exit 1 on a mismatch.
Run with PYTHONPATH=<llama.cpp>/gguf-py.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import torch
from gguf import GGUFReader
from safetensors.torch import load_file

GGUF_NAME = {"self_attn.q_proj": "attn_q", "self_attn.k_proj": "attn_k", "self_attn.v_proj": "attn_v",
             "self_attn.o_proj": "attn_output", "mlp.gate_proj": "ffn_gate", "mlp.up_proj": "ffn_up",
             "mlp.down_proj": "ffn_down"}
PEFT_RE = re.compile(r"layers\.(\d+)\.(self_attn\.[qkvo]_proj|mlp\.(?:gate|up|down)_proj)\.lora_([AB])\.weight$")


def unpermute_rows(m, n_heads):
    dim1 = m.shape[0]
    return m.reshape(n_heads, 2, dim1 // n_heads // 2, *m.shape[1:]).transpose(1, 2).reshape(m.shape)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True, help="PEFT adapter dir")
    ap.add_argument("--gguf", required=True)
    ap.add_argument("--base-config", required=True, help="the Glimmer config.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--probes", type=int, default=4)
    ap.add_argument("--tolerance", type=float, default=0.02, help="max relative error per module")
    args = ap.parse_args()
    cfg = json.loads(Path(args.base_config).read_text())["text_config"]
    acfg = json.loads((Path(args.adapter) / "adapter_config.json").read_text())
    peft_scale = acfg["lora_alpha"] / acfg["r"]
    peft = {}
    for name, t in load_file(str(Path(args.adapter) / "adapter_model.safetensors")).items():
        m = PEFT_RE.search(name)
        if m:
            peft.setdefault((int(m.group(1)), m.group(2)), {})[m.group(3)] = t.float()
    reader = GGUFReader(args.gguf)
    fields = {f.name: f for f in reader.fields.values()}
    alpha_field = fields.get("adapter.lora.alpha")
    gguf_alpha = float(alpha_field.parts[alpha_field.data[0]][0]) if alpha_field else None
    gguf = {t.name: np.array(t.data, dtype=np.float32) for t in reader.tensors}
    gen = torch.Generator().manual_seed(20261027)
    worst, rows, missing = 0.0, [], []
    for (layer, kind), ab in sorted(peft.items()):
        base = f"blk.{layer}.{GGUF_NAME[kind]}.weight"
        if base + ".lora_a" not in gguf or base + ".lora_b" not in gguf:
            missing.append(base)
            continue
        A, B = ab["A"], ab["B"]
        Ag, Bg = torch.from_numpy(gguf[base + ".lora_a"]), torch.from_numpy(gguf[base + ".lora_b"])
        if Ag.shape != A.shape:
            Ag = Ag.reshape(A.shape)
        if Bg.shape != B.shape:
            Bg = Bg.reshape(B.shape)
        x = torch.randn(A.shape[1], args.probes, generator=gen)
        ref = peft_scale * (B @ (A @ x))
        if kind == "self_attn.q_proj":
            ref = unpermute_rows(ref, cfg["num_attention_heads"])
        elif kind == "self_attn.k_proj":
            ref = unpermute_rows(ref, cfg["num_key_value_heads"])
        got = (gguf_alpha / A.shape[0]) * (Bg @ (Ag @ x))
        rel = float((got - ref).norm() / ref.norm().clamp_min(1e-12))
        worst = max(worst, rel)
        rows.append({"tensor": base, "rel_err": round(rel, 6)})
    report = {"adapter": args.adapter, "gguf": args.gguf, "peft_r": acfg["r"], "peft_alpha": acfg["lora_alpha"],
              "gguf_alpha": gguf_alpha, "modules_checked": len(rows), "missing_in_gguf": missing,
              "extra_gguf_tensors": sorted(n for n in gguf if not n.endswith((".lora_a", ".lora_b"))),
              "worst_rel_err": round(worst, 6), "tolerance": args.tolerance,
              "worst_by_kind": {k: round(max(r["rel_err"] for r in rows if f".{k}." in r["tensor"]), 6)
                                for k in sorted(set(GGUF_NAME.values())) if any(f".{k}." in r["tensor"] for r in rows)},
              "passed": not missing and worst <= args.tolerance and gguf_alpha == acfg["lora_alpha"]}
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("modules_checked", "missing_in_gguf", "gguf_alpha", "worst_rel_err", "worst_by_kind", "passed")}))
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
