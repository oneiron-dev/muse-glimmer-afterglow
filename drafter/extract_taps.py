#!/usr/bin/env python3
"""Cache the served target's hidden taps over each world's day transcripts, for DFlash drafter training and the
teacher-forced check.

The served target for a world is Glimmer plus that world's kept LoRA adapter at scale 1 (app/serve: one adapter per
world, scale sent per request). The drafter reads the target's hidden states, so the taps come from the NF4 target
with the world's PEFT adapter applied: outputs of decoder layers TAPS (0-based; the drafter's target_layer_ids; z-lab
reads hidden_states[id + 1], i.e. the output of layer id). Layers after the last tap are skipped.

One file per world and day: W/taps/<world>/dayNN.pt = {"ids", "spans", "hs" (L, 5*D) bf16}. A few days also carry
"hs_alt" (taps one layer earlier) so the trainer can confirm the tap convention on the stock drafter.

usage: extract_taps.py W --world WORLD --adapter PEFT_DIR [--days 1-10]
"""
import argparse
import json
import os
import time

import torch

TAPS = [1, 13, 25, 37, 49]
ALT = [t - 1 for t in TAPS]

ap = argparse.ArgumentParser()
ap.add_argument("workdir")
ap.add_argument("--world", required=True)
ap.add_argument("--adapter", required=True, help="the world's kept PEFT adapter dir ('none' = base weights)")
ap.add_argument("--days", default="1-10")
ap.add_argument("--alt-days", default="10", help="days that also store the off-by-one taps for the convention check")
a = ap.parse_args()
W = a.workdir
MODEL = f"{W}/models/target-nf4"
OUT = f"{W}/taps/{a.world}"
os.makedirs(OUT, exist_ok=True)
lo, hi = (int(x) for x in a.days.split("-"))
alt_days = {int(x) for x in a.alt_days.split(",") if x}
t_start = time.time()

from transformers import AutoConfig, MuseGlimmerForConditionalGeneration  # noqa: E402

config = AutoConfig.from_pretrained(MODEL)
qc = config.quantization_config
if isinstance(qc, dict):
    qc["llm_int8_enable_fp32_cpu_offload"] = True
else:
    qc.llm_int8_enable_fp32_cpu_offload = True
dm = {"model.language_model.layers": 0, "model.language_model.norm": 0, "model.language_model.rotary_emb": 0,
      "model.language_model.embed_tokens": 0, "lm_head": "cpu", "model.vision_tower": "cpu",
      "model.vision_adapter": "cpu", "model.vision_projection": "cpu"}
model = MuseGlimmerForConditionalGeneration.from_pretrained(MODEL, config=config, device_map=dm, dtype=torch.bfloat16)
if a.adapter != "none":
    from peft import PeftModel

    # PEFT re-dispatches a model whose device map holds "cpu" (our unused vision tower), and that walk fails on the
    # bnb modules left on the meta device. The language layers all sit on GPU 0, so attach the adapter in place.
    if hasattr(model, "hf_device_map"):
        del model.hf_device_map
    model = PeftModel.from_pretrained(model, a.adapter, is_trainable=False)
    lora = [(n, m) for n, m in model.named_modules() if n.endswith("lora_B")]
    assert lora, "the adapter attached no LoRA modules"
    dev_set = {p.device.type for _, m in lora for p in m.parameters()}
    nz = sum(int(any(p.abs().sum() > 0 for p in m.parameters())) for _, m in lora)
    print(f"adapter {a.adapter}: {len(lora)} LoRA modules on {dev_set}, {nz} with nonzero B", flush=True)
    assert dev_set == {"cuda"} and nz > 0
model.eval()
lm = None
for name, mod in model.named_modules():
    if name.endswith("language_model") and hasattr(mod, "layers"):
        lm = mod
        break
assert lm is not None, "language model not found"
print(f"target loaded in {time.time() - t_start:.0f}s; VRAM {torch.cuda.memory_allocated() / 2**30:.2f} GiB", flush=True)

cap = {}


class Stop(Exception):
    pass


want = set(TAPS)


def hook(i):
    def f(mod, inp, out):
        h = out[0] if isinstance(out, tuple) else out
        if i in want:
            cap[i] = h.detach()
        if i == max(want):
            raise Stop()
    return f


for i, layer in enumerate(lm.layers):
    layer.register_forward_hook(hook(i))


def run(ids, layers):
    global want
    want = set(layers)
    cap.clear()
    x = torch.tensor([ids], dtype=torch.long, device=0)
    with torch.no_grad():
        try:
            lm(input_ids=x, attention_mask=torch.ones_like(x), use_cache=False)
        except Stop:
            pass
    return torch.cat([cap[i][0] for i in layers], dim=-1).cpu().contiguous()


if a.adapter != "none":  # the adapter must change the taps (else the served target is not what we read)
    probe = json.load(open(f"{W}/seqs/{a.world}/day{hi:02d}.json"))["ids"][:256]
    on = run(probe, TAPS)
    with model.disable_adapter():
        off = run(probe, TAPS)
    rel = float((on.float() - off.float()).norm() / off.float().norm())
    print(f"adapter check: relative tap change with the adapter on vs off = {rel:.4f}", flush=True)
    assert rel > 1e-3, "the adapter does not change the taps"

torch.cuda.reset_peak_memory_stats()
ntok, t0 = 0, time.time()
for day in range(lo, hi + 1):
    src = f"{W}/seqs/{a.world}/day{day:02d}.json"
    dst = f"{OUT}/day{day:02d}.pt"
    if not os.path.exists(src) or os.path.exists(dst):
        continue
    d = json.load(open(src))
    rec = {"ids": torch.tensor(d["ids"], dtype=torch.int32), "spans": d["spans"], "rounds": d["rounds"],
           "hs": run(d["ids"], TAPS), "adapter": a.adapter}
    if day in alt_days:
        rec["hs_alt"] = run(d["ids"], ALT)
    torch.save(rec, dst + ".tmp")
    os.replace(dst + ".tmp", dst)
    ntok += len(d["ids"])
    el = time.time() - t0
    print(f"{a.world} day {day}: {len(d['ids'])} tok, {ntok / max(el, 1e-6):.0f} tok/s, "
          f"peak VRAM {torch.cuda.max_memory_allocated() / 2**30:.2f} GiB", flush=True)
print(f"TAPS-DONE {a.world} {ntok} tokens in {(time.time() - t0) / 60:.1f} min; total wall "
      f"{(time.time() - t_start) / 60:.1f} min", flush=True)
