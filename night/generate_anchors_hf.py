#!/usr/bin/env python3
"""Answer the general anchor prompts with the NF4 base checkpoint itself (transformers, on the GPU host).

Same output as generate_anchors.py, but the answers come from the exact weights the adapter trains on,
so the replay rows are true self-distillation. Greedy; answers cut by the token limit are dropped.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
from score_peft import build_prefix, generate  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--prompts", default=str(ROOT / "night" / "data" / "anchor_prompts_v1.jsonl"))
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--max-new-tokens", type=int, default=300)
    args = ap.parse_args()
    import unsloth  # noqa: F401
    from unsloth import FastModel
    import torch

    model, processor = FastModel.from_pretrained(model_name=args.model_dir, max_seq_length=2048, load_in_4bit=True)
    tokenizer = getattr(processor, "tokenizer", processor)
    tokenizer.chat_template = (Path(args.model_dir) / "chat_template.jinja").read_text()
    for name in ("vision_tower", "vision_adapter", "vision_projection"):
        mod = getattr(model.model, name, None)
        if mod is not None:
            mod.to("cpu")
    torch.cuda.empty_cache()
    model.eval()
    prompts = [json.loads(line) for line in Path(args.prompts).read_text().splitlines() if line.strip()]
    outs = generate(model, tokenizer, [build_prefix(tokenizer, p["prompt"]) for p in prompts], args.max_new_tokens, 16)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    kept, dropped = 0, []
    with open(out_dir / "anchors.jsonl", "w") as f:
        for p, o in zip(prompts, outs):
            if o["truncated"] or not o["answer"]:
                dropped.append(p["id"])
                continue
            kept += 1
            f.write(json.dumps({"item_id": p["id"], "kind": "anchor", "form": "anchor", "user": p["prompt"],
                                "assistant": o["answer"], "valid": True, "teacher": "glimmer-nf4-base"}) + "\n")
    summary = {"teacher": "glimmer-nf4-base", "prompts": len(prompts), "kept": kept, "dropped": dropped}
    (out_dir / "anchors-summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
