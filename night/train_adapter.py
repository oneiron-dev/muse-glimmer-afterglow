#!/usr/bin/env python3
"""Train one QLoRA adapter on Muse Glimmer 30B (bnb NF4) from a night's train.jsonl. Runs on a CUDA host.

Loss falls on the assistant turn only. Target modules are named by full-path regex, because
`gate_proj` exists both in the MLP and as the attention gate (`self_attn.gate_proj`).
Saves the PEFT adapter after each epoch listed in --save-epochs (out_dir/epoch<N>/) and writes
train-report.json (recipe, versions, trainable params, loss curve, tokens/s, memory).

Soft anchors (--kl-weight W > 0; lead 30 Oct 08:03-08:05, research round 2 #1): rows that are plain Glimmer's own
replies (forms "anchor", "agent_*", "public_*" and the soft-anchor pools "kl_*") are not trained as hard labels. Each
gets W x KL(teacher || student) at its reply tokens, where the teacher is the same NF4 model with the adapter off and
only its top-20 log-probabilities count (renormalised; computed once before epoch 1, cached by row hash in
--kl-cache-dir). Fact rows and the ask-back unknown-key rows keep cross-entropy. The fixed pools (kl_obscure,
kl_known, kl_people) rotate: --kl-rotate of them per epoch; the other soft rows train every epoch.
"""

import argparse
import json
import math
import os
import random
import sys
import time
import hashlib
from pathlib import Path

GENERATION_DATE = "2026-10-27"
KNOWLEDGE_CUTOFF = "2026-01-04"
REASONING_STRENGTH = "minimal"
ANSWER_PREFIX = "<|start|>assistant to=user<|message|>"
HEADER_START = "<|start|>assistant"  # what the chat endpoint's generation prompt ends with
RAW_MAX_LEN = 1600  # agent-anchor rows carry the day agent's system prompt and tool list (~1150 tokens)
KL_MAX_LEN = 2048  # soft-anchor pool rows after a recall turn; a longer one is dropped whole, never cut
TEACHER_MAX_LEN = 8192  # a context-distillation teacher sequence carries the notes block (no gradient, tail logits only)
SOFT_FORMS = ("agent_", "public_", "kl_", "cd_")  # plain Glimmer's own words (kl_, cd_: the pools and context distillation)
CAPPED_FORMS = ("kl_", "cd_")
LONG_ROW = 600  # rows longer than this train in micro-batches of --long-batch-rows
PAD_ID = 200018
TARGETS = {
    "attn": r"self_attn\.(q_proj|k_proj|v_proj|o_proj)",
    "attn_mlp": r"(self_attn\.(q_proj|k_proj|v_proj|o_proj)|mlp\.(gate_proj|up_proj|down_proj))",
}
LAYERS = {"all": r"\d+", "last16": r"(3[6-9]|4\d|5[01])"}
KL_TOP = 20
KL_ROTATING = ("kl_obscure", "kl_known", "kl_people")


def soft_row(row):
    """Plain Glimmer's own words: trained toward its distribution (KL), not as hard labels. A context-distillation row
    (cd_*, Meta vault's night/context_distill.py) is the student's own sample, trained toward plain Glimmer reading the
    same completion with the user's notes in its prompt (row["teacher"]["prompt"])."""
    f = row.get("form", "")
    return f == "anchor" or f.startswith(SOFT_FORMS)


def target_regex(targets, layers):
    return rf".*language_model\.layers\.{LAYERS[layers]}\.{TARGETS[targets]}"


def render(tokenizer, messages):
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False,
                                         reasoning_strength=REASONING_STRENGTH, knowledge_cutoff=KNOWLEDGE_CUTOFF,
                                         current_date=GENERATION_DATE)


def row_id(row):
    return row.get("id") or row.get("item_id")


def encode_row(tokenizer, row, max_len, train_header=False, limit=None):
    """input_ids and labels for one exchange; labels are -100 everywhere except the assistant reply and its end token.
    With train_header the answer header's recipient (" to=user<|message|>") is trained too, so the adapter answers in
    the user channel when the server leaves the header to the model (chat endpoint), not in the self channel."""
    capped = row.get("form", "").startswith(CAPPED_FORMS)
    if row.get("raw"):  # a verbatim exchange (agent anchors): the prompt as the server renders it, the reply as generated
        prompt = row["raw"]["prompt"]
        if tokenizer.bos_token and not prompt.startswith(tokenizer.bos_token):  # the server adds BOS when it tokenizes
            prompt = tokenizer.bos_token + prompt
        forced = row["raw"].get("forced_header")  # the prompt already ends in the answer header (a fact-form sample)
        prefix, full = (prompt if forced else prompt + HEADER_START), prompt + row["raw"]["completion"]
        if "completion_ids" in row["raw"]:  # guard: the reply must be exactly the token ids the base generated
            got = tokenizer(row["raw"]["completion"], add_special_tokens=False)["input_ids"]
            want = ([] if forced else tokenizer(HEADER_START, add_special_tokens=False)["input_ids"]) + row["raw"]["completion_ids"]
            if got != want:
                raise SystemExit(f"row {row_id(row)}: the anchor reply does not re-tokenize to the generated ids")
        max_len = limit or max(max_len, KL_MAX_LEN if capped else RAW_MAX_LEN)
    else:
        user, assistant = row["messages"]
        full = render(tokenizer, [user, assistant])
        prefix = render(tokenizer, [user]) + (HEADER_START if train_header else ANSWER_PREFIX)
    if not full.startswith(prefix):
        raise ValueError(f"row {row_id(row)}: rendered exchange does not start with the prompt prefix")
    ids_prefix = tokenizer(prefix, add_special_tokens=False)["input_ids"]
    ids_full = tokenizer(full, add_special_tokens=False)["input_ids"]
    if ids_full[:len(ids_prefix)] != ids_prefix:
        raise ValueError(f"row {row_id(row)}: prefix tokens differ inside the full exchange")
    if len(ids_full) > max_len and (capped or limit):
        return None
    ids_full = ids_full[:max_len]
    labels = [-100] * len(ids_prefix) + ids_full[len(ids_prefix):]
    return ids_full, labels[:len(ids_full)]


def teacher_encoding(tokenizer, row):
    """A context-distillation row's teacher sequence: the same completion after the teacher's prompt (the user's notes in
    it). None when it is too long or its completion tokens differ from the student's."""
    return encode_row(tokenizer, {**row, "raw": {**row["raw"], "prompt": row["teacher"]["prompt"]}}, TEACHER_MAX_LEN, limit=TEACHER_MAX_LEN)


def same_completion(student, teacher):
    s = [x for x in student[1] if x != -100]
    t = [x for x in teacher[1] if x != -100]
    return s == t


def reply_positions(labels):
    """Positions whose logits predict a reply token (the token at p + 1 is a label)."""
    return [p for p in range(len(labels) - 1) if labels[p + 1] != -100]


def logits_at(model, ids, positions):
    """Logits at the given positions; only the tail from the first of them is kept (logits_to_keep) when the model takes
    it, so a long teacher sequence (the notes block) never holds a full-vocabulary row per prompt token."""
    import torch
    keep = len(ids) - min(positions)
    batch = {"input_ids": torch.tensor([ids]).cuda(), "attention_mask": torch.ones((1, len(ids)), dtype=torch.long).cuda()}
    os.environ["UNSLOTH_RETURN_LOGITS"] = "1"  # Unsloth's fused loss path returns no logits otherwise
    try:
        try:
            out = model(**batch, logits_to_keep=keep)
        except TypeError:
            out = model(**batch)
    finally:
        os.environ.pop("UNSLOTH_RETURN_LOGITS", None)
    start = len(ids) - out.logits.shape[1]
    return out.logits[0, [p - start for p in positions]].float()


def teacher_top_k(model, encoded, soft, cache_dir, report, write_report, teacher_encoded=None):
    """The adapter-off model's top-20 log-probs at every reply token of every soft row; cached by row hash. A
    context-distillation row is read by the teacher with its own prompt (teacher_encoded[j]): the same completion tokens,
    so the student's KL compares the same positions."""
    import torch
    cache_dir.mkdir(parents=True, exist_ok=True)
    out, hits, t0 = {}, 0, time.time()
    model.eval()
    for j, enc in enumerate(encoded):
        if not soft[j]:
            continue
        ids, labels = (teacher_encoded or {}).get(j) or enc
        key = hashlib.sha256(json.dumps([ids, labels]).encode()).hexdigest()[:32]
        path = cache_dir / f"{key}.pt"
        if path.exists():
            out[j] = torch.load(path)
            hits += 1
            continue
        pos = reply_positions(labels)
        with torch.no_grad(), model.disable_adapter():
            lp = torch.log_softmax(logits_at(model, ids, pos), dim=-1)
        top = lp.topk(KL_TOP, dim=-1)
        out[j] = (top.indices.int().cpu(), top.values.half().cpu())
        torch.save(out[j], path)
    model.train()
    report["kl_teacher"] = {"rows": len(out), "cache_hits": hits, "seconds": round(time.time() - t0, 1), "top_k": KL_TOP,
                            "cache_dir": str(cache_dir)}
    write_report()
    return out


def kl_row(model, row, teach):
    """KL(teacher || student) over the teacher's top-20 (renormalised), mean over the row's reply tokens."""
    import torch
    ids, labels = row
    idx, t_lp = teach[0].cuda().long(), teach[1].cuda().float()
    s_lp = torch.log_softmax(logits_at(model, ids, reply_positions(labels)), dim=-1).gather(-1, idx)
    t_p = torch.softmax(t_lp, dim=-1)  # renormalised over the top-20
    return (t_p * (torch.log(t_p + 1e-12) - s_lp)).sum(-1).mean()


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
    ap.add_argument("--kl-weight", type=float, default=0.0, help="soft anchors: W x top-20 KL toward the adapter-off model "
                    "on plain Glimmer's own rows (0 = off: every row is cross-entropy)")
    ap.add_argument("--kl-rotate", type=int, default=80, help="fixed-pool soft rows (kl_obscure/known/people) per epoch")
    ap.add_argument("--kl-cache-dir", help="teacher top-20 cache, by row hash (default: <out-dir>/../kl-teacher-cache)")
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
    teach_enc = [teacher_encoding(tokenizer, r) if r.get("teacher") and e is not None else None for r, e in zip(rows, encoded)]
    teacher_dropped = [row_id(r) for r, e, t in zip(rows, encoded, teach_enc)
                       if r.get("teacher") and e is not None and (t is None or not same_completion(e, t))]
    too_long = [row_id(r) for r, e in zip(rows, encoded) if e is None]
    keep_j = [j for j, (r, e) in enumerate(zip(rows, encoded)) if e is not None and row_id(r) not in set(teacher_dropped)]
    rows, encoded, teach_enc = [rows[j] for j in keep_j], [encoded[j] for j in keep_j], [teach_enc[j] for j in keep_j]
    soft = [args.kl_weight > 0 and soft_row(r) for r in rows]
    bos = tokenizer.bos_token_id
    if bos is not None and encoded[0][0][0] != bos:
        raise SystemExit(f"first token {encoded[0][0][0]} is not BOS {bos}")
    teacher = {}
    if args.kl_weight > 0:
        teacher = teacher_top_k(model, encoded, soft, Path(args.kl_cache_dir) if args.kl_cache_dir else out_dir.parent / "kl-teacher-cache",
                                report, write_report, {j: t for j, t in enumerate(teach_enc) if t is not None})
    report["data"] = {"rows": len(rows), "soft_rows": sum(soft), "kl_rows_over_max_len": len(too_long),
                      "cd_rows": sum(1 for r in rows if r.get("teacher")), "cd_rows_dropped_teacher": len(teacher_dropped), "tokens_per_epoch": sum(len(i) for i, _ in encoded),
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
    rotating = [j for j, r in enumerate(rows) if soft[j] and r.get("form") in KL_ROTATING]
    rng.shuffle(rotating)
    rot_set = set(rotating)
    for epoch in range(1, args.epochs + 1):
        turn = set(rotating[((epoch - 1) * args.kl_rotate) % max(1, len(rotating)):][:args.kl_rotate]) if rotating else set()
        if rotating and len(turn) < min(args.kl_rotate, len(rotating)):  # wrap around the pool
            turn |= set(rotating[:args.kl_rotate - len(turn)])
        order = [j for j in range(len(encoded)) if not (j in rot_set and j not in turn)]
        rng.shuffle(order)
        hard = [j for j in order if not soft[j]]
        short = [j for j in hard if len(encoded[j][0]) <= LONG_ROW]
        long_ = [j for j in hard if len(encoded[j][0]) > LONG_ROW]
        micro = [short[i:i + args.batch_rows] for i in range(0, len(short), args.batch_rows)]
        micro += [long_[i:i + args.long_batch_rows] for i in range(0, len(long_), args.long_batch_rows)]
        soft_order = [j for j in order if soft[j]]  # soft rows run one at a time (their logits are kept for the KL term),
        micro += [soft_order[i:i + args.batch_rows // 2] for i in range(0, len(soft_order), args.batch_rows // 2)]  # 8 per step
        rng.shuffle(micro)
        for k in range(0, len(micro), args.grad_accum):
            group = micro[k:k + args.grad_accum]
            n_loss = sum(sum(1 for x in encoded[j][1] if x != -100) for mb in group for j in mb)
            loss_sum = 0.0
            for mb in group:
                if soft[mb[0]]:  # soft rows: W x KL to the teacher's top-20 at their reply tokens, one row per forward
                    for j in mb:
                        loss = args.kl_weight * kl_row(model, encoded[j], teacher[j]) * (len(teacher[j][0]) / max(1, n_loss))
                        loss.backward()
                        loss_sum += float(loss)
                        tokens_seen += len(encoded[j][0])
                    continue
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
    if args.kl_weight > 0:
        report["kl"] = {"weight": args.kl_weight, "rotate": args.kl_rotate, "soft_rows": sum(soft), "rotating_rows": len(rotating)}
    report.update({"status": "ok", "steps": step, "planned_steps": total_steps, "train_s": round(train_s, 1),
                   "tokens_seen": tokens_seen, "tok_s": round(tokens_seen / train_s, 1),
                   "ended": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    write_report()
    print(json.dumps({k: report[k] for k in ("status", "steps", "train_s", "tok_s", "peak_alloc_gib", "lora")}), flush=True)


if __name__ == "__main__":
    main()
