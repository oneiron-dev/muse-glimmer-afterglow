#!/usr/bin/env python3
"""Train one QLoRA adapter on Muse Glimmer 30B (bnb NF4) from a night's train.jsonl. Runs on a CUDA host.

Loss falls on the assistant turn only. Target modules are named by full-path regex, because
`gate_proj` exists both in the MLP and as the attention gate (`self_attn.gate_proj`).
Saves the PEFT adapter after each epoch listed in --save-epochs (out_dir/epoch<N>/) and writes
train-report.json (recipe, versions, trainable params, loss curve, tokens/s, memory).
"""

import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path

GENERATION_DATE = "2026-10-27"
KNOWLEDGE_CUTOFF = "2026-01-04"
REASONING_STRENGTH = "minimal"
ANSWER_PREFIX = "<|start|>assistant to=user<|message|>"
HEADER_START = "<|start|>assistant"  # what the chat endpoint's generation prompt ends with
RAW_MAX_LEN = 1600  # agent-anchor rows carry the day agent's system prompt and tool list (~1150 tokens)
LONG_ROW = 600  # rows longer than this train in micro-batches of --long-batch-rows
PAD_ID = 200018
TARGETS = {
    "attn": r"self_attn\.(q_proj|k_proj|v_proj|o_proj)",
    "attn_mlp": r"(self_attn\.(q_proj|k_proj|v_proj|o_proj)|mlp\.(gate_proj|up_proj|down_proj))",
}
LAYERS = {"all": r"\d+", "last16": r"(3[6-9]|4\d|5[01])"}


def target_regex(targets, layers):
    return rf".*language_model\.layers\.{LAYERS[layers]}\.{TARGETS[targets]}"


def render(tokenizer, messages):
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False,
                                         reasoning_strength=REASONING_STRENGTH, knowledge_cutoff=KNOWLEDGE_CUTOFF,
                                         current_date=GENERATION_DATE)


def encode_row(tokenizer, row, max_len, train_header=False):
    """input_ids and labels for one exchange; labels are -100 everywhere except the assistant reply and its end token.
    With train_header the answer header's recipient (" to=user<|message|>") is trained too, so the adapter answers in
    the user channel when the server leaves the header to the model (chat endpoint), not in the self channel."""
    if row.get("raw"):  # a verbatim exchange (agent anchors): the prompt as the server renders it, the reply as generated
        prefix, full = row["raw"]["prompt"] + HEADER_START, row["raw"]["prompt"] + row["raw"]["completion"]
        max_len = max(max_len, RAW_MAX_LEN)
    else:
        user, assistant = row["messages"]
        full = render(tokenizer, [user, assistant])
        prefix = render(tokenizer, [user]) + (HEADER_START if train_header else ANSWER_PREFIX)
    if not full.startswith(prefix):
        raise ValueError(f"row {row['id']}: rendered exchange does not start with the prompt prefix")
    ids_prefix = tokenizer(prefix, add_special_tokens=False)["input_ids"]
    ids_full = tokenizer(full, add_special_tokens=False)["input_ids"]
    if ids_full[:len(ids_prefix)] != ids_prefix:
        raise ValueError(f"row {row['id']}: prefix tokens differ inside the full exchange")
    ids_full = ids_full[:max_len]
    labels = [-100] * len(ids_prefix) + ids_full[len(ids_prefix):]
    return ids_full, labels[:len(ids_full)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--train", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--rank", type=int, default=64)
    ap.add_argument("--alpha", type=int, default=None, help="default: equal to rank")
    ap.add_argument("--dropout", type=float, default=0.0)
    ap.add_argument("--targets", choices=sorted(TARGETS), default="attn_mlp")
    ap.add_argument("--layers", choices=sorted(LAYERS), default="all")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--save-epochs", default="", help="comma list; default: the last epoch")
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--warmup-steps", type=int, default=10)
    ap.add_argument("--batch-rows", type=int, default=16)
    ap.add_argument("--grad-accum", type=int, default=1)
    ap.add_argument("--max-grad-norm", type=float, default=0.3)
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--long-batch-rows", type=int, default=2, help="rows per micro-batch for rows over LONG_ROW tokens")
    ap.add_argument("--train-header", action="store_true", help="also train the answer header's recipient (\" to=user<|message|>\")")
    ap.add_argument("--max-steps", type=int, default=0, help="stop after this many optimizer steps (probe runs)")
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()
    alpha = args.alpha or args.rank
    save_epochs = {int(x) for x in args.save_epochs.split(",") if x} or {args.epochs}
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"status": "started", "args": vars(args), "alpha": alpha, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    report_path = out_dir / "train-report.json"

    def write_report():
        report_path.write_text(json.dumps(report, indent=1) + "\n")

    write_report()
    import unsloth  # noqa: F401  (patches first)
    from unsloth import FastModel
    import torch
    import bitsandbytes as bnb
    import peft
    import transformers

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    report["versions"] = {"torch": torch.__version__, "transformers": transformers.__version__, "peft": peft.__version__,
                          "bitsandbytes": bnb.__version__, "unsloth": getattr(unsloth, "__version__", "?")}
    gib = lambda b: round(b / 1024**3, 2)  # noqa: E731
    t0 = time.time()
    model, processor = FastModel.from_pretrained(model_name=args.model_dir, max_seq_length=args.max_len, load_in_4bit=True)
    tokenizer = getattr(processor, "tokenizer", processor)
    template = (Path(args.model_dir) / "chat_template.jinja").read_text()
    tokenizer.chat_template = template
    report["load_s"] = round(time.time() - t0, 1)
    report["mem_after_load_gib"] = gib(torch.cuda.memory_allocated())
    # Text-only training: the vision tower never runs, so keep it off the GPU.
    moved = []
    for name in ("vision_tower", "vision_adapter", "vision_projection"):
        mod = getattr(model.model, name, None)
        if mod is not None:
            mod.to("cpu")
            moved.append(name)
    torch.cuda.empty_cache()
    report["vision_moved_to_cpu"] = moved
    report["mem_after_vision_offload_gib"] = gib(torch.cuda.memory_allocated())
    regex = target_regex(args.targets, args.layers)
    model = FastModel.get_peft_model(model, r=args.rank, lora_alpha=alpha, lora_dropout=args.dropout, bias="none",
                                     target_modules=regex, use_gradient_checkpointing="unsloth",
                                     random_state=args.seed, finetune_vision_layers=False)
    lora_modules = sorted({n.split(".lora_")[0] for n, _ in model.named_parameters() if "lora_" in n})
    kinds = sorted({m.split("layers.")[1].split(".", 1)[1] for m in lora_modules if "layers." in m})
    layers_hit = sorted({int(m.split("layers.")[1].split(".")[0]) for m in lora_modules if "layers." in m})
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    report["lora"] = {"target_regex": regex, "modules": len(lora_modules), "kinds": kinds, "layers": [layers_hit[0], layers_hit[-1], len(layers_hit)] if layers_hit else [],
                      "vision_lora": sum(1 for m in lora_modules if "vision" in m), "trainable_params": trainable}
    if any("vision" in m for m in lora_modules) or any(k.endswith("self_attn.gate_proj") for k in kinds):
        raise SystemExit(f"unexpected LoRA targets: {kinds}")
    report["mem_after_lora_gib"] = gib(torch.cuda.memory_allocated())
    write_report()

    rows = [json.loads(line) for line in Path(args.train).read_text().splitlines() if line.strip()]
    encoded = [encode_row(tokenizer, r, args.max_len, args.train_header) for r in rows]
    bos = tokenizer.bos_token_id
    if bos is not None and encoded[0][0][0] != bos:
        raise SystemExit(f"first token {encoded[0][0][0]} is not BOS {bos}")
    report["data"] = {"rows": len(rows), "tokens_per_epoch": sum(len(i) for i, _ in encoded),
                      "loss_tokens_per_epoch": sum(sum(1 for x in lab if x != -100) for _, lab in encoded),
                      "max_row_tokens": max(len(i) for i, _ in encoded)}
    write_report()

    params = [p for p in model.parameters() if p.requires_grad]
    opt = bnb.optim.AdamW8bit(params, lr=args.lr, betas=(0.9, 0.999), weight_decay=0.0)
    steps_per_epoch = math.ceil(len(encoded) / (args.batch_rows * args.grad_accum))
    total_steps = steps_per_epoch * args.epochs
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / max(1, args.warmup_steps)))
    model.config.use_cache = False
    model.train()
    rng = random.Random(args.seed)
    curve, step, tokens_seen, t_train = [], 0, 0, time.time()
    torch.cuda.reset_peak_memory_stats()
    stop = False
    for epoch in range(1, args.epochs + 1):
        order = list(range(len(encoded)))
        rng.shuffle(order)
        short = [j for j in order if len(encoded[j][0]) <= LONG_ROW]
        long_ = [j for j in order if len(encoded[j][0]) > LONG_ROW]
        micro = [short[i:i + args.batch_rows] for i in range(0, len(short), args.batch_rows)]
        micro += [long_[i:i + args.long_batch_rows] for i in range(0, len(long_), args.long_batch_rows)]
        rng.shuffle(micro)
        for k in range(0, len(micro), args.grad_accum):
            group = micro[k:k + args.grad_accum]
            n_loss = sum(sum(1 for x in encoded[j][1] if x != -100) for mb in group for j in mb)
            loss_sum = 0.0
            for mb in group:
                width = max(len(encoded[j][0]) for j in mb)
                ids = torch.full((len(mb), width), PAD_ID, dtype=torch.long)
                lab = torch.full((len(mb), width), -100, dtype=torch.long)
                att = torch.zeros((len(mb), width), dtype=torch.long)
                for r, j in enumerate(mb):
                    i_ids, i_lab = encoded[j]
                    ids[r, :len(i_ids)] = torch.tensor(i_ids)
                    lab[r, :len(i_lab)] = torch.tensor(i_lab)
                    att[r, :len(i_ids)] = 1
                    tokens_seen += len(i_ids)
                out = model(input_ids=ids.cuda(), attention_mask=att.cuda(), labels=lab.cuda())
                mb_loss_tokens = int((lab[:, 1:] != -100).sum())
                loss = out.loss * (mb_loss_tokens / max(1, n_loss))
                loss.backward()
                loss_sum += float(loss)
            torch.nn.utils.clip_grad_norm_(params, args.max_grad_norm)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            elapsed = time.time() - t_train
            curve.append({"step": step, "epoch": epoch, "loss": round(loss_sum, 4), "tok_s": round(tokens_seen / elapsed, 1)})
            if step % 10 == 0 or step == 1:
                print(json.dumps(curve[-1]), flush=True)
            if args.max_steps and step >= args.max_steps:
                stop = True
                break
        epoch_loss = [c["loss"] for c in curve if c["epoch"] == epoch]
        report.setdefault("epochs", []).append({"epoch": epoch, "mean_loss": round(sum(epoch_loss) / max(1, len(epoch_loss)), 4),
                                                "steps": step, "elapsed_s": round(time.time() - t_train, 1)})
        if epoch in save_epochs and not args.no_save and not stop:
            model.save_pretrained(str(out_dir / f"epoch{epoch}"))
            report.setdefault("saved", []).append(f"epoch{epoch}")
        report["curve"] = curve
        report["peak_alloc_gib"] = gib(torch.cuda.max_memory_allocated())
        report["peak_reserved_gib"] = gib(torch.cuda.max_memory_reserved())
        write_report()
        if stop:
            break
    if stop and not args.no_save:
        # Stopped by --max-steps inside an epoch: the adapter at that step is the run's result.
        model.save_pretrained(str(out_dir / "final"))
        report.setdefault("saved", []).append("final")
    train_s = time.time() - t_train
    report.update({"status": "ok", "steps": step, "planned_steps": total_steps, "train_s": round(train_s, 1),
                   "tokens_seen": tokens_seen, "tok_s": round(tokens_seen / train_s, 1),
                   "ended": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    write_report()
    print(json.dumps({k: report[k] for k in ("status", "steps", "train_s", "tok_s", "peak_alloc_gib", "lora")}), flush=True)


if __name__ == "__main__":
    main()
