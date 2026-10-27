#!/usr/bin/env python3
"""Score the base model and PEFT adapters on a world's held-out probes and the general slice, in transformers.

Fast sweep scorer (NF4 + PEFT on one GPU); the morning eval of record runs on llama-server.
Arms: base (A: no notes, C: notes in the prompt) and each adapter (B: no notes, D: notes).
Greedy, answer forced into the user channel, reasoning strength minimal, truncation = failure.
Writes one JSON per arm with every answer, plus a summary line per arm on stdout.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoring  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVAL_DATE = "2026-10-27"
KNOWLEDGE_CUTOFF = "2026-01-04"
REASONING_STRENGTH = "minimal"
ANSWER_PREFIX = "<|start|>assistant to=user<|message|>"
EOS_IDS = [200001, 200008]
PAD_ID = 200018
FACT_SUFFIX = " Answer in one short sentence."
MAX_NEW_TOKENS = {"forward": 64, "reverse": 64, "never_told": 64, "general": 64, "preference": 256, "procedure": 256}
NOTES_HEADER = "Notes I keep about {first}:\n"
GENERAL_KINDS = ("general", "forget_mmlu", "forget_tool")


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def load_notes(path):
    """Notes as lines for arms C and D: the day agent's notes.json (active key/value notes), a {"notes": [str]}
    file, or consolidated.json (statements)."""
    doc = json.loads(Path(path).read_text())
    if "items" in doc:
        return [i["statement"] for i in doc["items"]]
    notes = doc["notes"]
    if notes and isinstance(notes[0], dict):
        return [f"{n['key']}: {n['value']}" for n in notes if n.get("status", "active") == "active" and not n.get("superseded_by")]
    return notes


def prompt_text(item, notes, first):
    q = item["question"] + (FACT_SUFFIX if item["kind"] in ("forward", "reverse", "never_told") else "")
    if notes:
        return NOTES_HEADER.format(first=first) + "\n".join(f"- {n}" for n in notes) + "\n\n" + q
    return q


def system_with_hint(hint):
    """The template's default system text with one extra sentence: the only change a prompt-only arm makes."""
    return (f"You are a helpful AI assistant. {hint.strip()}\nKnowledge cutoff: {KNOWLEDGE_CUTOFF}.\n"
            f"Current date: {EVAL_DATE}.")


def build_prefix(tokenizer, text, system_hint=None):
    messages = [{"role": "user", "content": text}]
    if system_hint:
        messages.insert(0, {"role": "system", "content": system_with_hint(system_hint)})
    rendered = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False,
                                             reasoning_strength=REASONING_STRENGTH, knowledge_cutoff=KNOWLEDGE_CUTOFF,
                                             current_date=EVAL_DATE)
    return rendered + ANSWER_PREFIX


def generate(model, tokenizer, prefixes, max_new_tokens, batch_size):
    import torch
    outs = []
    for i in range(0, len(prefixes), batch_size):
        chunk = prefixes[i:i + batch_size]
        enc = [tokenizer(p, add_special_tokens=False)["input_ids"] for p in chunk]
        width = max(len(e) for e in enc)
        ids = torch.full((len(enc), width), PAD_ID, dtype=torch.long)
        att = torch.zeros((len(enc), width), dtype=torch.long)
        for r, e in enumerate(enc):
            ids[r, width - len(e):] = torch.tensor(e)
            att[r, width - len(e):] = 1
        with torch.no_grad():
            gen = model.generate(input_ids=ids.cuda(), attention_mask=att.cuda(), max_new_tokens=max_new_tokens,
                                 do_sample=False, pad_token_id=PAD_ID, eos_token_id=EOS_IDS, use_cache=True)
        for r in range(len(enc)):
            new = gen[r, width:].tolist()
            ended = any(t in EOS_IDS for t in new)
            if ended:
                new = new[:min(new.index(t) for t in EOS_IDS if t in new)]
            text = tokenizer.decode(new, skip_special_tokens=False)
            outs.append({"answer": scoring.strip_answer(text), "tokens": len(new), "truncated": not ended})
    return outs


def score_arm(model, tokenizer, items, facts_by_id, notes, batch_size, first):
    groups = {}
    for it in items:
        groups.setdefault(MAX_NEW_TOKENS[it["kind"] if it["kind"] in MAX_NEW_TOKENS else "general"], []).append(it)
    results = []
    for max_new, group in groups.items():
        prefixes = [build_prefix(tokenizer, prompt_text(it, notes if it["kind"] not in GENERAL_KINDS else None, first)) for it in group]
        for it, g in zip(group, generate(model, tokenizer, prefixes, max_new, batch_size)):
            passed, note = scoring.score_probe(it, g["answer"], g["truncated"], facts_by_id)
            results.append({"id": it["id"], "kind": it["kind"], "fact_id": it.get("fact_id"), "answer": g["answer"],
                            "tokens": g["tokens"], "truncated": g["truncated"], "pass": passed, "note": note})
    return results


def fact_valid_on(fact, facts_by_id, day):
    if fact.get("valid_from", 1) > day:
        return False
    new_id = fact.get("superseded_by")
    return not (new_id and facts_by_id[new_id].get("valid_from", 1) <= day)


def probes_for_day(items, facts_by_id, day):
    """Forward/reverse probes only for facts valid on `day`; stale probes only once their fact is superseded."""
    out = []
    for it in items:
        fid = it.get("fact_id")
        if fid is None:
            out.append(it)
        elif it["kind"] == "stale":
            if not fact_valid_on(facts_by_id[fid], facts_by_id, day) and facts_by_id[fid].get("valid_from", 1) <= day:
                out.append(it)
        elif fact_valid_on(facts_by_id[fid], facts_by_id, day):
            out.append(it)
    return out


def summarize(results, world):
    by_kind = {}
    for r in results:
        s = by_kind.setdefault(r["kind"], {"n": 0, "pass": 0, "truncated": 0})
        s["n"] += 1
        s["pass"] += r["pass"]
        s["truncated"] += r["truncated"]
    learned = []
    for f in world["facts"]:
        passes = [r["pass"] for r in results if r["kind"] == "forward" and r["fact_id"] == f["id"]]
        if passes:
            learned.append(scoring.fact_learned(passes))
    return {"by_kind": by_kind, "facts_learned": sum(learned), "facts": len(learned)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, default=1, help="score the facts valid on this day (corrections supersede)")
    ap.add_argument("--notes", help="notes.json ({notes: [...]}) or consolidated.json ({items: [{statement}]}) for arms C and D")
    ap.add_argument("--base-arms", default="", help="comma list of A,C to score on the base model")
    ap.add_argument("--adapters", nargs="*", default=[], help="label=path pairs; each scored as B (and D with --notes-arms)")
    ap.add_argument("--adapter-notes-arm", action="store_true", help="also score D (adapter + notes)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--forget-slice", action="store_true", help="also score the 180 MMLU-Pro + 20 tool forgetting slice")
    ap.add_argument("--parity-check", type=int, default=0, help="score the first N forward probes at batch 1 too")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wdir = ROOT / "worlds" / args.world
    world = json.loads((wdir / "world.json").read_text())
    facts_by_id = {f["id"]: f for f in world["facts"]}
    first = world["persona"]["name"].split()[0]
    items = probes_for_day(read_jsonl(wdir / "heldout" / "probes.jsonl"), facts_by_id, args.day)
    general = [{**g, "kind": "general"} for g in read_jsonl(ROOT / "eval" / "data" / "general_slice_v1.jsonl")]
    if args.forget_slice:
        general += read_jsonl(ROOT / "eval" / "data" / "forget_slice_v1.jsonl")
    notes = load_notes(args.notes) if args.notes else None

    import unsloth  # noqa: F401
    from unsloth import FastModel
    import torch

    t0 = time.time()
    model, processor = FastModel.from_pretrained(model_name=args.model_dir, max_seq_length=4096, load_in_4bit=True)
    tokenizer = getattr(processor, "tokenizer", processor)
    tokenizer.chat_template = (Path(args.model_dir) / "chat_template.jinja").read_text()
    for name in ("vision_tower", "vision_adapter", "vision_projection"):
        mod = getattr(model.model, name, None)
        if mod is not None:
            mod.to("cpu")
    torch.cuda.empty_cache()
    model.eval()
    load_s = round(time.time() - t0, 1)

    def run(label, arm, use_notes):
        t = time.time()
        res = score_arm(model, tokenizer, items + general, facts_by_id, notes if use_notes else None, args.batch_size, first)
        summary = summarize(res, world)
        record = {"label": label, "arm": arm, "notes": bool(use_notes), "seconds": round(time.time() - t, 1),
                  "summary": summary, "results": res, "load_s": load_s,
                  "peak_alloc_gib": round(torch.cuda.max_memory_allocated() / 1024**3, 2)}
        (out_dir / f"score-{label}-{arm}.json").write_text(json.dumps(record, indent=1) + "\n")
        print(json.dumps({"label": label, "arm": arm, "seconds": record["seconds"], **summary}), flush=True)
        return res

    for arm in [a for a in args.base_arms.split(",") if a]:
        res = run("base", arm, arm == "C")
        if arm == "A" and args.parity_check:
            fwd = [it for it in items if it["kind"] == "forward"][:args.parity_check]
            single = score_arm(model, tokenizer, fwd, facts_by_id, None, 1, first)
            batched = {r["id"]: r for r in res}
            same = sum(1 for s in single if s["answer"] == batched[s["id"]]["answer"])
            same_pass = sum(1 for s in single if s["pass"] == batched[s["id"]]["pass"])
            parity = {"n": len(single), "same_answer": same, "same_pass": same_pass,
                      "examples": [{"id": s["id"], "single": s["answer"], "batched": batched[s["id"]]["answer"]} for s in single if s["answer"] != batched[s["id"]]["answer"]][:5]}
            (out_dir / "parity.json").write_text(json.dumps(parity, indent=1) + "\n")
            print(json.dumps({"parity": {k: parity[k] for k in ("n", "same_answer", "same_pass")}}), flush=True)

    from peft import PeftModel
    peft_model = None
    for pair in args.adapters:
        label, path = pair.split("=", 1)
        if peft_model is None:
            peft_model = PeftModel.from_pretrained(model, path, adapter_name=label)
            model = peft_model
        else:
            peft_model.load_adapter(path, adapter_name=label)
        peft_model.set_adapter(label)
        model.eval()
        run(label, "B", False)
        if args.adapter_notes_arm and notes:
            run(label, "D", True)


if __name__ == "__main__":
    main()
