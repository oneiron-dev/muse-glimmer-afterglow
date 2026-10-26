#!/usr/bin/env python3
"""Answer the general anchor prompts with the base model, for replay rows that keep general skills.

The anchors are the base model's own greedy answers (self-distillation), so training on them pulls
the adapter toward the base behaviour. Answers cut by the token limit are dropped.
With an adapter loaded on the server, the call sends LoRA scale 0 so the answers come from the base.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import teacher as teacher_mod  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--teacher", default="glimmer", choices=sorted(teacher_mod.TEACHERS))
    ap.add_argument("--prompts", default=str(ROOT / "night" / "data" / "anchor_prompts_v1.jsonl"))
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prompts = [json.loads(line) for line in Path(args.prompts).read_text().splitlines() if line.strip()]
    log_path = out_dir / "teacher-calls.jsonl"
    kept, dropped = [], []
    for p in prompts:
        text, finish, _ = teacher_mod.complete(args.teacher, [{"role": "user", "content": p["prompt"]}], max_tokens=300,
                                               temperature=0.0, log_path=log_path, purpose=f"anchor {p['id']}")
        row = {"item_id": p["id"], "kind": "anchor", "form": "anchor", "user": p["prompt"], "assistant": text.strip(),
               "valid": finish == "stop" and bool(text.strip()), "teacher": args.teacher}
        (kept if row["valid"] else dropped).append(row)
    with open(out_dir / "anchors.jsonl", "w") as f:
        for row in kept:
            f.write(json.dumps(row) + "\n")
    summary = {"teacher": args.teacher, "prompts": len(prompts), "kept": len(kept), "dropped": [r["item_id"] for r in dropped]}
    (out_dir / "anchors-summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
