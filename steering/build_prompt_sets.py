#!/usr/bin/env python3
"""Write the plain prompt sets whose answer-header states extract_states.py reads (detector data for per-prompt
steering). Text is exactly what the night eval sends before the chat template: the SimpleQA gate slice and the
anchor pool (disjoint from the gate) in the gate's prompt form, and one world's never-told and day-1 forward probes
via score_peft.prompt_text. The gate states are for analysis only; a detector is fitted on the anchor pool.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-root", required=True, help="repo root whose eval/ and worlds/ the screen scores with")
    ap.add_argument("--world", default="w1-ines-varga")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    root = Path(args.eval_root)
    sys.path.insert(0, str(root / "eval"))
    import never_told
    import simpleqa_gate
    from score_peft import probes_for_day, prompt_text, read_jsonl

    world = json.loads((root / "worlds" / args.world / "world.json").read_text())
    facts = {f["id"]: f for f in world["facts"]}
    first = world["persona"]["name"].split()[0]
    probes = probes_for_day(read_jsonl(root / "worlds" / args.world / "heldout" / "probes.jsonl"), facts, 1)
    sets = {
        "gate": [{"id": q["id"], "text": simpleqa_gate.PROMPT.format(question=q["question"])} for q in simpleqa_gate.gate_items()],
        "anchors": [{"id": r["id"], "text": r["prompt"]} for r in read_jsonl(HERE.parent / "night" / "data" / "simpleqa_anchor_prompts_v1.jsonl")],
        "never_told": [{"id": it["id"], "text": prompt_text(it, None, first)} for it in never_told.load(args.world)],
        "forward": [{"id": it["id"], "text": prompt_text(it, None, first)} for it in probes if it["kind"] == "forward"],
    }
    gate_ids = {r["id"] for r in sets["gate"]}
    if gate_ids & {r["id"] for r in sets["anchors"]}:
        raise SystemExit("anchor pool overlaps the gate slice")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in sets.items():
        (out / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(json.dumps({k: len(v) for k, v in sets.items()}))


if __name__ == "__main__":
    main()
