#!/usr/bin/env python3
"""Prove a Fireworks dataset (night/fireworks_dataset.py) renders the night's rows byte-identically (CPU, tokenizer only).

For each converted row it renders the messages (+ tools) with the Glimmer chat template and compares with what the night
trainer trains on: a plain row against train_adapter.render() of the original (reasoning strength "minimal", the
trainer's cutoff and date); a verbatim row against BOS + raw prompt + raw completion, rendered at the day agent's
reasoning strength ("low"). It also renders each row at the template default, as the Fireworks renderer does, and
checks that the only difference is the reasoning strength line. Writes a JSON report; exit 1 on any mismatch.

Usage: python night/fireworks_render_check.py --model-dir DIR --train TRAIN.jsonl --dataset DATASET.jsonl --out REPORT.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_adapter import render  # noqa: E402

REASONING_LINE = re.compile(r"Reasoning strength: [a-z]+\.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--train", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(args.model_dir)
    originals = {}
    for line in Path(args.train).read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            originals[r.get("id") or r.get("item_id")] = r
    bad, tokens, n = [], 0, 0
    for line in Path(args.dataset).read_text().splitlines():
        conv = json.loads(line)
        orig = originals[conv["meta_id"]]
        kw = {"tools": conv["tools"]} if conv.get("tools") else {}
        if orig.get("raw"):
            want = orig["raw"]["prompt"] + orig["raw"]["completion"]
            if not want.startswith(tok.bos_token):
                want = tok.bos_token + want
            got = tok.apply_chat_template(conv["messages"], tokenize=False, add_generation_prompt=False, reasoning_strength="low", **kw)
        else:
            want = render(tok, orig["messages"])
            got = tok.apply_chat_template(conv["messages"], tokenize=False, add_generation_prompt=False, reasoning_strength="minimal", **kw)
        fw = tok.apply_chat_template(conv["messages"], tokenize=False, add_generation_prompt=False, **kw)
        n += 1
        tokens += len(tok(fw, add_special_tokens=False)["input_ids"])
        if got != want:
            i = next((k for k in range(min(len(got), len(want))) if got[k] != want[k]), min(len(got), len(want)))
            bad.append({"id": conv["meta_id"], "form": conv["meta_form"], "at": i, "got": got[max(0, i - 60):i + 60],
                        "want": want[max(0, i - 60):i + 60]})
        elif REASONING_LINE.sub("R", fw) != REASONING_LINE.sub("R", want):
            bad.append({"id": conv["meta_id"], "form": conv["meta_form"], "fireworks_render": "differs beyond the reasoning line"})
    report = {"rows": n, "mismatches": len(bad), "examples": bad[:10], "fireworks_render_tokens": tokens,
              "dataset": str(Path(args.dataset).resolve()), "passed": not bad}
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("rows", "mismatches", "fireworks_render_tokens", "passed")}))
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
