#!/usr/bin/env python3
"""Fine-tune Glimmer's DFlash drafter on a world's own served traffic (step-A recipe, multi-span day transcripts).

Data: W/taps/<world>/dayNN.pt (extract_taps.py): one served day per file, its token ids, the generated spans (one per
server call: reasoning, tool call or reply) and the served target's taps (Glimmer + the world's adapter).

Recipe (DFlash paper sec. 4.2 + app. A.1; step A, 14 Oct, /Users/olety/Desktop/temp/drafter-tune-20261014):
  - start from the official drafter; the target's raw embedding table and lm_head stay frozen; every drafter weight
    trains
  - anchor blocks inside the generated spans: slot 0 = the anchor token, slots 1..15 = mask; the labels are the next
    15 tokens of the SAME span (positions at or past the span end are unsupervised: the drafter only drafts inside
    one generation)
  - a block sees the target's taps of every token before its anchor (sliding window 2048) and its own block
  - CE on raw lm_head logits, weight exp(-(k-1)/7) on slot k; AdamW (0.9, 0.95), clip 1.0, cosine, 10% warmup
  - memory on one 24 GB card: bf16 weights + grads on the GPU, fp32 master + AdamW state on the CPU
Teacher-forced check (the keep rule's first half): tau = 1 + mean leading-correct draft slots (greedy, max 15) over
every anchor of the held-out days; reported per world and for the stock drafter.

usage: train_drafter.py W --train WORLD:DAYS[,WORLD:DAYS...] --val WORLD:DAYS[,...] --out NAME
                          [--lrs 3e-5] [--epochs 2] [--anchors-per-step 1024] [--eval-every-epoch] [--eval-only]
       DAYS is "1-9" or "10"; WORLD "all" means the four worlds.
"""
import argparse
import json
import math
import os
import random
import sys
import time

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402
from safetensors.torch import load_file  # noqa: E402

WORLDS = ["w1-ines-varga", "w2-solo-developer", "w3-shop-owner", "w4-grad-student"]

ap = argparse.ArgumentParser()
ap.add_argument("workdir")
ap.add_argument("--train", default="")
ap.add_argument("--val", required=True)
ap.add_argument("--out", default="ckpt/drafter")
ap.add_argument("--lrs", default="3e-5")
ap.add_argument("--epochs", type=int, default=2)
ap.add_argument("--anchors-per-step", type=int, default=1024)
ap.add_argument("--chunk", type=int, default=64)
ap.add_argument("--gamma", type=float, default=7.0)
ap.add_argument("--warmup", type=float, default=0.1)
ap.add_argument("--eval-every-epoch", action="store_true")
ap.add_argument("--eval-only", action="store_true")
ap.add_argument("--save", action="store_true", help="save the final weights (HF format) for conversion")
ap.add_argument("--seed", type=int, default=20261103)
args = ap.parse_args()
W = args.workdir
os.makedirs(os.path.dirname(f"{W}/{args.out}"), exist_ok=True)
torch.manual_seed(args.seed)
dev = torch.device("cuda")
T0 = time.time()


def log(*a):
    print(f"[{(time.time() - T0) / 60:6.1f} min]", *a, flush=True)


def cpu_quota():
    """CPU threads for the CPU-side optimizer: the container's CFS quota (rented boxes show every host core)."""
    for path, parse in (("/sys/fs/cgroup/cpu.max", lambda s: s.split()),
                        ("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", lambda s: [s, open("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read()])):
        try:
            q, p = parse(open(path).read().strip())
            if q != "max" and int(q) > 0:
                return max(1, int(int(q) / int(p)))
        except (OSError, ValueError):
            pass
    return len(os.sched_getaffinity(0))


torch.set_num_threads(int(os.environ.get("DRAFTER_THREADS", cpu_quota())))
log(f"torch CPU threads {torch.get_num_threads()}")


def parse_set(spec):
    out = []
    for part in [p for p in spec.split(",") if p]:
        world, days = part.split(":")
        lo, hi = (int(x) for x in (days.split("-") if "-" in days else (days, days)))
        for w in (WORLDS if world == "all" else [world]):
            out += [(w, d) for d in range(lo, hi + 1) if os.path.exists(f"{W}/taps/{w}/day{d:02d}.pt")]
    return out


from transformers.models.muse_glimmer_assistant import MuseGlimmerAssistantModel  # noqa: E402

model, info = MuseGlimmerAssistantModel.from_pretrained(f"{W}/models/drafter-stock", dtype=torch.bfloat16,
                                                        output_loading_info=True)
assert not any(info[k] for k in ("missing_keys", "unexpected_keys", "mismatched_keys")), info
model.to(dev)
cfg = model.config
BS, MASK, WIN = cfg.block_size, cfg.mask_token_id, cfg.sliding_window
NH, NKV, HD = cfg.num_attention_heads, cfg.num_key_value_heads, cfg.head_dim
eh_path = f"{W}/models/embed_head.safetensors"
if not os.path.exists(eh_path):
    from safetensors import safe_open
    from safetensors.torch import save_file
    idx = json.load(open(f"{W}/models/target-nf4/model.safetensors.index.json"))["weight_map"]
    t = {}
    for key, name in (("model.language_model.embed_tokens.weight", "embed"), ("lm_head.weight", "lm_head")):
        with safe_open(f"{W}/models/target-nf4/{idx[key]}", "pt") as f:
            t[name] = f.get_tensor(key).to(torch.bfloat16).contiguous()
    save_file(t, eh_path)
eh = load_file(eh_path)
EMBED = eh["embed"]          # raw table (DFlash bypasses Glimmer's embed norm), on the CPU
LM_HEAD = eh["lm_head"].to(dev)
inv_freq = 1.0 / (cfg.rope_parameters["rope_theta"] ** (torch.arange(0, HD, 2, dtype=torch.float32, device=dev) / HD))
WEIGHTS = torch.exp(-torch.arange(0, BS - 1, dtype=torch.float32) / args.gamma).to(dev)
log(f"drafter {sum(p.numel() for p in model.parameters()) / 1e9:.3f}B params; block {BS}, mask {MASK}, window {WIN}")


def rope(pos):
    f = pos.float()[:, None] * inv_freq[None]
    e = torch.cat([f, f], -1)
    return e.cos().to(torch.bfloat16), e.sin().to(torch.bfloat16)


def rot(x, cos, sin):
    x1, x2 = x[..., : HD // 2], x[..., HD // 2:]
    return x * cos[None] + torch.cat([-x2, x1], -1) * sin[None]


def forward(rec, anchors, ends, hs_key="hs"):
    """The drafter on len(anchors) blocks of one day. Returns final hidden (Nb, BS, D) and labels (Nb, BS-1)."""
    ids = rec["ids"].long()
    nb = len(anchors)
    a = torch.tensor(anchors, dtype=torch.long)
    e = torch.tensor(ends, dtype=torch.long)
    lo = max(0, int(a.min()) - WIN)
    hi = int(a.max())
    ctx = rec[hs_key][lo:hi].to(dev, non_blocking=True)
    noise_ids = torch.full((nb, BS), MASK, dtype=torch.long)
    noise_ids[:, 0] = ids[a]
    x = EMBED[noise_ids.view(-1)].to(dev)
    qpos = (a[:, None] + torch.arange(BS)[None]).view(-1).to(dev)
    cpos = torch.arange(lo, hi, device=dev)
    lab_pos = a[:, None] + torch.arange(1, BS)[None]
    labels = torch.where(lab_pos < e[:, None], ids[lab_pos.clamp(max=ids.numel() - 1)],
                         torch.full_like(lab_pos, -100)).to(dev)
    a_row = a.to(dev).repeat_interleave(BS)
    m_ctx = (cpos[None] < a_row[:, None]) & ((qpos[:, None] - cpos[None]) < WIN)
    blk = torch.arange(nb, device=dev).repeat_interleave(BS)
    mask = torch.cat([m_ctx, blk[:, None] == blk[None]], 1)[None, None]
    cos_q, sin_q = rope(qpos)
    cos_k, sin_k = rope(torch.cat([cpos, qpos]))
    c = model.encoder(ctx)
    h = x
    for layer in model.layers:
        att = layer.self_attn
        r = h
        y = layer.input_layernorm(h)
        kv = torch.cat([c, y], 0)
        q = att.q_norm(att.q_proj(y).view(-1, NH, HD)).transpose(0, 1)
        k = att.k_norm(att.k_proj(kv).view(-1, NKV, HD)).transpose(0, 1)
        v = att.v_proj(kv).view(-1, NKV, HD).transpose(0, 1)
        q, k = rot(q, cos_q, sin_q), rot(k, cos_k, sin_k)
        k, v = k.repeat_interleave(NH // NKV, 0), v.repeat_interleave(NH // NKV, 0)
        o = F.scaled_dot_product_attention(q[None], k[None], v[None], attn_mask=mask, scale=HD ** -0.5)
        h = r + att.o_proj(o[0].transpose(0, 1).reshape(-1, NH * HD))
        h = h + layer.mlp(layer.post_attention_layernorm(h))
    return model.norm(h).view(nb, BS, -1), labels


def block_loss(h, labels, chunk=64):
    tot = torch.zeros((), device=dev)
    for s in range(0, h.shape[0], chunk):
        hh, ll = h[s:s + chunk, 1:], labels[s:s + chunk]

        def f(hh, ll):
            z = (hh.reshape(-1, hh.shape[-1]) @ LM_HEAD.T).float()
            ce = F.cross_entropy(z, ll.reshape(-1), ignore_index=-100, reduction="none").view(ll.shape)
            return (ce * WEIGHTS[None] * (ll >= 0)).sum()
        tot = tot + (torch.utils.checkpoint.checkpoint(f, hh, ll, use_reentrant=False)
                     if torch.is_grad_enabled() else f(hh, ll))
    return tot


def anchors_of(rec):
    """(anchor, span end) for every anchor with at least one label inside its span."""
    out = []
    for s, e in rec["spans"]:
        out += [(p, e) for p in range(s, e - 1)]
    return out


CACHE = {}


def load(key):
    if key not in CACHE:
        w, d = key
        CACHE[key] = torch.load(f"{W}/taps/{w}/day{d:02d}.pt", weights_only=True, mmap=True)
    return CACHE[key]


@torch.no_grad()
def evaluate(items, hs_key="hs"):
    """Teacher-forced greedy acceptance on every anchor of the held-out days, per world."""
    model.eval()
    by_world = {}
    for key in items:
        rec = load(key)
        if hs_key not in rec:
            continue
        an = anchors_of(rec)
        d = by_world.setdefault(key[0], [0.0, 0, 0.0, 0.0])
        for i in range(0, len(an), 128):
            part = an[i:i + 128]
            h, lab = forward(rec, [p for p, _ in part], [e for _, e in part], hs_key)
            d[2] += float(block_loss(h, lab))
            d[3] += float((WEIGHTS[None] * (lab >= 0)).sum())
            pred = torch.cat([(h[j:j + 64, 1:] @ LM_HEAD.T).argmax(-1) for j in range(0, h.shape[0], 64)])
            ok = (pred == lab) & (lab >= 0)
            lead = ok.int().cumprod(1).sum(1).float()
            d[0] += float(lead.sum())
            d[1] += lead.numel()
    model.train()
    res = {w: {"tau": round(1 + v[0] / max(v[1], 1), 4), "blocks": v[1], "wce": round(v[2] / max(v[3], 1), 4)}
           for w, v in by_world.items()}
    tot = [sum(v[i] for v in by_world.values()) for i in range(2)]
    res["all"] = {"tau": round(1 + tot[0] / max(tot[1], 1), 4), "blocks": tot[1]}
    return res


train = parse_set(args.train)
val = parse_set(args.val)
log(f"train {len(train)} days {train[:3]}..., val {len(val)} days {val}")
assert val, "no held-out days found"

alt = [k for k in val if "hs_alt" in load(k)][:2]
if alt:
    r_main, r_alt = evaluate(alt, "hs")["all"]["tau"], evaluate(alt, "hs_alt")["all"]["tau"]
    log(f"tap convention check on {alt}: layer outputs {cfg.target_layer_ids} -> tau {r_main}; one earlier -> {r_alt}")
    assert r_main > r_alt, "tap convention looks wrong"
stock = evaluate(val)
log("STOCK held-out:", json.dumps(stock))
if args.eval_only:
    json.dump({"stock": stock}, open(f"{W}/{args.out}-stock.json", "w"), indent=1)
    sys.exit(0)


def train_one(lr, out):
    global model
    model = MuseGlimmerAssistantModel.from_pretrained(f"{W}/models/drafter-stock", dtype=torch.bfloat16).to(dev)
    params = list(model.parameters())
    master = [p.detach().float().cpu() for p in params]
    for m in master:
        m.requires_grad_(True)
        m.grad = torch.zeros_like(m)
    stage = [torch.empty(p.shape, dtype=torch.bfloat16, pin_memory=True) for p in params]
    try:
        opt = torch.optim.AdamW(master, lr=lr, betas=(0.9, 0.95), weight_decay=0.0, fused=True)
    except Exception:
        opt = torch.optim.AdamW(master, lr=lr, betas=(0.9, 0.95), weight_decay=0.0, foreach=True)
    # windows of `chunk` consecutive anchors of one day (one forward each, so each block's 2048-token context is
    # shared by its window); a step takes anchors_per_step / chunk windows drawn at random over every day
    windows = []
    for k in train:
        an = anchors_of(load(k))
        windows += [(k, an[i:i + args.chunk]) for i in range(0, len(an), args.chunk)]
    per_step = max(1, args.anchors_per_step // args.chunk)
    pool_n = sum(len(w) for _, w in windows)
    steps_per_epoch = math.ceil(len(windows) / per_step)
    total = steps_per_epoch * args.epochs
    warm = max(3, int(round(total * args.warmup)))

    def lr_at(step):
        if step < warm:
            return lr * (step + 1) / warm
        return lr * 0.5 * (1 + math.cos(math.pi * (step - warm) / max(1, total - warm)))
    log(f"[lr {lr:g}] {pool_n} anchors in {len(windows)} windows, {steps_per_epoch} steps/epoch x {args.epochs} "
        f"epochs, warmup {warm}")
    model.train()
    torch.cuda.reset_peak_memory_stats()
    rng = random.Random(args.seed)
    hist, evals = [], []
    t_train = time.time()
    step = 0
    for ep in range(args.epochs):
        order = list(windows)
        rng.shuffle(order)
        for s0 in range(0, len(order), per_step):
            batch = order[s0:s0 + per_step]
            norm = sum(float(WEIGHTS[: max(0, min(BS - 1, e - 1 - p))].sum()) for _, part in batch for p, e in part)
            lsum = 0.0
            t_step = time.time()
            for k, part in batch:
                h, lab = forward(load(k), [p for p, _ in part], [e for _, e in part])
                loss = block_loss(h, lab) / norm
                loss.backward()
                lsum += float(loss.detach())
            gn = float(torch.nn.utils.clip_grad_norm_(params, 1.0))
            t_gpu = time.time() - t_step
            with torch.no_grad():
                for p, st in zip(params, stage):
                    st.copy_(p.grad, non_blocking=True)
                    p.grad = None
                torch.cuda.synchronize()
                for m, st in zip(master, stage):
                    m.grad.copy_(st)
                for g in opt.param_groups:
                    g["lr"] = lr_at(step)
                opt.step()
                for m, st in zip(master, stage):
                    st.copy_(m)
                for p, st in zip(params, stage):
                    p.copy_(st, non_blocking=True)
                torch.cuda.synchronize()
            t_opt = time.time() - t_step - t_gpu
            hist.append({"step": step + 1, "loss": round(lsum, 4), "grad_norm": round(gn, 3), "lr": lr_at(step),
                         "fwd_bwd_s": round(t_gpu, 2), "optimizer_s": round(t_opt, 2)})
            if step % 10 == 0 or step == total - 1:
                log(f"[lr {lr:g}] step {step + 1}/{total} loss {lsum:.4f} gnorm {gn:.2f} lr {lr_at(step):.2e} "
                    f"fwd+bwd {t_gpu:.1f}s optimizer {t_opt:.1f}s peak VRAM {torch.cuda.max_memory_allocated() / 2**30:.2f} GiB")
            step += 1
        if args.eval_every_epoch or ep == args.epochs - 1:
            ev = evaluate(val)
            evals.append({"epoch": ep + 1, "val": ev})
            log(f"[lr {lr:g}] epoch {ep + 1} held-out:", json.dumps(ev))
    train_min = (time.time() - t_train) / 60
    rep = {"lr": lr, "args": vars(args), "train_days": train, "val_days": val, "anchors": pool_n, "windows": len(windows), "steps": total,
           "warmup_steps": warm, "train_minutes": round(train_min, 2),
           "peak_vram_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2), "stock_val": stock,
           "evals": evals, "history": hist}
    os.makedirs(f"{W}/{out}", exist_ok=True)
    if args.save:
        model.save_pretrained(f"{W}/{out}", safe_serialization=True)
    json.dump(rep, open(f"{W}/{out}/train_report.json", "w"), indent=1)
    del opt, master, params, stage
    return rep


reports = [train_one(float(lr), f"{args.out}-lr{float(lr):g}") for lr in args.lrs.split(",")]
summary = {"stock": stock, "runs": [{"lr": r["lr"], "train_minutes": r["train_minutes"], "anchors": r["anchors"],
                                     "evals": r["evals"]} for r in reports]}
json.dump(summary, open(f"{W}/{args.out}-summary.json", "w"), indent=1)
for r in reports:
    for e in r["evals"]:
        log(f"TRAIN-DONE lr {r['lr']:g} epoch {e['epoch']}: held-out tau "
            + " ".join(f"{w.split('-')[0]} {stock[w]['tau']:.3f}->{v['tau']:.3f}" for w, v in e["val"].items()))
